"""Pin allocation and component sizing based on 2D peer orientation and electrical conventions."""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Pin, Point, Side
from schematic.semantics import is_ground, is_power

GRID_SIZE = 20.0
PIN_PITCH = 20.0
PIN_LEAD_LEN = 20.0
HEADER_HEIGHT = 40.0
MIN_WIDTH = 140.0
MIN_HEIGHT = 80.0


def allocate_pins_and_dimensions(
    schematic, use_geometry: bool = False, redistribute_sides: bool = True
) -> None:
    """Assign sides (LEFT, RIGHT, TOP, BOTTOM) and coordinates to all pins.

    With ``redistribute_sides=False`` the current side of every pin is kept
    and only sizes/coordinates are recomputed.  The placement engine uses
    this to freeze orientation decisions (it controls them explicitly through
    mirror moves) instead of letting side proposals flip underneath it.
    Strictly aligns all dimensions and coordinates to GRID_SIZE (20.0).
    """
    # 1. Build connection map: for each (comp_id, pin_name), which other components does it connect to?
    pin_peer_map: Dict[Tuple[str, str], List[str]] = {}
    pin_peer_pts: Dict[Tuple[str, str], List[Point]] = {}
    pin_bundle_rank: Dict[Tuple[str, str], Tuple[Tuple[str, ...], int]] = {}

    # Nets connected to the same component set form a bundle structurally.
    # Give each member a common rank so the matched terminals occupy the same
    # relative rows at every endpoint, regardless of pin names or signal type.
    bundle_groups: Dict[Tuple[str, ...], List[Net]] = {}
    for net in schematic.nets:
        if is_ground(net.name):
            continue
        if len(net.connections) >= 3 and is_power(net.name):
            continue
        endpoints = tuple(sorted({conn.component_id for conn in net.connections}))
        if len(endpoints) >= 2:
            bundle_groups.setdefault(endpoints, []).append(net)
    for endpoints, nets in bundle_groups.items():
        if len(nets) < 2:
            continue
        for rank, net in enumerate(sorted(nets, key=lambda item: item.name)):
            for conn in net.connections:
                key = (conn.component_id, conn.pin_id or conn.pin_name)
                pin_bundle_rank.setdefault(key, (endpoints, rank))

    for net in schematic.nets:
        connected_comp_ids = sorted({conn.component_id for conn in net.connections})
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

        # One uniform rule for every component: two-pin bodies get opposite
        # sides (symbol convention), every other pin faces the side proposed
        # by geometry or naming conventions.  When sides are frozen, only the
        # engine's mirror moves flip them.
        if redistribute_sides and len(comp_pin_list) == 2 and all(not p.side_specified for p in comp_pin_list):
            _assign_2pin_sides(comp_pin_list[0], comp_pin_list[1])
        elif redistribute_sides:
            for pin in comp_pin_list:
                if not pin.side_specified:
                    peers = pin_peer_map.get((comp_id, pin.name), [])
                    pin.side = _choose_pin_side(comp, pin, peers, schematic.components, use_geometry)

        for pin in comp_pin_list:
            if pin.side == Side.LEFT:
                left_pins.append(pin)
            elif pin.side == Side.RIGHT:
                right_pins.append(pin)
            elif pin.side == Side.TOP:
                top_pins.append(pin)
            else:
                bottom_pins.append(pin)

        # Build pin target Y-map for barycentric crossing minimization
        pin_peer_y: Dict[str, float] = {}
        if use_geometry:
            for pin in comp_pin_list:
                pts = pin_peer_pts.get((comp_id, pin.id), pin_peer_pts.get((comp_id, pin.name), []))
                if pts:
                    pin_peer_y[pin.id] = sum(p.y for p in pts) / len(pts)

        # Sort pins along each side (preserves user-specified order when given, otherwise barycentric)
        _sort_side_pins(left_pins, pin_peer_y)
        _sort_side_pins(right_pins, pin_peer_y)
        _sort_side_pins(top_pins, pin_peer_y)
        _sort_side_pins(bottom_pins, pin_peer_y)
        for side_pins in (left_pins, right_pins, top_pins, bottom_pins):
            _align_bundle_slots(side_pins, comp_id, pin_bundle_rank)

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


def _assign_2pin_sides(p1: Pin, p2: Pin) -> None:
    """Give a two-pin body opposite sides (symbol convention, not per kind).

    Polarity names only decide which terminal starts on the left; the
    placement engine may mirror the body afterwards, so this is a starting
    proposal rather than a fate.
    """
    p1_u, p2_u = p1.name.upper(), p2.name.upper()
    p1_is_in = any(t in p1_u for t in ["IN", "ANODE", "NEG", "-"])
    p2_is_in = any(t in p2_u for t in ["IN", "ANODE", "NEG", "-"])
    p1_is_out = any(t in p1_u for t in ["OUT", "CATHODE", "POS", "+"])
    p2_is_out = any(t in p2_u for t in ["OUT", "CATHODE", "POS", "+"])

    if (p2_is_in and not p1_is_in) or (p1_is_out and not p2_is_out):
        p1.side, p2.side = Side.RIGHT, Side.LEFT
    else:
        p1.side, p2.side = Side.LEFT, Side.RIGHT


def mirror_component(comp: Component) -> bool:
    """Swap the sides of every pin of a component (horizontal flip).

    Returns False when the source explicitly specified pin sides: those keep
    their user-given orientation.  This is the single orientation primitive
    for every component type, symbolic or not.
    """
    if any(pin.side_specified for pin in comp.pins.values()):
        return False
    for pin in comp.pins.values():
        if pin.side == Side.LEFT:
            pin.side = Side.RIGHT
        elif pin.side == Side.RIGHT:
            pin.side = Side.LEFT
        elif pin.side == Side.TOP:
            pin.side = Side.BOTTOM
        elif pin.side == Side.BOTTOM:
            pin.side = Side.TOP
    return True


def resolve_equivalent_pins(schematic) -> None:
    """Bind connections to the nearest of several equivalent component pins.

    Components may expose the same pin name twice (for example two GND pads).
    The connection is then assigned to the geometrically nearest instance,
    evaluated against the centres of its peer components in the net.  This is
    pure geometry; no pin name, net name or component kind is involved.
    """
    for net in schematic.nets:
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            if not comp:
                continue
            matching = comp.get_pins_by_name(conn.pin_name)
            if len(matching) <= 1:
                if matching:
                    conn.pin_id = matching[0].id
                continue
            peer_points = []
            for peer_id in conn.peer_component_ids:
                peer = schematic.components.get(peer_id)
                if peer and peer.id != comp.id:
                    peer_points.append(peer.center)
            if not peer_points:
                peer_points = [
                    schematic.components[c.component_id].center
                    for c in net.connections
                    if c.component_id != comp.id and c.component_id in schematic.components
                ]
            if peer_points:
                pin = min(matching, key=lambda p: min(p.position.manhattan_to(q) for q in peer_points))
            else:
                pin = matching[0]
            conn.pin_id = pin.id


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


def _align_bundle_slots(
    pins: List[Pin],
    comp_id: str,
    bundle_rank: Dict[Tuple[str, str], Tuple[Tuple[str, ...], int]],
) -> None:
    """Reorder only bus-member slots, preserving the surrounding pin layout."""
    grouped: Dict[Tuple[str, ...], List[Tuple[int, Pin, int]]] = {}
    for index, pin in enumerate(pins):
        rank_info = bundle_rank.get((comp_id, pin.id)) or bundle_rank.get((comp_id, pin.name))
        if rank_info:
            endpoints, rank = rank_info
            grouped.setdefault(endpoints, []).append((index, pin, rank))
    for members in grouped.values():
        if len(members) < 2:
            continue
        slots = sorted(index for index, _, _ in members)
        ordered = [pin for _, pin, _ in sorted(members, key=lambda item: (item[2], item[1].id))]
        for index, pin in zip(slots, ordered):
            pins[index] = pin


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
