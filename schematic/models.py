"""Data models for text-based schematic representation."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Dict, List, Optional, Tuple


class Side(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: Point) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def manhattan_to(self, other: Point) -> float:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def as_tuple(self) -> Tuple[float, float]:
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
    lead_start: Point = field(default_factory=lambda: Point(0.0, 0.0))
    position: Point = field(default_factory=lambda: Point(0.0, 0.0))

    @property
    def display_name(self) -> str:
        if self.label and self.label != self.name:
            return f"{self.name} ({self.label})"
        return self.name


@dataclass
class Component:
    id: str
    type_name: str = ""
    pins: Dict[str, Pin] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    width: float = 140.0
    height: float = 80.0

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.width, self.height)

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2.0, self.y + self.height / 2.0)

    def get_or_create_pin(self, pin_name: str, label: str = "") -> Pin:
        if pin_name not in self.pins:
            self.pins[pin_name] = Pin(name=pin_name, label=label)
        elif label and not self.pins[pin_name].label:
            self.pins[pin_name].label = label
        return self.pins[pin_name]


@dataclass
class NetConnection:
    component_id: str
    pin_name: str

    @property
    def key(self) -> str:
        return f"{self.component_id}.{self.pin_name}"


@dataclass
class Net:
    name: str
    connections: List[NetConnection] = field(default_factory=list)
    color: Optional[str] = None
    is_bus: bool = False

    def add_connection(self, comp_id: str, pin_name: str) -> None:
        conn = NetConnection(comp_id, pin_name)
        if not any(c.component_id == comp_id and c.pin_name == pin_name for c in self.connections):
            self.connections.append(conn)


@dataclass
class WireSegment:
    start: Point
    end: Point
    net_name: str = ""
    color: Optional[str] = None

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
class Schematic:
    components: Dict[str, Component] = field(default_factory=dict)
    nets: List[Net] = field(default_factory=list)
    wires: List[WireSegment] = field(default_factory=list)
    junctions: List[WireJunction] = field(default_factory=list)
    width: float = 1200.0
    height: float = 800.0

    def get_or_create_component(self, comp_id: str, type_name: str = "") -> Component:
        if comp_id not in self.components:
            self.components[comp_id] = Component(id=comp_id, type_name=type_name)
        elif type_name and not self.components[comp_id].type_name:
            self.components[comp_id].type_name = type_name
        return self.components[comp_id]
