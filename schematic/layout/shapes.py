"""Component geometry in grid units.

Every component is described by a *shape*: body size, the pins with their
terminal (where a wire attaches), the lead start (where the pin meets the
body) and the outward direction, plus the zones occupied by text.  All
coordinates are integers in grid units (1 = 20 px) relative to the
component's top-left corner, so placement and routing work on exact
integers.

A component can have several *variants*:

* two-pin symbols (resistor, capacitor, ...): four rotations;
* three-pin switches: common terminal left or right;
* image parts: 0 / 90 / 270 degrees in placement (only 0 unless rotatable;
  180 is supported by the geometry and the renderer but never chosen);
* boxes: one variant -- their free pins are assigned to sides from geometry
  (``placement.Layout.geo_sides``) and then optimised; declared sides are fixed.

Nothing here looks at names except to measure the width of text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from schematic.models import Component, Pin, Point, Side

GRID = 20.0

VEC: Dict[Side, Tuple[int, int]] = {
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
CW: Dict[Side, Side] = {
    Side.LEFT: Side.TOP,
    Side.TOP: Side.RIGHT,
    Side.RIGHT: Side.BOTTOM,
    Side.BOTTOM: Side.LEFT,
}

# Approximate glyph widths (px) of the renderer's fonts.
CHAR_TITLE = 6.6      # 10-11 px bold sans
CHAR_VALUE = 5.6      # 9 px mono
CHAR_PIN_BOX = 6.2    # 10 px mono (box pin names)
CHAR_PIN_TAG = 5.0    # 8 px mono (image part pin tags)

Box = Tuple[float, float, float, float]  # x0, y0, x1, y1 in grid units


@dataclass(frozen=True)
class PinShape:
    id: str
    tx: int            # terminal, relative to the component origin
    ty: int
    lx: int            # lead start (on the body outline or inside the body)
    ly: int
    side: Side         # direction a wire leaves the terminal
    order: int = 0


@dataclass
class Shape:
    w: int
    h: int
    pins: Dict[str, PinShape]
    text: List[Box] = field(default_factory=list)   # text zones (soft obstacles)
    rotation: int = 0                               # image rotation in degrees
    vertical: bool = False                          # two-pin symbol drawn vertically

    def __post_init__(self) -> None:
        xs = [0.0, float(self.w)]
        ys = [0.0, float(self.h)]
        for p in self.pins.values():
            xs.append(float(p.tx))
            ys.append(float(p.ty))
        for b in self.text:
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
        # Footprint: everything the component draws, in whole grid cells.
        self.footprint: Tuple[int, int, int, int] = (
            math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys)))


def _text_w(text: str, char: float) -> float:
    return len(text) * char / GRID


def two_pin_kind(comp: Component) -> bool:
    return comp.is_standard and len(comp.pins) == 2


def switch_kind(comp: Component) -> bool:
    return comp.is_standard and comp.kind == "switch" and len(comp.pins) == 3


def classify(comp: Component) -> str:
    """'symbol2' | 'switch3' | 'image' | 'box'."""
    if comp.image:
        return "image"
    if two_pin_kind(comp):
        return "symbol2"
    if switch_kind(comp):
        return "switch3"
    return "box"


# ------------------------------------------------------------------ symbols
def symbol2_shape(comp: Component, rot: int) -> Shape:
    """rot 0: first pin left; 1: first pin top; 2: first pin right; 3: first pin bottom."""
    a, b = list(comp.pins.values())
    cap = comp.kind == "capacitor"
    vertical = rot in (1, 3)
    if vertical:
        w, h = 2, (2 if cap else 3)
        top, bot = (a, b) if rot == 1 else (b, a)
        pins = {
            top.id: PinShape(top.id, 1, 0, 1, 0, Side.TOP),
            bot.id: PinShape(bot.id, 1, h, 1, h, Side.BOTTOM),
        }
        # Name / value to the right of the symbol, upright.
        tw = max(_text_w(comp.id, CHAR_TITLE), _text_w(comp.value, CHAR_VALUE))
        text = [(1.7, h / 2.0 - 0.75, 1.7 + tw, h / 2.0 + 0.75)]
    else:
        w, h = (2 if cap else 3), 2
        left, right = (a, b) if rot == 0 else (b, a)
        pins = {
            left.id: PinShape(left.id, 0, 1, 0, 1, Side.LEFT),
            right.id: PinShape(right.id, w, 1, w, 1, Side.RIGHT),
        }
        cx = w / 2.0
        tw = _text_w(comp.id, CHAR_TITLE)
        vw = _text_w(comp.value, CHAR_VALUE)
        text = [(cx - tw / 2.0, -0.35, cx + tw / 2.0, 0.35)]
        if comp.value:
            text.append((cx - vw / 2.0, 1.45, cx + vw / 2.0, 2.0))
    return Shape(w, h, pins, text, vertical=vertical)


def switch_common(comp: Component) -> Pin:
    """The common terminal of a three-pin switch is its middle pin."""
    pins = list(comp.pins.values())
    return pins[len(pins) // 2]


def switch3_shape(comp: Component, rot: int) -> Shape:
    """rot 0: common terminal left, throws right; rot 1: mirrored."""
    common = switch_common(comp)
    throws = [p for p in comp.pins.values() if p is not common]
    w, h = 3, 2
    cs, ts = (Side.LEFT, Side.RIGHT) if rot == 0 else (Side.RIGHT, Side.LEFT)
    cx, tx = (0, w) if rot == 0 else (w, 0)
    pins = {common.id: PinShape(common.id, cx, 1, cx, 1, cs)}
    for i, p in enumerate(throws):
        pins[p.id] = PinShape(p.id, tx, 2 * i, tx, 2 * i, ts, i)
    tw = _text_w(comp.id, CHAR_TITLE)
    vw = _text_w(comp.value, CHAR_VALUE)
    text = [(1.5 - tw / 2.0, -0.9, 1.5 + tw / 2.0, -0.2)]
    if comp.value:
        text.append((1.5 - vw / 2.0, 2.2, 1.5 + vw / 2.0, 2.9))
    return Shape(w, h, pins, text)


# ------------------------------------------------------------------- images
def _rotate_point(gx: int, gy: int, gw: int, gh: int, rot: int) -> Tuple[int, int]:
    for _ in range(rot // 90):
        gx, gy, gw, gh = gh - gy, gx, gh, gw
    return gx, gy


def _rotate_side(side: Side, rot: int) -> Side:
    for _ in range(rot // 90):
        side = CW[side]
    return side


def image_shape(comp: Component, rot: int) -> Shape:
    gw = comp.grid_width if comp.grid_width > 0 else 6
    gh = comp.grid_height if comp.grid_height > 0 else 6
    w, h = (gh, gw) if rot in (90, 270) else (gw, gh)
    pins: Dict[str, PinShape] = {}
    text: List[Box] = [(0.0, -0.9, _text_w(comp.id, CHAR_TITLE) + 0.3, -0.1)]
    for pin in comp.pins.values():
        if pin.grid_x is None or pin.grid_y is None:
            continue
        lx, ly = _rotate_point(pin.grid_x, pin.grid_y, gw, gh, rot)
        if pin.side_specified:
            side = _rotate_side(pin.side, rot)
        else:
            d = {Side.LEFT: lx, Side.RIGHT: w - lx, Side.TOP: ly, Side.BOTTOM: h - ly}
            side = min((Side.LEFT, Side.RIGHT, Side.TOP, Side.BOTTOM), key=lambda s: d[s])
        if side == Side.LEFT:
            tx, ty = -1, ly
        elif side == Side.RIGHT:
            tx, ty = w + 1, ly
        elif side == Side.TOP:
            tx, ty = lx, -1
        else:
            tx, ty = lx, h + 1
        pins[pin.id] = PinShape(pin.id, tx, ty, lx, ly, side)
        tag = _text_w(pin.display_name, CHAR_PIN_TAG) + 0.3
        # Pin tags run along their own wire, just outside the terminal.
        if side == Side.LEFT:
            text.append((tx - tag, ty - 0.6, tx, ty - 0.1))
        elif side == Side.RIGHT:
            text.append((tx, ty - 0.6, tx + tag, ty - 0.1))
        elif side == Side.TOP:
            text.append((tx + 0.1, ty - tag, tx + 0.6, ty))
        else:
            text.append((tx + 0.1, ty, tx + 0.6, ty + tag))
    return Shape(w, h, pins, text, rotation=rot)


# -------------------------------------------------------------------- boxes
BOX_FIRST_PIN = 3        # first side pin row below the header (grid units)


def box_shape(comp: Component, sides: Dict[str, Side], orders: Dict[str, float]) -> Shape:
    """Box with pins on the given sides, sorted by ``orders`` within a side."""
    groups: Dict[Side, List[Pin]] = {s: [] for s in (Side.LEFT, Side.RIGHT, Side.TOP, Side.BOTTOM)}
    for pin in comp.pins.values():
        groups[sides[pin.id]].append(pin)
    for s in groups:
        groups[s].sort(key=lambda p: (orders.get(p.id, 0.0), p.order, list(comp.pins).index(p.id)))
    left, right, top, bottom = (groups[s] for s in (Side.LEFT, Side.RIGHT, Side.TOP, Side.BOTTOM))

    label_l = max((len(p.display_name) for p in left), default=0)
    label_r = max((len(p.display_name) for p in right), default=0)
    title = max(len(comp.id), len(comp.type_name))
    width_px = max(
        120.0 if len(comp.pins) <= 2 else 140.0,
        (label_l + label_r) * CHAR_PIN_BOX + 44.0,
        title * 8.5 + 32.0,
        (max(len(top), len(bottom)) + 3) * GRID,
    )
    w = int(math.ceil(width_px / GRID))
    n_side = max(len(left), len(right), 1)
    h = max(4, BOX_FIRST_PIN + n_side)
    pins: Dict[str, PinShape] = {}
    for i, p in enumerate(left):
        y = BOX_FIRST_PIN + i
        pins[p.id] = PinShape(p.id, -1, y, 0, y, Side.LEFT, i)
    for i, p in enumerate(right):
        y = BOX_FIRST_PIN + i
        pins[p.id] = PinShape(p.id, w + 1, y, w, y, Side.RIGHT, i)
    for i, p in enumerate(top):
        x = 2 + i
        pins[p.id] = PinShape(p.id, x, -1, x, 0, Side.TOP, i)
    for i, p in enumerate(bottom):
        x = 2 + i
        pins[p.id] = PinShape(p.id, x, h + 1, x, h, Side.BOTTOM, i)
    return Shape(w, h, pins, [])


# ------------------------------------------------------------- write-back
def apply_shape(comp: Component, shape: Shape, gx: int, gy: int) -> None:
    """Writes a placed shape back into the component (pixel coordinates)."""
    comp.x, comp.y = gx * GRID, gy * GRID
    comp.width, comp.height = shape.w * GRID, shape.h * GRID
    comp.rotation = shape.rotation
    for pid, ps in shape.pins.items():
        pin = comp.pins[pid]
        pin.side = ps.side
        pin.order = ps.order
        pin.position = Point((gx + ps.tx) * GRID, (gy + ps.ty) * GRID)
        pin.lead_start = Point((gx + ps.lx) * GRID, (gy + ps.ly) * GRID)


def is_vertical_symbol(comp: Component) -> bool:
    """True when a placed two-pin symbol runs top-to-bottom (used by the renderer)."""
    pins = list(comp.pins.values())
    if len(pins) != 2:
        return False
    return abs(pins[0].position.x - pins[1].position.x) < 1e-6 and abs(pins[0].position.y - pins[1].position.y) > 1e-6


def text_boxes_abs(shape: Shape, gx: int, gy: int) -> Sequence[Box]:
    return [(gx + b[0], gy + b[1], gx + b[2], gy + b[3]) for b in shape.text]
