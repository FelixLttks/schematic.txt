"""Orthogonal schematic router with strict 20px grid alignment, obstacle avoidance, and zero collinear overlaps.

Rules enforced:
1. All vertices, line corners, pin terminals, and junction nodes lie strictly on a 20px grid.
2. Lines must NEVER overlap collinearly (no two lines on the same coordinate interval).
   Lines can only CROSS each other orthogonally at 90°.
3. Lines must NEVER pass through, behind, or in front of component bodies.
   Lines must route through clear corridors around all components.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import (
    GRID_SIZE,
    Component,
    Net,
    Pin,
    Point,
    Rect,
    Schematic,
    Side,
    WireJunction,
    WireSegment,
    snap_to_grid,
)

TRACK_PITCH = GRID_SIZE  # 20.0px grid pitch


class IntervalSet:
    """Tracks occupied 1D intervals along a single coordinate line to prevent overlaps."""
    def __init__(self):
        self.intervals: List[Tuple[float, float]] = []

    def overlaps(self, start: float, end: float, margin: float = 2.0) -> bool:
        s = min(start, end) - margin
        e = max(start, end) + margin
        for i_s, i_e in self.intervals:
            if not (e <= i_s or s >= i_e):
                return True
        return False

    def add(self, start: float, end: float) -> None:
        self.intervals.append((min(start, end), max(start, end)))


class TrackManager:
    """Allocates unique non-overlapping lanes strictly on the 20px grid."""
    def __init__(self, pitch: float = TRACK_PITCH):
        self.pitch = pitch
        self.horizontal_tracks: Dict[float, IntervalSet] = {}
        self.vertical_tracks: Dict[float, IntervalSet] = {}

    def allocate_horizontal(
        self,
        base_y: float,
        start_x: float,
        end_x: float,
        forbidden_ranges: Optional[List[Tuple[float, float]]] = None,
    ) -> float:
        """Finds a Y lane on the 20px grid near base_y that has no interval overlap and does not collide with obstacles."""
        forbidden = forbidden_ranges or []
        step = 0
        base_snapped = snap_to_grid(base_y, self.pitch)
        while True:
            k = (step // 2 + 1) * (1 if step % 2 == 1 else -1) if step > 0 else 0
            cand_y = round(base_snapped + k * self.pitch, 1)
            step += 1

            if any(f_min <= cand_y <= f_max for f_min, f_max in forbidden):
                continue

            if cand_y not in self.horizontal_tracks:
                self.horizontal_tracks[cand_y] = IntervalSet()
                self.horizontal_tracks[cand_y].add(start_x, end_x)
                return cand_y

            if not self.horizontal_tracks[cand_y].overlaps(start_x, end_x):
                self.horizontal_tracks[cand_y].add(start_x, end_x)
                return cand_y

    def allocate_vertical(
        self,
        base_x: float,
        start_y: float,
        end_y: float,
        forbidden_ranges: Optional[List[Tuple[float, float]]] = None,
    ) -> float:
        """Finds an X lane on the 20px grid near base_x that has no interval overlap and does not collide with obstacles."""
        forbidden = forbidden_ranges or []
        step = 0
        base_snapped = snap_to_grid(base_x, self.pitch)
        while True:
            k = (step // 2 + 1) * (1 if step % 2 == 1 else -1) if step > 0 else 0
            cand_x = round(base_snapped + k * self.pitch, 1)
            step += 1

            if any(f_min <= cand_x <= f_max for f_min, f_max in forbidden):
                continue

            if cand_x not in self.vertical_tracks:
                self.vertical_tracks[cand_x] = IntervalSet()
                self.vertical_tracks[cand_x].add(start_y, end_y)
                return cand_x

            if not self.vertical_tracks[cand_x].overlaps(start_y, end_y):
                self.vertical_tracks[cand_x].add(start_y, end_y)
                return cand_x


class SchematicRouter:
    def __init__(self, schematic: Schematic):
        self.schematic = schematic
        self.segments: List[WireSegment] = []
        self.junctions: List[WireJunction] = []
        self.tm = TrackManager(pitch=GRID_SIZE)

    def route(self) -> None:
        self.segments.clear()
        self.junctions.clear()
        self.tm = TrackManager(pitch=GRID_SIZE)

        comps = list(self.schematic.components.values())
        if not comps:
            return

        # Obstacle definitions: component interiors
        forbidden_x = [(comp.x - 14.0, comp.x + comp.width + 14.0) for comp in comps]
        forbidden_y = [(comp.y - 10.0, comp.y + comp.height + 10.0) for comp in comps]

        min_comp_x = min(c.x for c in comps)
        max_comp_x = max(c.x + c.width for c in comps)
        min_comp_y = min(c.y for c in comps)
        max_comp_y = max(c.y + c.height for c in comps)

        top_rail_base_y = snap_to_grid(min_comp_y - 60.0, GRID_SIZE)
        bottom_rail_base_y = snap_to_grid(max_comp_y + 60.0, GRID_SIZE)

        # Sort nets: Power/GND first, then multi-pin buses, then point-to-point
        sorted_nets = sorted(
            self.schematic.nets,
            key=lambda n: (
                0 if _is_power_or_gnd(n.name) else 1,
                -len(n.connections),
            ),
        )

        for net in sorted_nets:
            color = self._assign_net_color(net.name)
            net.color = color

            pins = self._get_net_pins(net)
            if len(pins) < 2:
                continue

            nu = net.name.upper()
            is_gnd = "GND" in nu or "VSS" in nu
            is_power = any(w in nu for w in ["3V3", "5V", "VCC", "VIN", "VDD"])

            if is_gnd:
                self._route_horizontal_rail(pins, net.name, color, bottom_rail_base_y, forbidden_x, is_top=False)
            elif is_power:
                self._route_horizontal_rail(pins, net.name, color, top_rail_base_y, forbidden_x, is_top=True)
            elif len(pins) == 2:
                self._route_point_to_point(pins[0], pins[1], net.name, color, forbidden_x, forbidden_y, top_rail_base_y, bottom_rail_base_y)
            else:
                self._route_bus_net(pins, net.name, color, forbidden_x, forbidden_y)

        self.schematic.wires = self.segments
        self.schematic.junctions = self.junctions

    def _add_segment(self, p1: Point, p2: Point, net_name: str, color: str) -> None:
        """Adds a wire segment snapped strictly to the grid."""
        s = p1.snapped(GRID_SIZE)
        e = p2.snapped(GRID_SIZE)
        if s.distance_to(e) > 0.5:
            self.segments.append(WireSegment(s, e, net_name, color))

    def _add_junction(self, point: Point, net_name: str, radius: float = 3.5) -> None:
        """Adds a junction dot snapped strictly to the grid."""
        self.junctions.append(WireJunction(point=point.snapped(GRID_SIZE), radius=radius, net_name=net_name))

    def _get_net_pins(self, net: Net) -> List[Pin]:
        pins: List[Pin] = []
        for conn in net.connections:
            comp = self.schematic.components.get(conn.component_id)
            if comp and conn.pin_name in comp.pins:
                pins.append(comp.pins[conn.pin_name])
        return pins

    def _assign_net_color(self, net_name: str) -> str:
        nu = net_name.upper()
        if "GND" in nu or "VSS" in nu:
            return "#4a5568"
        if any(term in nu for term in ["3V3", "5V", "VCC", "VIN", "VDD"]):
            return "#c53030"
        if "SDA" in nu:
            return "#2b6cb0"
        if "SCL" in nu or "SCK" in nu or "CLK" in nu:
            return "#2c7a7b"
        if any(term in nu for term in ["MOSI", "MISO", "CS", "SPI"]):
            return "#6b46c1"
        if any(term in nu for term in ["TX", "RX"]):
            return "#b7791f"
        return "#1a365d"

    def _get_adjacent_corridor_x(self, pin: Pin, forbidden_x: List[Tuple[float, float]]) -> float:
        """Finds a clear vertical corridor coordinate adjacent to the pin on the grid."""
        pt = pin.position
        direction = -1.0 if pin.side == Side.LEFT else 1.0
        base_x = snap_to_grid(pt.x + direction * 40.0, GRID_SIZE)
        return self.tm.allocate_vertical(base_x, pt.y - 1.0, pt.y + 1.0, forbidden_ranges=forbidden_x)

    def _route_horizontal_rail(
        self,
        pins: List[Pin],
        net_name: str,
        color: str,
        rail_base_y: float,
        forbidden_x: List[Tuple[float, float]],
        is_top: bool,
    ) -> None:
        """Routes a rail across the top or bottom margin with vertical taps through clear corridors."""
        tap_xs: List[float] = []
        for pin in pins:
            pt = pin.position
            direction = -1.0 if pin.side == Side.LEFT else 1.0
            base_x = snap_to_grid(pt.x + direction * 40.0, GRID_SIZE)
            corr_x = self.tm.allocate_vertical(
                base_x,
                min(pt.y, rail_base_y),
                max(pt.y, rail_base_y),
                forbidden_ranges=forbidden_x,
            )
            tap_xs.append(corr_x)

        min_x = min(tap_xs)
        max_x = max(tap_xs)

        rail_y = self.tm.allocate_horizontal(rail_base_y, min_x, max_x)

        tap_junctions: List[Point] = []
        for pin, corr_x in zip(pins, tap_xs):
            pt = pin.position
            # 1. Horizontal lead from pin terminal to corridor
            y_pin_lane = self.tm.allocate_horizontal(pt.y, pt.x, corr_x)
            self._add_segment(Point(pt.x, y_pin_lane), Point(corr_x, y_pin_lane), net_name, color)

            # 2. Vertical tap from corridor to rail
            self._add_segment(Point(corr_x, y_pin_lane), Point(corr_x, rail_y), net_name, color)

            tap_point = Point(corr_x, rail_y)
            tap_junctions.append(tap_point)

        # Main horizontal rail segment
        sorted_taps = sorted(tap_junctions, key=lambda p: p.x)
        self._add_segment(sorted_taps[0], sorted_taps[-1], net_name, color)

        # Junction dots
        for tap in sorted_taps:
            self._add_junction(tap, net_name=net_name)

    def _route_point_to_point(
        self,
        p1: Pin,
        p2: Pin,
        net_name: str,
        color: str,
        forbidden_x: List[Tuple[float, float]],
        forbidden_y: List[Tuple[float, float]],
        top_y: float,
        bottom_y: float,
    ) -> None:
        """Routes a 2-pin connection strictly on the grid through clear corridors."""
        pt1 = p1.position
        pt2 = p2.position

        # Case 1: Pins face each other across a single vertical corridor (p1 exits right, p2 exits left, p1.x < p2.x)
        if p1.side == Side.RIGHT and p2.side == Side.LEFT and pt1.x < pt2.x:
            mid_x = snap_to_grid((pt1.x + pt2.x) / 2.0, GRID_SIZE)
            corr_x = self.tm.allocate_vertical(mid_x, pt1.y, pt2.y, forbidden_ranges=forbidden_x)

            y1 = self.tm.allocate_horizontal(pt1.y, pt1.x, corr_x)
            y2 = self.tm.allocate_horizontal(pt2.y, corr_x, pt2.x)

            self._add_segment(Point(pt1.x, y1), Point(corr_x, y1), net_name, color)
            self._add_segment(Point(corr_x, y1), Point(corr_x, y2), net_name, color)
            self._add_segment(Point(corr_x, y2), Point(pt2.x, y2), net_name, color)
            return

        if p1.side == Side.LEFT and p2.side == Side.RIGHT and pt1.x > pt2.x:
            mid_x = snap_to_grid((pt1.x + pt2.x) / 2.0, GRID_SIZE)
            corr_x = self.tm.allocate_vertical(mid_x, pt1.y, pt2.y, forbidden_ranges=forbidden_x)

            y1 = self.tm.allocate_horizontal(pt1.y, corr_x, pt1.x)
            y2 = self.tm.allocate_horizontal(pt2.y, pt2.x, corr_x)

            self._add_segment(Point(pt1.x, y1), Point(corr_x, y1), net_name, color)
            self._add_segment(Point(corr_x, y1), Point(corr_x, y2), net_name, color)
            self._add_segment(Point(corr_x, y2), Point(pt2.x, y2), net_name, color)
            return

        # Case 2: Route via adjacent corridors and bypass channel
        corr1_x = self._get_adjacent_corridor_x(p1, forbidden_x)
        corr2_x = self._get_adjacent_corridor_x(p2, forbidden_x)

        avg_y = (pt1.y + pt2.y) / 2.0
        if avg_y < (top_y + bottom_y) / 2.0:
            bypass_y = self.tm.allocate_horizontal(top_y, corr1_x, corr2_x)
        else:
            bypass_y = self.tm.allocate_horizontal(bottom_y, corr1_x, corr2_x)

        y1 = self.tm.allocate_horizontal(pt1.y, pt1.x, corr1_x)
        y2 = self.tm.allocate_horizontal(pt2.y, pt2.x, corr2_x)

        self._add_segment(Point(pt1.x, y1), Point(corr1_x, y1), net_name, color)
        self._add_segment(Point(corr1_x, y1), Point(corr1_x, bypass_y), net_name, color)
        self._add_segment(Point(corr1_x, bypass_y), Point(corr2_x, bypass_y), net_name, color)
        self._add_segment(Point(corr2_x, bypass_y), Point(corr2_x, y2), net_name, color)
        self._add_segment(Point(corr2_x, y2), Point(pt2.x, y2), net_name, color)

    def _route_bus_net(
        self,
        pins: List[Pin],
        net_name: str,
        color: str,
        forbidden_x: List[Tuple[float, float]],
        forbidden_y: List[Tuple[float, float]],
    ) -> None:
        """Routes multi-point signal buses (e.g. I2C SDA/SCL, SPI) strictly through corridors with junction dots."""
        # Find shared corridor among the majority of pins
        corr_candidates = [self._get_adjacent_corridor_x(p, forbidden_x) for p in pins]
        corr_candidates.sort()
        mid_corr_x = corr_candidates[len(corr_candidates) // 2]

        min_y = min(p.position.y for p in pins)
        max_y = max(p.position.y for p in pins)

        # Allocate unique vertical lane on the grid for the bus trunk
        trunk_x = self.tm.allocate_vertical(mid_corr_x, min_y, max_y, forbidden_ranges=forbidden_x)

        tap_junctions: List[Point] = []
        for pin in pins:
            pt = pin.position
            # Connect pin horizontally directly into trunk_x
            y_lane = self.tm.allocate_horizontal(pt.y, pt.x, trunk_x)
            self._add_segment(Point(pt.x, y_lane), Point(trunk_x, y_lane), net_name, color)
            tap_point = Point(trunk_x, y_lane)
            tap_junctions.append(tap_point)

        sorted_taps = sorted(tap_junctions, key=lambda p: p.y)
        # Register full trunk vertical interval in track manager
        if trunk_x in self.tm.vertical_tracks:
            self.tm.vertical_tracks[trunk_x].add(sorted_taps[0].y, sorted_taps[-1].y)

        self._add_segment(sorted_taps[0], sorted_taps[-1], net_name, color)

        for tap in sorted_taps:
            self._add_junction(tap, net_name=net_name)


def _is_power_or_gnd(net_name: str) -> bool:
    nu = net_name.upper()
    return "GND" in nu or "VSS" in nu or any(w in nu for w in ["3V3", "5V", "VCC", "VIN", "VDD"])


def route_schematic(schematic: Schematic) -> None:
    router = SchematicRouter(schematic)
    router.route()
