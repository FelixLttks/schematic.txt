"""Pin side / order assignment and component sizing.

A component's shape is a function of its pins: which side each pin sits on,
in which order, and how long the labels are.  User-declared sides and orders
(``left: ...`` / ``right: ...``) are hard constraints and are never changed.
Free pins are assigned by the placer from geometry: every free pin faces the
components it connects to, and pins on one side are sorted by the position of
their peers so that bundles leave a component without crossing each other.

Standard symbols (resistor, capacitor, ...) keep their fixed sizes:
60x40 horizontal / 40x60 vertical (40x40 for capacitors).
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

from schematic.models import Component, Pin, Point, Side

GRID_SIZE = 20.0
PIN_PITCH = 20.0
PIN_LEAD_LEN = 20.0
HEADER_HEIGHT = 40.0
MIN_WIDTH = 140.0
MIN_HEIGHT = 80.0

SIDES = (Side.LEFT, Side.RIGHT, Side.TOP, Side.BOTTOM)
OUTWARD: Dict[Side, Tuple[int, int]] = {
    Side.LEFT: (-1, 0),
    Side.RIGHT: (1, 0),
    Side.TOP: (0, -1),
    Side.BOTTOM: (0, 1),
}
OPPOSITE: Dict[Side, Side] = {
    Side.LEFT: Side.RIGHT,
    Side.RIGHT: Side.LEFT,
    Side.TOP: Side.BOTTOM,
    Side.BOTTOM: Side.TOP,
}

_POWER_WORDS = ("VCC", "3V3", "5V", "VIN", "VDD", "VBUS", "VBAT", "+")
_GROUND_WORDS = ("GND", "VSS")


def snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


def conventional_rank(pin: Pin) -> float:
    """Fallback ordering: supply pins first, ground last, clocks before data."""
    name = pin.name.upper()
    label = pin.label.upper()
    if any(w in name or w in label for w in _GROUND_WORDS):
        return 100.0
    if any(w in name or w in label for w in _POWER_WORDS):
        return 0.0
    if any(w in name or w in label for w in ("SCL", "SCK", "CLK")):
        return 20.0
    if any(w in name or w in label for w in ("SDA", "MOSI", "MISO", "TX", "RX")):
        return 30.0
    return 50.0


def pins_on(comp: Component, side: Side) -> List[Pin]:
    pins = [p for p in comp.pins.values() if p.side == side]
    pins.sort(key=lambda p: p.order)
    return pins


def is_vertical_symbol(comp: Component) -> bool:
    tb = sum(1 for p in comp.pins.values() if p.side in (Side.TOP, Side.BOTTOM))
    lr = sum(1 for p in comp.pins.values() if p.side in (Side.LEFT, Side.RIGHT))
    return tb > lr


def apply_order(comp: Component, key: Callable[[Pin], float]) -> None:
    """Re-numbers ``pin.order`` per side; declared orders are kept as they are."""
    if comp.image:
        return
    for side in SIDES:
        pins = [p for p in comp.pins.values() if p.side == side]
        declared = [p for p in pins if p.side_specified]
        free = [p for p in pins if not p.side_specified]
        declared.sort(key=lambda p: p.order)
        free.sort(key=lambda p: (key(p), conventional_rank(p), p.name, p.id))
        for i, p in enumerate(declared + free):
            p.order = i


def size_and_place_pins(comp: Component) -> None:
    """Computes the component size and every pin's coordinates from the pins'
    sides and orders, relative to the current ``comp.x`` / ``comp.y``."""
    if comp.image:
        _place_custom_pins(comp)
        return

    left, right = pins_on(comp, Side.LEFT), pins_on(comp, Side.RIGHT)
    top, bottom = pins_on(comp, Side.TOP), pins_on(comp, Side.BOTTOM)

    if comp.is_standard:
        vertical = (len(top) + len(bottom)) > (len(left) + len(right))
        if vertical:
            comp.width = 40.0
            comp.height = 40.0 if comp.kind == "capacitor" else 60.0
        else:
            comp.width = 40.0 if comp.kind == "capacitor" else 60.0
            comp.height = 40.0
        _place_standard_pins(comp, left, right, top, bottom)
        return

    num_pins = len(comp.pins)
    if num_pins <= 2:
        min_w, min_h, header_h = 120.0, 60.0, 32.0
    elif num_pins <= 4:
        min_w, min_h, header_h = 140.0, 70.0, 36.0
    else:
        min_w, min_h, header_h = MIN_WIDTH, MIN_HEIGHT, HEADER_HEIGHT

    max_label_left = max((len(p.display_name) for p in left), default=0)
    max_label_right = max((len(p.display_name) for p in right), default=0)
    title_len = max(len(comp.id), len(comp.type_name))
    content_width = (max_label_left + max_label_right) * 7.5 + 44.0
    header_width = title_len * 8.5 + 32.0
    max_tb = max(len(top), len(bottom), 0)
    pin_tb_width = (max_tb + 1) * PIN_PITCH + 40.0
    comp.width = float(math.ceil(max(min_w, content_width, header_width, pin_tb_width) / GRID_SIZE) * GRID_SIZE)

    max_lr = max(len(left), len(right), 1)
    raw_height = max(min_h, max_lr * PIN_PITCH + header_h + 20.0)
    comp.height = float(math.ceil(raw_height / GRID_SIZE) * GRID_SIZE)

    start_y = snap(comp.y + header_h + 20.0)
    for i, pin in enumerate(left):
        py = start_y + i * PIN_PITCH
        pin.lead_start = Point(snap(comp.x), py)
        pin.position = Point(pin.lead_start.x - PIN_LEAD_LEN, py)
    for i, pin in enumerate(right):
        py = start_y + i * PIN_PITCH
        pin.lead_start = Point(snap(comp.x + comp.width), py)
        pin.position = Point(pin.lead_start.x + PIN_LEAD_LEN, py)
    start_x = snap(comp.x + 40.0)
    for i, pin in enumerate(top):
        px = start_x + i * PIN_PITCH
        pin.lead_start = Point(px, snap(comp.y))
        pin.position = Point(px, pin.lead_start.y - PIN_LEAD_LEN)
    for i, pin in enumerate(bottom):
        px = start_x + i * PIN_PITCH
        pin.lead_start = Point(px, snap(comp.y + comp.height))
        pin.position = Point(px, pin.lead_start.y + PIN_LEAD_LEN)


def _place_custom_pins(comp: Component) -> None:
    gw = comp.grid_width if comp.grid_width > 0 else 6
    gh = comp.grid_height if comp.grid_height > 0 else 6
    comp.width = float(snap(gw * GRID_SIZE))
    comp.height = float(snap(gh * GRID_SIZE))

    for pin in comp.pins.values():
        if pin.grid_x is not None and pin.grid_y is not None:
            gx, gy = pin.grid_x, pin.grid_y
            px = snap(comp.x + gx * GRID_SIZE)
            py = snap(comp.y + gy * GRID_SIZE)
            pin.lead_start = Point(px, py)

            if not pin.side_specified:
                dist_left = gx
                dist_right = gw - gx
                dist_top = gy
                dist_bottom = gh - gy
                min_d = min(dist_left, dist_right, dist_top, dist_bottom)
                if min_d == dist_left:
                    pin.side = Side.LEFT
                elif min_d == dist_right:
                    pin.side = Side.RIGHT
                elif min_d == dist_top:
                    pin.side = Side.TOP
                else:
                    pin.side = Side.BOTTOM

            if pin.side == Side.LEFT:
                pin.position = Point(snap(comp.x - PIN_LEAD_LEN), py)
            elif pin.side == Side.RIGHT:
                pin.position = Point(snap(comp.x + comp.width + PIN_LEAD_LEN), py)
            elif pin.side == Side.TOP:
                pin.position = Point(px, snap(comp.y - PIN_LEAD_LEN))
            else:
                pin.position = Point(px, snap(comp.y + comp.height + PIN_LEAD_LEN))
        else:
            # Fallback for pins without explicit coordinates
            pin.lead_start = Point(snap(comp.x), snap(comp.y + 20.0))
            pin.position = Point(snap(comp.x - PIN_LEAD_LEN), snap(comp.y + 20.0))
            pin.side = Side.LEFT


def _place_standard_pins(
    comp: Component, left: List[Pin], right: List[Pin], top: List[Pin], bottom: List[Pin]
) -> None:
    if (len(top) + len(bottom)) > (len(left) + len(right)):
        cx = snap(comp.x + comp.width / 2.0)
        for pins, y in ((top, snap(comp.y)), (bottom, snap(comp.y + comp.height))):
            for i, p in enumerate(pins):
                px = cx + (i - (len(pins) - 1) / 2.0) * 2 * PIN_PITCH if len(pins) > 1 else cx
                p.lead_start = Point(snap(px), y)
                p.position = Point(snap(px), y)
        return

    cy = snap(comp.y + comp.height / 2.0)
    left_x, right_x = snap(comp.x), snap(comp.x + comp.width)
    top_y, bot_y = snap(comp.y), snap(comp.y + comp.height)
    if comp.kind == "switch" and len(comp.pins) == 3:
        if len(left) == 1 and len(right) == 2:
            _set(left[0], left_x, cy)
            _set(right[0], right_x, top_y)
            _set(right[1], right_x, bot_y)
            return
        if len(right) == 1 and len(left) == 2:
            _set(right[0], right_x, cy)
            _set(left[0], left_x, top_y)
            _set(left[1], left_x, bot_y)
            return
    for pins, x in ((left, left_x), (right, right_x)):
        if len(pins) == 1:
            _set(pins[0], x, cy)
        else:
            for i, p in enumerate(pins):
                _set(p, x, snap(cy + (i - len(pins) / 2.0 + 0.5) * PIN_PITCH))


def _set(pin: Pin, x: float, y: float) -> None:
    pin.lead_start = Point(x, y)
    pin.position = Point(x, y)


def orient_two_pin(comp: Component, first: Pin, first_side: Side) -> None:
    """Puts ``first`` on ``first_side`` and the other free pin opposite to it."""
    if any(p.side_specified for p in comp.pins.values()):
        return
    first.side = first_side
    for p in comp.pins.values():
        if p is not first:
            p.side = OPPOSITE[first_side]
    first.order = 0
    for p in comp.pins.values():
        if p is not first:
            p.order = 0


def orient_switch(comp: Component, common_side: Side) -> None:
    """3-pin switch: common terminal on one side, the two throws opposite."""
    if any(p.side_specified for p in comp.pins.values()):
        return
    pins = list(comp.pins.values())
    common = switch_common(comp)
    for p in pins:
        p.side = common_side if p is common else OPPOSITE[common_side]
    throws = [p for p in pins if p is not common]
    for i, p in enumerate(throws):
        p.order = i
    common.order = 0


def switch_common(comp: Component) -> Pin:
    pins = list(comp.pins.values())
    for p in pins:
        if p.name.upper() in ("2", "COM", "COMMON", "C", "IN"):
            return p
    return pins[1] if len(pins) >= 2 else pins[0]


def default_sides(comp: Component) -> None:
    """Geometry-free side assignment used before anything is placed."""
    free = [p for p in comp.pins.values() if not p.side_specified]
    if not free:
        return
    if comp.is_standard and len(comp.pins) == 2 and len(free) == 2:
        a, b = free
        # Inputs / anodes on the left, otherwise declaration order.
        b_first = b.name.upper() in ("IN", "A", "ANODE") and a.name.upper() not in ("IN", "A", "ANODE")
        orient_two_pin(comp, b if b_first else a, Side.LEFT)
        return
    if comp.kind == "switch" and len(comp.pins) == 3 and len(free) == 3:
        orient_switch(comp, Side.LEFT)
        return
    declared_sides = [p.side for p in comp.pins.values() if p.side_specified]
    for p in free:
        p.side = declared_sides[0] if declared_sides else Side.LEFT
    apply_order(comp, lambda p: 0.0)


def allocate_pins_and_dimensions(schematic, use_geometry: bool = False) -> None:
    """Compatibility entry point: sizes every component from its current pins.

    Free pins get geometry-free default sides unless ``use_geometry`` is set,
    in which case free pins face the centroid of the components they connect to.
    """
    peers: Dict[Tuple[str, str], List[Point]] = {}
    if use_geometry:
        for net in schematic.nets:
            centers = {c.component_id: schematic.components[c.component_id].center
                       for c in net.connections if c.component_id in schematic.components}
            for c in net.connections:
                pts = [p for cid, p in centers.items() if cid != c.component_id]
                peers.setdefault((c.component_id, c.pin_id or c.pin_name), []).extend(pts)
    for comp in schematic.components.values():
        if not use_geometry:
            default_sides(comp)
        else:
            for p in comp.pins.values():
                if p.side_specified:
                    continue
                pts = peers.get((comp.id, p.id)) or peers.get((comp.id, p.name)) or []
                if pts:
                    dx = sum(pt.x for pt in pts) / len(pts) - comp.center.x
                    p.side = Side.LEFT if dx < 0 else Side.RIGHT
            apply_order(comp, lambda p: 0.0)
        size_and_place_pins(comp)


def resolve_pin(comp: Component, pin_id: Optional[str], pin_name: str) -> Optional[Pin]:
    if pin_id and pin_id in comp.pins:
        return comp.pins[pin_id]
    return comp.get_pin(pin_name)
