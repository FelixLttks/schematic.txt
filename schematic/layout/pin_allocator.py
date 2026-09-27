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
    pin_peer_pts: Dict[Tuple[str, str], List[Point]] = {}

    for net in schematic.nets:
        connected_comp_ids = list({conn.component_id for conn in net.connections})
        for conn in net.connections:
            key = (conn.component_id, conn.pin_name)
            peers = [cid for cid in connected_comp_ids if cid != conn.component_id]
            pin_peer_map.setdefault(key, []).extend(peers)
            if use_geometry:
                for pid in peers:
                    p_comp = schematic.components.get(pid)
                    if p_comp and p_comp.id != conn.component_id:
                        pin_peer_pts.setdefault(key, []).append(p_comp.center)

    # 2. For each component, decide which side each pin should go to
    for comp_id, comp in schematic.components.items():
        left_pins: List[Pin] = []
        right_pins: List[Pin] = []
        top_pins: List[Pin] = []
        bottom_pins: List[Pin] = []

        comp_pin_list = list(comp.pins.values())

        if len(comp_pin_list) == 2 and all(not p.side_specified for p in comp_pin_list):
            _assign_2pin_sides(comp, comp_pin_list[0], comp_pin_list[1], pin_peer_map, schematic.components, use_geometry)
            for pin in comp_pin_list:
                if pin.side == Side.LEFT:
                    left_pins.append(pin)
                elif pin.side == Side.RIGHT:
                    right_pins.append(pin)
                elif pin.side == Side.TOP:
                    top_pins.append(pin)
                else:
                    bottom_pins.append(pin)
        elif comp.kind == "switch" and len(comp_pin_list) == 3 and all(not p.side_specified for p in comp_pin_list):
            _assign_3pin_switch_sides(comp, comp_pin_list, pin_peer_map, schematic.components, use_geometry)
            for pin in comp_pin_list:
                if pin.side == Side.LEFT:
                    left_pins.append(pin)
                elif pin.side == Side.RIGHT:
                    right_pins.append(pin)
                elif pin.side == Side.TOP:
                    top_pins.append(pin)
                else:
                    bottom_pins.append(pin)
        else:
            for pin in comp_pin_list:
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

        # Build pin target Y-map for barycentric crossing minimization
        pin_peer_y: Dict[str, float] = {}
        if use_geometry:
            for pin in comp_pin_list:
                pts = pin_peer_pts.get((comp_id, pin.name), [])
                if pts:
                    pin_peer_y[pin.id] = sum(p.y for p in pts) / len(pts)

        # Sort pins along each side (preserves user-specified order when given, otherwise barycentric)
        _sort_side_pins(left_pins, pin_peer_y)
        _sort_side_pins(right_pins, pin_peer_y)
        _sort_side_pins(top_pins, pin_peer_y)
        _sort_side_pins(bottom_pins, pin_peer_y)

        # Standard symbolic components (resistor, capacitor, switch, fuse, diode, etc.)
        if comp.is_standard:
            is_vert = (len(top_pins) + len(bottom_pins)) > (len(left_pins) + len(right_pins))
            if is_vert:
                comp.width = 40.0
                comp.height = 40.0 if comp.kind == "capacitor" else 60.0
            else:
                comp.width = 40.0 if comp.kind == "capacitor" else 60.0
                comp.height = 40.0
            _calculate_standard_pin_positions(comp, left_pins, right_pins, top_pins, bottom_pins)
            continue

        # Adaptive component dimensions
        num_pins = len(comp.pins)
        if num_pins <= 2:
            min_w = 120.0
            min_h = 60.0
            header_h = 32.0
        elif num_pins <= 4:
            min_w = 140.0
            min_h = 70.0
            header_h = 36.0
        else:
            min_w = MIN_WIDTH
            min_h = MIN_HEIGHT
            header_h = HEADER_HEIGHT

        max_label_left = max((len(p.display_name) for p in left_pins), default=0)
        max_label_right = max((len(p.display_name) for p in right_pins), default=0)
        title_len = max(len(comp.id), len(comp.type_name))

        content_width = (max_label_left + max_label_right) * 7.5 + 44.0
        header_width = title_len * 8.5 + 32.0
        max_tb = max(len(top_pins), len(bottom_pins), 0)
        pin_tb_width = (max_tb + 1) * PIN_PITCH + 40.0

        raw_width = max(min_w, content_width, header_width, pin_tb_width)
        comp.width = float(math.ceil(raw_width / GRID_SIZE) * GRID_SIZE)

        max_lr = max(len(left_pins), len(right_pins), 1)
        raw_height = max(min_h, max_lr * PIN_PITCH + header_h + 20.0)
        comp.height = float(math.ceil(raw_height / GRID_SIZE) * GRID_SIZE)

        # Now compute exact pin coordinates
        _calculate_pin_positions(comp, left_pins, right_pins, top_pins, bottom_pins, header_h)


def _assign_2pin_sides(
    comp: Component,
    p1: Pin,
    p2: Pin,
    pin_peer_map: Dict[Tuple[str, str], List[str]],
    all_comps: Dict[str, Component],
    use_geometry: bool = False,
) -> None:
    """Enforces professional opposite-side allocation for 2-pin passives and components."""
    p1_name, p2_name = p1.name, p2.name
    p1_u, p2_u = p1_name.upper(), p2_name.upper()
    is_pwr_source = any(t in comp.type_name.upper() or t in comp.id.upper() for t in ["BATTERY", "SUPPLY", "CELL"])

    # 1. Power source polarities (e.g. Battery, Supply): POS on RIGHT, NEG on LEFT
    if is_pwr_source:
        if any(t in p1_u for t in ["POS", "+", "VOUT", "OUT"]) or any(t in p2_u for t in ["NEG", "-", "GND"]):
            p1.side, p2.side = Side.RIGHT, Side.LEFT
            return
        if any(t in p2_u for t in ["POS", "+", "VOUT", "OUT"]) or any(t in p1_u for t in ["NEG", "-", "GND"]):
            p1.side, p2.side = Side.LEFT, Side.RIGHT
            return

    # 2. Polarities (IN/VIN/ANODE vs OUT/VOUT/CATHODE)
    if any(t in p1_u for t in ["IN", "VIN", "ANODE"]) or any(t in p2_u for t in ["OUT", "VOUT", "CATHODE"]):
        p1.side, p2.side = Side.LEFT, Side.RIGHT
        return
    if any(t in p2_u for t in ["IN", "VIN", "ANODE"]) or any(t in p1_u for t in ["OUT", "VOUT", "CATHODE"]):
        p1.side, p2.side = Side.RIGHT, Side.LEFT
        return

    # 3. 2D Peer geometry: the pin with peers further to the left gets LEFT
    if use_geometry:
        peers1 = [all_comps[cid] for cid in pin_peer_map.get((comp.id, p1_name), []) if cid in all_comps and cid != comp.id]
        peers2 = [all_comps[cid] for cid in pin_peer_map.get((comp.id, p2_name), []) if cid in all_comps and cid != comp.id]
        dx1 = (sum(c.center.x for c in peers1) / len(peers1) - comp.center.x) if peers1 else None
        dx2 = (sum(c.center.x for c in peers2) / len(peers2) - comp.center.x) if peers2 else None

        if dx1 is not None and dx2 is not None:
            if dx1 < dx2 - 5.0:
                p1.side, p2.side = Side.LEFT, Side.RIGHT
                return
            elif dx2 < dx1 - 5.0:
                p1.side, p2.side = Side.RIGHT, Side.LEFT
                return
        elif dx1 is not None:
            p1.side = Side.LEFT if dx1 < 0 else Side.RIGHT
            p2.side = Side.RIGHT if p1.side == Side.LEFT else Side.LEFT
            return
        elif dx2 is not None:
            p2.side = Side.LEFT if dx2 < 0 else Side.RIGHT
            p1.side = Side.RIGHT if p2.side == Side.LEFT else Side.LEFT
            return

    # 4. Default: first pin LEFT, second pin RIGHT
    p1.side, p2.side = Side.LEFT, Side.RIGHT


def _assign_3pin_switch_sides(
    comp: Component,
    pins: List[Pin],
    pin_peer_map: Dict[Tuple[str, str], List[str]],
    all_comps: Dict[str, Component],
    use_geometry: bool = False,
) -> None:
    """Assigns common terminal to one side and two switch throws to opposite side."""
    common_pin = None
    for p in pins:
        if p.name.upper() in ("2", "COM", "COMMON", "C", "IN"):
            common_pin = p
            break
    if not common_pin:
        common_pin = pins[1] if len(pins) >= 2 else pins[0]

    throws = [p for p in pins if p != common_pin]

    com_peers = pin_peer_map.get((comp.id, common_pin.name), [])
    throw_peers = []
    for t in throws:
        throw_peers.extend(pin_peer_map.get((comp.id, t.name), []))

    if use_geometry:
        com_comps = [all_comps[cid] for cid in com_peers if cid in all_comps and cid != comp.id]
        thr_comps = [all_comps[cid] for cid in throw_peers if cid in all_comps and cid != comp.id]
        com_dx = (sum(c.center.x for c in com_comps) / len(com_comps) - comp.center.x) if com_comps else None
        thr_dx = (sum(c.center.x for c in thr_comps) / len(thr_comps) - comp.center.x) if thr_comps else None

        if com_dx is not None and com_dx > 10.0:
            common_pin.side = Side.RIGHT
            for t in throws:
                t.side = Side.LEFT
            return
        if thr_dx is not None and thr_dx < -10.0:
            common_pin.side = Side.RIGHT
            for t in throws:
                t.side = Side.LEFT
            return

    common_pin.side = Side.LEFT
    for t in throws:
        t.side = Side.RIGHT


def _choose_pin_side(
    comp: Component,
    pin: Pin,
    peers: List[str],
    all_comps: Dict[str, Component],
    use_geometry: bool = False,
) -> Side:
    """Chooses LEFT or RIGHT side for the pin based on 2D peer placement and conventions."""
    p_name = pin.name.upper()
    p_label = pin.label.upper()

    # When 2D geometry is known, prefer facing the actual connected peer
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

    # Unconnected pin: match dominant side of other pins if any
    if not peers:
        other_sides = [p.side for p in comp.pins.values() if p.name != pin.name and p.side_specified]
        if other_sides:
            return other_sides[0]

    # Conventional naming
    if any(w in p_name or w in p_label for w in ["VCC", "3V3", "5V", "VIN", "VDD", "IN", "RX", "SCL", "CLK", "ANODE"]):
        return Side.LEFT
    if any(w in p_name or w in p_label for w in ["GND", "VSS", "OUT", "TX", "SDA", "MOSI", "CS", "CATHODE", "POS"]):
        return Side.RIGHT

    return Side.LEFT


def _sort_side_pins(pins: List[Pin], pin_peer_y: Optional[Dict[str, float]] = None) -> None:
    """Sorts pins cleanly along a side. Preserves user-specified order when given, else barycentric."""
    def sort_key(pin: Pin) -> Tuple[int, float]:
        if pin.side_specified:
            return (0, float(pin.order))
        if pin_peer_y and pin.id in pin_peer_y:
            return (1, pin_peer_y[pin.id])
        p_name = pin.name.upper()
        p_label = pin.label.upper()
        if any(w in p_name or w in p_label for w in ["VCC", "3V3", "5V", "VIN", "VDD"]):
            return (2, 0.0)
        if "GND" in p_name or "GND" in p_label or "VSS" in p_name:
            return (2, 100.0)
        if any(w in p_name or w in p_label for w in ["SCL", "SCK", "CLK"]):
            return (2, 20.0)
        if any(w in p_name or w in p_label for w in ["SDA", "MOSI", "MISO", "TX", "RX"]):
            return (2, 30.0)
        return (2, 50.0)

    pins.sort(key=sort_key)


def _calculate_pin_positions(
    comp: Component,
    left_pins: List[Pin],
    right_pins: List[Pin],
    top_pins: List[Pin],
    bottom_pins: List[Pin],
    header_h: float = HEADER_HEIGHT,
) -> None:
    """Assigns lead_start and terminal position for each pin snapped strictly to grid."""
    start_y = round((comp.y + header_h + 20.0) / GRID_SIZE) * GRID_SIZE

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


def _calculate_standard_pin_positions(
    comp: Component,
    left_pins: List[Pin],
    right_pins: List[Pin],
    top_pins: List[Pin],
    bottom_pins: List[Pin],
) -> None:
    """Calculates pin positions for standard symbolic components strictly snapped to grid."""
    is_vertical = (len(top_pins) + len(bottom_pins)) > (len(left_pins) + len(right_pins))

    if is_vertical:
        cx = round((comp.x + comp.width / 2.0) / GRID_SIZE) * GRID_SIZE
        top_y = round(comp.y / GRID_SIZE) * GRID_SIZE
        bot_y = round((comp.y + comp.height) / GRID_SIZE) * GRID_SIZE
        if top_pins:
            for p in top_pins:
                p.lead_start = Point(cx, top_y)
                p.position = Point(cx, top_y)
        if bottom_pins:
            for p in bottom_pins:
                p.lead_start = Point(cx, bot_y)
                p.position = Point(cx, bot_y)
        return

    # Horizontal orientation
    cy = round((comp.y + comp.height / 2.0) / GRID_SIZE) * GRID_SIZE
    left_x = round(comp.x / GRID_SIZE) * GRID_SIZE
    right_x = round((comp.x + comp.width) / GRID_SIZE) * GRID_SIZE

    # SPDT / 3-terminal switch special pin placement
    if comp.kind == "switch" and len(comp.pins) == 3:
        top_y = round(comp.y / GRID_SIZE) * GRID_SIZE
        bot_y = round((comp.y + comp.height) / GRID_SIZE) * GRID_SIZE
        if len(left_pins) == 1 and len(right_pins) == 2:
            left_pins[0].lead_start = Point(left_x, cy)
            left_pins[0].position = Point(left_x, cy)
            # Sort right pins: throw 1 on top, throw 2 on bottom
            right_pins[0].lead_start = Point(right_x, top_y)
            right_pins[0].position = Point(right_x, top_y)
            right_pins[1].lead_start = Point(right_x, bot_y)
            right_pins[1].position = Point(right_x, bot_y)
            return
        elif len(right_pins) == 1 and len(left_pins) == 2:
            right_pins[0].lead_start = Point(right_x, cy)
            right_pins[0].position = Point(right_x, cy)
            left_pins[0].lead_start = Point(left_x, top_y)
            left_pins[0].position = Point(left_x, top_y)
            left_pins[1].lead_start = Point(left_x, bot_y)
            left_pins[1].position = Point(left_x, bot_y)
            return

    # Standard 2-pin passives: 1 pin left, 1 pin right
    if len(left_pins) == 1:
        left_pins[0].lead_start = Point(left_x, cy)
        left_pins[0].position = Point(left_x, cy)
    else:
        for i, p in enumerate(left_pins):
            py = cy + (i - len(left_pins) / 2.0 + 0.5) * PIN_PITCH
            py_snap = round(py / GRID_SIZE) * GRID_SIZE
            p.lead_start = Point(left_x, py_snap)
            p.position = Point(left_x, py_snap)

    if len(right_pins) == 1:
        right_pins[0].lead_start = Point(right_x, cy)
        right_pins[0].position = Point(right_x, cy)
    else:
        for i, p in enumerate(right_pins):
            py = cy + (i - len(right_pins) / 2.0 + 0.5) * PIN_PITCH
            py_snap = round(py / GRID_SIZE) * GRID_SIZE
            p.lead_start = Point(right_x, py_snap)
            p.position = Point(right_x, py_snap)
