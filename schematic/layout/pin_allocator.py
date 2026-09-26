"""Pin allocation and component sizing based on 2D peer orientation and electrical conventions."""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Pin, Point, Side

PIN_PITCH = 24.0
PIN_LEAD_LEN = 16.0
HEADER_HEIGHT = 34.0
MIN_WIDTH = 140.0
MIN_HEIGHT = 80.0


def allocate_pins_and_dimensions(schematic) -> None:
    """
    Assigns sides (LEFT, RIGHT) and layout coordinates
    to all pins of all components in the schematic.
    Uses horizontal pin leads for standard clean schematic presentation.
    """
    # 1. First build connection map: for each (comp_id, pin_name), which other components does it connect to?
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

        for pin_name, pin in comp.pins.items():
            peers = pin_peer_map.get((comp_id, pin_name), [])
            side = _choose_pin_side(comp, pin, peers, schematic.components)
            pin.side = side
            if side == Side.LEFT:
                left_pins.append(pin)
            else:
                right_pins.append(pin)

        # Balance sides if one side is overloaded and the other is empty
        if not left_pins and len(right_pins) > 2:
            half = len(right_pins) // 2
            left_pins = right_pins[:half]
            right_pins = right_pins[half:]
            for p in left_pins:
                p.side = Side.LEFT
        elif not right_pins and len(left_pins) > 2:
            half = len(left_pins) // 2
            right_pins = left_pins[half:]
            left_pins = left_pins[:half]
            for p in right_pins:
                p.side = Side.RIGHT

        # Sort pins along each side
        _sort_side_pins(left_pins)
        _sort_side_pins(right_pins)

        # Compute component width and height based on pin counts and text labels
        max_label_left = max((len(p.display_name) for p in left_pins), default=0)
        max_label_right = max((len(p.display_name) for p in right_pins), default=0)
        title_len = max(len(comp.id), len(comp.type_name))

        # Approx font width = 7.5px
        content_width = (max_label_left + max_label_right) * 7.5 + 44.0
        header_width = title_len * 8.5 + 32.0

        comp.width = max(MIN_WIDTH, content_width, header_width)
        max_pins_on_side = max(len(left_pins), len(right_pins), 1)
        comp.height = max(MIN_HEIGHT, max_pins_on_side * PIN_PITCH + HEADER_HEIGHT + 20.0)

        # Now compute exact pin coordinates
        _calculate_pin_positions(comp, left_pins, right_pins)


def _choose_pin_side(comp: Component, pin: Pin, peers: List[str], all_comps: Dict[str, Component]) -> Side:
    """Chooses LEFT or RIGHT side for the pin based on peer placement and signal role."""
    # 1. Peer orientation preference
    if peers:
        dx_sum = 0.0
        for peer_id in peers:
            peer = all_comps.get(peer_id)
            if peer:
                dx_sum += peer.center.x - comp.center.x

        if dx_sum < -20.0:
            return Side.LEFT
        elif dx_sum > 20.0:
            return Side.RIGHT

    # 2. Convention fallback: Power/Inputs on LEFT, GND/Outputs on RIGHT
    p_name = pin.name.upper()
    p_label = pin.label.upper()

    if any(w in p_name or w in p_label for w in ["VCC", "3V3", "5V", "VIN", "VDD", "IN", "RX", "SCL", "CLK"]):
        return Side.LEFT
    if any(w in p_name or w in p_label for w in ["GND", "VSS", "OUT", "TX", "SDA", "MOSI", "CS"]):
        return Side.RIGHT

    return Side.LEFT


def _sort_side_pins(pins: List[Pin]) -> None:
    """Sorts pins so power/VCC is at top and GND is at bottom."""
    def sort_key(pin: Pin) -> int:
        p_name = pin.name.upper()
        p_label = pin.label.upper()
        # Top: Power
        if any(w in p_name or w in p_label for w in ["VCC", "3V3", "5V", "VIN", "VDD"]):
            return 0
        # Bottom: GND
        if "GND" in p_name or "GND" in p_label or "VSS" in p_name:
            return 100
        # Clocks
        if any(w in p_name or w in p_label for w in ["SCL", "SCK", "CLK"]):
            return 20
        # Data
        if any(w in p_name or w in p_label for w in ["SDA", "MOSI", "MISO", "TX", "RX"]):
            return 30
        return 50

    pins.sort(key=sort_key)


def _calculate_pin_positions(comp: Component, left_pins: List[Pin], right_pins: List[Pin]) -> None:
    """Assigns lead_start and terminal position for each pin."""
    start_y = comp.y + HEADER_HEIGHT + 16.0

    # LEFT pins
    for i, pin in enumerate(left_pins):
        py = start_y + i * PIN_PITCH
        pin.lead_start = Point(comp.x, py)
        pin.position = Point(comp.x - PIN_LEAD_LEN, py)

    # RIGHT pins
    for i, pin in enumerate(right_pins):
        py = start_y + i * PIN_PITCH
        pin.lead_start = Point(comp.x + comp.width, py)
        pin.position = Point(comp.x + comp.width + PIN_LEAD_LEN, py)
