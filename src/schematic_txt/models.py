"""Data models for text-based schematic representation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


GRID_SIZE: float = 20.0


def snap_to_grid(val: float, grid: float = GRID_SIZE) -> float:
    return round(val / grid) * grid


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def snapped(self, grid: float = GRID_SIZE) -> Point:
        return Point(snap_to_grid(self.x, grid), snap_to_grid(self.y, grid))

    def distance_to(self, other: Point) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def manhattan_to(self, other: Point) -> float:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y)


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2.0, self.y + self.height / 2.0)

    def expanded(self, margin: float) -> Rect:
        return Rect(
            x=self.x - margin,
            y=self.y - margin,
            width=self.width + 2 * margin,
            height=self.height + 2 * margin,
        )

    def contains_point(self, p: Point) -> bool:
        return self.left <= p.x <= self.right and self.top <= p.y <= self.bottom

    def intersects(self, other: Rect) -> bool:
        return not (
            self.right < other.left
            or self.left > other.right
            or self.bottom < other.top
            or self.top > other.bottom
        )


@dataclass
class Pin:
    name: str
    label: str = ""
    side: Side = Side.LEFT
    order: int = 0
    side_specified: bool = False
    id: str = ""
    lead_start: Point = field(default_factory=lambda: Point(0.0, 0.0))
    position: Point = field(default_factory=lambda: Point(0.0, 0.0))
    grid_x: int | None = None
    grid_y: int | None = None

    def __post_init__(self):
        if not self.id:
            self.id = self.name

    @property
    def display_name(self) -> str:
        if self.label and self.label != self.name:
            return f"{self.name} ({self.label})"
        return self.name


STANDARD_KINDS = {"resistor", "capacitor", "switch", "fuse", "diode", "led", "inductor"}

KIND_SYNONYMS = {
    "resistor": "resistor",
    "res": "resistor",
    "r": "resistor",
    "widerstand": "resistor",
    "capacitor": "capacitor",
    "cap": "capacitor",
    "c": "capacitor",
    "kondensator": "capacitor",
    "switch": "switch",
    "sw": "switch",
    "schalter": "switch",
    "fuse": "fuse",
    "sicherung": "fuse",
    "diode": "diode",
    "d": "diode",
    "led": "led",
    "inductor": "inductor",
    "ind": "inductor",
    "l": "inductor",
    "spule": "inductor",
    "drossel": "inductor",
}


def normalize_kind(val: str) -> str | None:
    v = val.strip().lower()
    return KIND_SYNONYMS.get(v)


@dataclass
class Component:
    id: str
    type_name: str = ""
    pins: dict[str, Pin] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    width: float = 140.0
    height: float = 80.0
    kind: str = ""
    value: str = ""
    image: str = ""
    image_rect: tuple[float, float, float, float] | None = None
    grid_width: int = 0
    grid_height: int = 0
    rotatable: bool = True
    rotatable_explicit: bool = False
    rotation: int = 0

    def __post_init__(self):
        if not self.kind and self.type_name:
            self.detect_standard_type()

    @property
    def is_standard(self) -> bool:
        return bool(self.kind and self.kind in STANDARD_KINDS)

    def set_kind(self, kind_str: str) -> None:
        raw = kind_str.strip().lower()
        if raw in ("false", "none", "no", "box", "ic", "module"):
            self.kind = ""
            return
        if raw in ("true", "yes", "standard"):
            # Try to detect from type_name or default to resistor if unspecified
            if not self.kind and self.type_name:
                self.detect_standard_type()
            if not self.kind:
                self.kind = "resistor"
            return
        norm = normalize_kind(raw)
        self.kind = norm if norm else raw
        if not self.value and self.type_name:
            t = self.type_name.strip()
            if t.lower() not in KIND_SYNONYMS and t.lower() not in STANDARD_KINDS:
                self.value = t

    def detect_standard_type(self) -> None:
        if self.kind:
            return
        t = self.type_name.strip()
        if not t:
            return

        # Do not match multi-pin ICs, controllers, monitors, adapters, converters
        skip_words = {
            "ic",
            "controller",
            "monitor",
            "adapter",
            "module",
            "board",
            "shield",
            "sensor",
            "display",
            "step-down",
            "battery",
            "supply",
            "converter",
        }
        t_lower = t.lower()
        t_words = set(re.findall(r"[a-z0-9_\-]+", t_lower))
        if any(sw in t_words for sw in skip_words):
            return

        # 1. Matches "Kind(Value)" e.g. "Resistor(2kOhm)" or "res(10k)"
        m = re.match(r"^([A-Za-z]+)\s*\((.*?)\)$", t)
        if m:
            k = normalize_kind(m.group(1))
            if k:
                self.kind = k
                if not self.value:
                    self.value = m.group(2).strip()
                return

        # 2. Matches "Kind Value" e.g. "Resistor 2kOhm" or "Switch SPDT"
        m = re.match(r"^([A-Za-z]+)\s+([A-Za-z0-9_\-\.\/]+.*)$", t)
        if m:
            k = normalize_kind(m.group(1))
            if k:
                self.kind = k
                if not self.value:
                    self.value = m.group(2).strip()
                return

        # 3. Matches "Value Kind" e.g. "2kOhm Resistor", "100nF Capacitor",
        #    "Inline Fuse", "Slide Switch (SPDT)"
        #    Single-letter synonyms (r, c, l, d) are not accepted here: "Device C" or
        #    "Header L" are names, not a capacitor or an inductor.
        m = re.match(r"^(.*?)\s+([A-Za-z]{2,})(?:\s*\((.*?)\))?$", t)
        if m:
            k = normalize_kind(m.group(2))
            if k:
                self.kind = k
                if not self.value:
                    sub_val = m.group(3)
                    prefix_val = m.group(1).strip()
                    if sub_val:
                        self.value = sub_val.strip()
                    elif prefix_val:
                        self.value = prefix_val
                return

        # 4. Single word standard kind e.g. "Resistor" or "Capacitor"
        k = normalize_kind(t)
        if k:
            self.kind = k

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.width, self.height)

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2.0, self.y + self.height / 2.0)

    def add_pin(
        self,
        name: str,
        label: str = "",
        side: Side = Side.LEFT,
        order: int = 0,
        side_specified: bool = False,
        pin_id: str | None = None,
        grid_x: int | None = None,
        grid_y: int | None = None,
    ) -> Pin:
        if not pin_id:
            existing_same_name = [p for p in self.pins.values() if p.name.upper() == name.upper()]
            if not existing_same_name and name not in self.pins:
                pin_id = name
            else:
                pin_id = f"{name}#{len(existing_same_name) + 1}"

        pin = Pin(
            name=name,
            label=label,
            side=side,
            order=order,
            side_specified=side_specified,
            id=pin_id,
            grid_x=grid_x,
            grid_y=grid_y,
        )
        self.pins[pin_id] = pin
        return pin

    def get_or_create_pin(self, pin_name: str, label: str = "") -> Pin:
        matches = self.get_pins_by_name(pin_name)
        if matches:
            if label and not matches[0].label:
                matches[0].label = label
            return matches[0]
        return self.add_pin(pin_name, label=label, side_specified=False)

    def get_pins_by_name(self, name: str) -> list[Pin]:
        name_u = name.upper()
        # Match on name, id, label (e.g. SDA) or full name(label) (e.g. GPIO21(SDA))
        results = []
        for p in self.pins.values():
            if (
                p.name.upper() == name_u
                or p.id.upper() == name_u
                or (p.label and p.label.upper() == name_u)
                or (p.label and f"{p.name}({p.label})".upper() == name_u)
            ):
                results.append(p)
        return results

    def get_pin(self, name_or_id: str) -> Pin | None:
        if name_or_id in self.pins:
            return self.pins[name_or_id]
        matches = self.get_pins_by_name(name_or_id)
        return matches[0] if matches else None


@dataclass
class NetConnection:
    component_id: str
    pin_name: str
    pin_id: str | None = None
    peer_component_ids: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.component_id}.{self.pin_id or self.pin_name}"


@dataclass
class Net:
    name: str
    connections: list[NetConnection] = field(default_factory=list)
    color: str | None = None
    is_bus: bool = False
    auto_named: bool = False  # True when the parser generated the name ("Comp_Pin")

    def add_connection(
        self,
        comp_id: str,
        pin_name: str,
        pin_id: str | None = None,
        peer_comp_ids: list[str] | None = None,
    ) -> None:
        p_list = list(peer_comp_ids or [])
        for c in self.connections:
            same_pin = c.component_id == comp_id and c.pin_name == pin_name
            if same_pin and (c.peer_component_ids == p_list or not p_list):
                if pin_id and not c.pin_id:
                    c.pin_id = pin_id
                return

        conn = NetConnection(
            component_id=comp_id,
            pin_name=pin_name,
            pin_id=pin_id,
            peer_component_ids=p_list,
        )
        self.connections.append(conn)


@dataclass
class WireSegment:
    start: Point
    end: Point
    net_name: str = ""
    color: str | None = None

    @property
    def is_horizontal(self) -> bool:
        return abs(self.start.y - self.end.y) < 1e-4

    @property
    def is_vertical(self) -> bool:
        return abs(self.start.x - self.end.x) < 1e-4


@dataclass
class WireJunction:
    point: Point
    radius: float = 3.5
    net_name: str = ""


@dataclass
class NetLabel:
    """A net-name annotation placed on a horizontal wire segment."""

    text: str
    x: float  # text anchor (start) position
    y: float  # text baseline
    width: float  # estimated rendered width
    net_name: str = ""


@dataclass
class PartTemplate:
    name: str
    image: str = ""
    image_rect: tuple[float, float, float, float] | None = None
    grid_width: int = 0
    grid_height: int = 0
    pins: dict[str, tuple[int, int, Side | None]] = field(default_factory=dict)
    pin_list: list[tuple[str, int, int, Side | None]] = field(default_factory=list)
    rotatable: bool | None = None

    def add_pin(self, p_raw: str, gx: int, gy: int, side: Side | None = None) -> None:
        self.pins[p_raw] = (gx, gy, side)
        self.pin_list.append((p_raw, gx, gy, side))


@dataclass
class Schematic:
    components: dict[str, Component] = field(default_factory=dict)
    parts: dict[str, PartTemplate] = field(default_factory=dict)
    nets: list[Net] = field(default_factory=list)
    wires: list[WireSegment] = field(default_factory=list)
    junctions: list[WireJunction] = field(default_factory=list)
    width: float = 1200.0
    height: float = 800.0
    labels: list[NetLabel] = field(default_factory=list)

    def get_or_create_part(self, name: str) -> PartTemplate:
        if name not in self.parts:
            self.parts[name] = PartTemplate(name=name)
        return self.parts[name]

    def get_or_create_component(self, comp_id: str, type_name: str = "") -> Component:
        if comp_id not in self.components:
            self.components[comp_id] = Component(id=comp_id, type_name=type_name)
        elif type_name and not self.components[comp_id].type_name:
            self.components[comp_id].type_name = type_name
            self.components[comp_id].detect_standard_type()
        return self.components[comp_id]
