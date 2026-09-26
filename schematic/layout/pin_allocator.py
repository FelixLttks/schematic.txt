"""Pin allocation and component sizing based on 2D peer orientation and electrical conventions."""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Pin, Point, Side

GRID_SIZE = 20.0
PIN_PITCH = 20.0
PIN_LEAD_LEN = 20.0
HEADER_HEIGHT = 40.0
MIN_WIDTH = 140.0
MIN_HEIGHT = 80.0


def allocate_pins_and_dimensions(schematic, use_geometry: bool = False) -> None:
    """
    Assigns sides (LEFT, RIGHT, TOP, BOTTOM) and layout coordinates
    to all pins of all components in the schematic.
    Strictly aligns all dimensions and coordinates to GRID_SIZE (20.0).
    """
    # 1. Build connection map: for each (comp_id, pin_name), which other components does it connect to?
    pin_peer_map: Dict[Tuple[str, str], List[str]] = {}
    for net in schematic.nets:
        connected_comp_ids = list({conn.component_id for conn in net.connections})
        for conn in net.connections:
            key = (conn.component_id, conn.pin_name)
            peers = [cid for cid in connected_comp_ids if cid != conn.component_id]
            pin_peer_map.setdefault(key, []).extend(peers)

    # 2. For each component, decide which side each pin should go to
    for comp_id, comp in schematic.components.items():
        left_pins: List[Pin] = []
        right_pins: List[Pin] = []
        top_pins: List[Pin] = []
        bottom_pins: List[Pin] = []

        for pin in comp.pins.values():
            if pin.side_specified:
                side = pin.side
            else:
                peers = pin_peer_map.get((comp_id, pin.name), [])
                side = _choose_pin_side(comp, pin, peers, schematic.components, use_geometry)
                pin.side = side
            if side == Side.LEFT:
                left_pins.append(pin)
            elif side == Side.RIGHT:
                right_pins.append(pin)
            elif side == Side.TOP:
                top_pins.append(pin)
            else:
                bottom_pins.append(pin)

        # Sort pins along each side (preserves user-specified order when given)
        _sort_side_pins(left_pins)
        _sort_side_pins(right_pins)
        _sort_side_pins(top_pins)
        _sort_side_pins(bottom_pins)

        # Compute component width and height based on pin counts and text labels
        max_label_left = max((len(p.display_name) for p in left_pins), default=0)
        max_label_right = max((len(p.display_name) for p in right_pins), default=0)
        title_len = max(len(comp.id), len(comp.type_name))

        content_width = (max_label_left + max_label_right) * 7.5 + 44.0
        header_width = title_len * 8.5 + 32.0
        max_tb = max(len(top_pins), len(bottom_pins), 0)
        pin_tb_width = (max_tb + 1) * PIN_PITCH + 40.0

        raw_width = max(MIN_WIDTH, content_width, header_width, pin_tb_width)
        comp.width = float(math.ceil(raw_width / GRID_SIZE) * GRID_SIZE)

        max_lr = max(len(left_pins), len(right_pins), 1)
        raw_height = max(MIN_HEIGHT, max_lr * PIN_PITCH + HEADER_HEIGHT + 20.0)
        comp.height = float(math.ceil(raw_height / GRID_SIZE) * GRID_SIZE)

        # Now compute exact pin coordinates
        _calculate_pin_positions(comp, left_pins, right_pins, top_pins, bottom_pins)


def _choose_pin_side(
    comp: Component,
    pin: Pin,
    peers: List[str],
    all_comps: Dict[str, Component],
    use_geometry: bool = False,
) -> Side:
    """Chooses LEFT or RIGHT side for the pin based on 2D peer placement and conventions."""
    if use_geometry and peers:
        valid_peers = [all_comps[pid] for pid in peers if pid in all_comps and pid != comp.id]
        if valid_peers:
            avg_px = sum(p.center.x for p in valid_peers) / len(valid_peers)
            dx = avg_px - comp.center.x
            if abs(dx) > 10.0:
                return Side.LEFT if dx < 0 else Side.RIGHT

    # Pre-geometric heuristics
    if peers:
        valid_peers = [all_comps[pid] for pid in peers if pid in all_comps and pid != comp.id]
        if valid_peers:
            dx_sum = sum(p.center.x - comp.center.x for p in valid_peers)
            if dx_sum < -10.0:
                return Side.LEFT
            elif dx_sum > 10.0:
                return Side.RIGHT

    p_name = pin.name.upper()
    p_label = pin.label.upper()

    if any(w in p_name or w in p_label for w in ["VCC", "3V3", "5V", "VIN", "VDD", "IN", "RX", "SCL", "CLK", "ANODE"]):
        return Side.LEFT
    if any(w in p_name or w in p_label for w in ["GND", "VSS", "OUT", "TX", "SDA", "MOSI", "CS", "CATHODE"]):
        return Side.RIGHT

    return Side.LEFT


def _sort_side_pins(pins: List[Pin]) -> None:
    """Sorts pins cleanly along a side. Preserves user-specified order when given."""
    def sort_key(pin: Pin) -> Tuple[int, int]:
        if pin.side_specified:
            return (0, pin.order)
        p_name = pin.name.upper()
        p_label = pin.label.upper()
        if any(w in p_name or w in p_label for w in ["VCC", "3V3", "5V", "VIN", "VDD"]):
            return (1, 0)
        if "GND" in p_name or "GND" in p_label or "VSS" in p_name:
            return (1, 100)
        if any(w in p_name or w in p_label for w in ["SCL", "SCK", "CLK"]):
            return (1, 20)
        if any(w in p_name or w in p_label for w in ["SDA", "MOSI", "MISO", "TX", "RX"]):
            return (1, 30)
        return (1, 50)

    pins.sort(key=sort_key)


def _calculate_pin_positions(
    comp: Component,
    left_pins: List[Pin],
    right_pins: List[Pin],
    top_pins: List[Pin],
    bottom_pins: List[Pin],
) -> None:
    """Assigns lead_start and terminal position for each pin snapped strictly to grid."""
    start_y = round((comp.y + HEADER_HEIGHT + 20.0) / GRID_SIZE) * GRID_SIZE

    # LEFT pins
    for i, pin in enumerate(left_pins):
        py = start_y + i * PIN_PITCH
        pin.lead_start = Point(round(comp.x / GRID_SIZE) * GRID_SIZE, py)
        pin.position = Point(pin.lead_start.x - PIN_LEAD_LEN, py)

    # RIGHT pins
    for i, pin in enumerate(right_pins):
        py = start_y + i * PIN_PITCH
        pin.lead_start = Point(round((comp.x + comp.width) / GRID_SIZE) * GRID_SIZE, py)
        pin.position = Point(pin.lead_start.x + PIN_LEAD_LEN, py)

    # TOP pins
    start_x = round((comp.x + 40.0) / GRID_SIZE) * GRID_SIZE
    for i, pin in enumerate(top_pins):
        px = start_x + i * PIN_PITCH
        pin.lead_start = Point(px, round(comp.y / GRID_SIZE) * GRID_SIZE)
        pin.position = Point(px, pin.lead_start.y - PIN_LEAD_LEN)

    # BOTTOM pins
    for i, pin in enumerate(bottom_pins):
        px = start_x + i * PIN_PITCH
        pin.lead_start = Point(px, round((comp.y + comp.height) / GRID_SIZE) * GRID_SIZE)
        pin.position = Point(px, pin.lead_start.y + PIN_LEAD_LEN)
