"""Orthogonal schematic router with channel allocation, obstacle avoidance, and junction dot generation."""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import (
    Component,
    Net,
    NetConnection,
    Pin,
    Point,
    Rect,
    Schematic,
    Side,
    WireJunction,
    WireSegment,
)

TRACK_PITCH = 14.0
OBSTACLE_PADDING = 18.0


class SchematicRouter:
    def __init__(self, schematic: Schematic):
        self.schematic = schematic
        self.segments: List[WireSegment] = []
        self.junctions: List[WireJunction] = []
        # Track occupied horizontal and vertical channels to space parallel wires
        self.occupied_x: List[float] = []
        self.occupied_y: List[float] = []

    def route(self) -> None:
        self.segments.clear()
        self.junctions.clear()
        self.occupied_x.clear()
        self.occupied_y.clear()

        obstacles = [
            comp.rect.expanded(OBSTACLE_PADDING)
            for comp in self.schematic.components.values()
        ]

        # Route power/gnd and multi-drop nets first, then point-to-point signals
        sorted_nets = sorted(
            self.schematic.nets,
            key=lambda n: (
                0 if _is_power_or_gnd(n.name) else 1,
                -len(n.connections),
            ),
        )

        for net_idx, net in enumerate(sorted_nets):
            color = self._assign_net_color(net.name)
            net.color = color

            pins = self._get_net_pins(net)
            if len(pins) < 2:
                continue

            if len(pins) == 2:
                # 2-pin point-to-point route
                self._route_point_to_point(pins[0], pins[1], net.name, color, obstacles, net_idx)
            else:
                # Multi-drop / Bus net route
                self._route_bus_net(pins, net.name, color, obstacles, net_idx)

        # Remove redundant/duplicate zero-length segments
        cleaned_segments = []
        for s in self.segments:
            if s.start.distance_to(s.end) > 0.5:
                cleaned_segments.append(s)

        self.schematic.wires = cleaned_segments
        self.schematic.junctions = self.junctions

    def _get_net_pins(self, net: Net) -> List[Pin]:
        pins: List[Pin] = []
        for conn in net.connections:
            comp = self.schematic.components.get(conn.component_id)
            if comp and conn.pin_name in comp.pins:
                pins.append(comp.pins[conn.pin_name])
        return pins

    def _assign_net_color(self, net_name: str) -> str:
        name_u = net_name.upper()
        if "GND" in name_u or "VSS" in name_u:
            return "#4a5568"  # Slate dark
        if any(term in name_u for term in ["3V3", "5V", "VCC", "VIN", "VDD", "PWR"]):
            return "#c53030"  # Schematic Power Red
        if "SDA" in name_u:
            return "#2b6cb0"  # I2C SDA Blue
        if "SCL" in name_u or "SCK" in name_u or "CLK" in name_u:
            return "#2c7a7b"  # Clock Teal
        if any(term in name_u for term in ["MOSI", "MISO", "CS", "SPI"]):
            return "#6b46c1"  # SPI Purple
        if any(term in name_u for term in ["TX", "RX"]):
            return "#b7791f"  # UART Amber
        return "#1a365d"      # Standard schematic signal dark navy

    def _route_point_to_point(
        self,
        p1: Pin,
        p2: Pin,
        net_name: str,
        color: str,
        obstacles: List[Rect],
        net_idx: int,
    ) -> None:
        pt1 = p1.position
        pt2 = p2.position

        # Direct horizontal line if aligned and clear
        if abs(pt1.y - pt2.y) < 1.0 and not self._is_line_blocked(pt1, pt2, obstacles):
            self.segments.append(WireSegment(pt1, pt2, net_name, color))
            return

        # Direct vertical line if aligned and clear
        if abs(pt1.x - pt2.x) < 1.0 and not self._is_line_blocked(pt1, pt2, obstacles):
            self.segments.append(WireSegment(pt1, pt2, net_name, color))
            return

        # Determine exit directions
        exit1 = _get_pin_exit_vector(p1.side)
        exit2 = _get_pin_exit_vector(p2.side)

        # If p1 exits right and p2 exits left (or vice-versa)
        if (p1.side == Side.RIGHT and p2.side == Side.LEFT and pt1.x < pt2.x) or \
           (p1.side == Side.LEFT and p2.side == Side.RIGHT and pt1.x > pt2.x):
            mid_x = (pt1.x + pt2.x) / 2.0 + (net_idx % 5 - 2) * TRACK_PITCH
            c1 = Point(mid_x, pt1.y)
            c2 = Point(mid_x, pt2.y)
            if not self._is_line_blocked(c1, c2, obstacles):
                self._add_wire_path([pt1, c1, c2, pt2], net_name, color)
                return

        # If p1 and p2 exit on same side (e.g. both right or both left)
        if p1.side == p2.side:
            if p1.side in (Side.LEFT, Side.RIGHT):
                sign = 1.0 if p1.side == Side.RIGHT else -1.0
                outer_x = max(pt1.x, pt2.x) + 30.0 + (net_idx % 4) * TRACK_PITCH if sign > 0 else min(pt1.x, pt2.x) - 30.0 - (net_idx % 4) * TRACK_PITCH
                c1 = Point(outer_x, pt1.y)
                c2 = Point(outer_x, pt2.y)
                self._add_wire_path([pt1, c1, c2, pt2], net_name, color)
                return

        # General Z-bend with clear channel search
        cand_x = (pt1.x + pt2.x) / 2.0
        c1 = Point(cand_x, pt1.y)
        c2 = Point(cand_x, pt2.y)
        if not self._is_line_blocked(pt1, c1, obstacles) and not self._is_line_blocked(c1, c2, obstacles) and not self._is_line_blocked(c2, pt2, obstacles):
            self._add_wire_path([pt1, c1, c2, pt2], net_name, color)
            return

        # Fallback via safe outer bypass channel
        safe_y = min(o.top for o in obstacles) - 30.0 - (net_idx % 6) * TRACK_PITCH
        if pt1.y > pt2.y and p1.side == Side.BOTTOM:
            safe_y = max(o.bottom for o in obstacles) + 30.0 + (net_idx % 6) * TRACK_PITCH

        p1_ext = Point(pt1.x + exit1.x * 20.0, pt1.y + exit1.y * 20.0)
        p2_ext = Point(pt2.x + exit2.x * 20.0, pt2.y + exit2.y * 20.0)
        b1 = Point(p1_ext.x, safe_y)
        b2 = Point(p2_ext.x, safe_y)

        self._add_wire_path([pt1, p1_ext, b1, b2, p2_ext, pt2], net_name, color)

    def _route_bus_net(
        self,
        pins: List[Pin],
        net_name: str,
        color: str,
        obstacles: List[Rect],
        net_idx: int,
    ) -> None:
        """
        Routes multi-point nets (e.g. GND, 3V3, I2C_SDA, I2C_SCL).
        Constructs a clean Bus Trunk Line with orthogonal branch taps and junction dots.
        """
        pts = [p.position for p in pins]

        min_x = min(p.x for p in pts)
        max_x = max(p.x for p in pts)
        min_y = min(p.y for p in pts)
        max_y = max(p.y for p in pts)

        dx = max_x - min_x
        dy = max_y - min_y

        is_gnd = "GND" in net_name.upper() or "VSS" in net_name.upper()
        is_power = any(w in net_name.upper() for w in ["3V3", "5V", "VCC", "VIN", "VDD"])

        # Decide whether trunk is horizontal or vertical:
        # If pins are distributed primarily horizontally or it's GND/Power rail
        if is_gnd:
            # Place GND trunk line along the bottom channel
            trunk_y = max(o.bottom for o in obstacles) + 25.0 + (net_idx % 4) * TRACK_PITCH
            self._route_horizontal_bus(pins, trunk_y, net_name, color)
            return

        if is_power:
            # Place Power trunk line along the top channel
            trunk_y = min(o.top for o in obstacles) - 25.0 - (net_idx % 4) * TRACK_PITCH
            self._route_horizontal_bus(pins, trunk_y, net_name, color)
            return

        # For signal buses like I2C (SDA/SCL) or SPI
        if dy >= dx:
            # Vertical trunk line in the corridor between MCU and sensors
            trunk_x = self._find_clear_vertical_corridor(min_x, max_x, obstacles, net_idx)
            self._route_vertical_bus(pins, trunk_x, net_name, color)
        else:
            # Horizontal trunk line
            trunk_y = self._find_clear_horizontal_corridor(min_y, max_y, obstacles, net_idx)
            self._route_horizontal_bus(pins, trunk_y, net_name, color)

    def _route_horizontal_bus(self, pins: List[Pin], trunk_y: float, net_name: str, color: str) -> None:
        tap_points: List[Point] = []

        # Sort pins by x coordinate
        sorted_pins = sorted(pins, key=lambda p: p.position.x)

        for pin in sorted_pins:
            p_term = pin.position
            # Branch tap from pin to trunk_y
            tap_junction = Point(p_term.x, trunk_y)
            self._add_wire_path([p_term, tap_junction], net_name, color)
            tap_points.append(tap_junction)

        # Route main trunk line between min_x and max_x of the taps
        trunk_start = Point(tap_points[0].x, trunk_y)
        trunk_end = Point(tap_points[-1].x, trunk_y)
        self.segments.append(WireSegment(trunk_start, trunk_end, net_name, color))

        # Add junction dots at all taps except extreme ends if only single branch
        for tap in tap_points:
            self.junctions.append(WireJunction(point=tap, radius=3.5, net_name=net_name))

    def _route_vertical_bus(self, pins: List[Pin], trunk_x: float, net_name: str, color: str) -> None:
        tap_points: List[Point] = []

        # Sort pins by y coordinate
        sorted_pins = sorted(pins, key=lambda p: p.position.y)

        for pin in sorted_pins:
            p_term = pin.position
            tap_junction = Point(trunk_x, p_term.y)
            self._add_wire_path([p_term, tap_junction], net_name, color)
            tap_points.append(tap_junction)

        # Route main trunk line between min_y and max_y of taps
        trunk_start = Point(trunk_x, tap_points[0].y)
        trunk_end = Point(trunk_x, tap_points[-1].y)
        self.segments.append(WireSegment(trunk_start, trunk_end, net_name, color))

        # Add junction dots
        for tap in tap_points:
            self.junctions.append(WireJunction(point=tap, radius=3.5, net_name=net_name))

    def _find_clear_vertical_corridor(self, min_x: float, max_x: float, obstacles: List[Rect], net_idx: int) -> float:
        center_x = (min_x + max_x) / 2.0
        # Offset to prevent parallel bus line overlaps
        offset = (net_idx % 6 - 2.5) * TRACK_PITCH
        return center_x + offset

    def _find_clear_horizontal_corridor(self, min_y: float, max_y: float, obstacles: List[Rect], net_idx: int) -> float:
        center_y = (min_y + max_y) / 2.0
        offset = (net_idx % 6 - 2.5) * TRACK_PITCH
        return center_y + offset

    def _add_wire_path(self, points: List[Point], net_name: str, color: str) -> None:
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            if p1.distance_to(p2) > 0.5:
                self.segments.append(WireSegment(p1, p2, net_name, color))

    def _is_line_blocked(self, p1: Point, p2: Point, obstacles: List[Rect]) -> bool:
        """Checks if a horizontal or vertical segment passes through any obstacle rect."""
        min_x, max_x = min(p1.x, p2.x), max(p1.x, p2.x)
        min_y, max_y = min(p1.y, p2.y), max(p1.y, p2.y)

        # Check horizontal segment
        if abs(p1.y - p2.y) < 1.0:
            y = p1.y
            for obs in obstacles:
                if obs.top <= y <= obs.bottom:
                    # Overlaps in x?
                    if not (max_x <= obs.left or min_x >= obs.right):
                        return True
            return False

        # Check vertical segment
        if abs(p1.x - p2.x) < 1.0:
            x = p1.x
            for obs in obstacles:
                if obs.left <= x <= obs.right:
                    # Overlaps in y?
                    if not (max_y <= obs.top or min_y >= obs.bottom):
                        return True
            return False

        return False


def _get_pin_exit_vector(side: Side) -> Point:
    if side == Side.LEFT:
        return Point(-1.0, 0.0)
    if side == Side.RIGHT:
        return Point(1.0, 0.0)
    if side == Side.TOP:
        return Point(0.0, -1.0)
    return Point(0.0, 1.0)


def _is_power_or_gnd(net_name: str) -> bool:
    nu = net_name.upper()
    return "GND" in nu or "VSS" in nu or any(w in nu for w in ["3V3", "5V", "VCC", "VIN", "VDD"])


def route_schematic(schematic: Schematic) -> None:
    router = SchematicRouter(schematic)
    router.route()
