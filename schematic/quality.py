"""Structural quality metrics for a placed and routed schematic.

This module is the single source of truth for how good a drawing is.  It
contains no component names, kinds or other instance-specific logic: every
metric is derived from geometry and net topology only, so the same numbers
apply to ICs, discrete symbols and any future component type alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from schematic.models import Schematic, WireSegment

Point2 = Tuple[float, float]


@dataclass(frozen=True)
class ScoreWeights:
    """Weights of the drawing quality objective.

    Readability outranks compactness: crossings and bends are deliberately
    expensive, while plain wire length stays cheap.  ``detour`` punishes wire
    that wanders far beyond the minimal connection tree (loops, wraps), and
    ``cross_body`` rejects wires that cut through a component body (which can
    otherwise only appear in incrementally scored candidates).
    """

    length: float = 1.0
    bend: float = 90.0
    crossing: float = 1200.0
    bundled_bend: float = 350.0
    detour: float = 1.5
    spread: float = 0.02
    overlap: float = 100000.0
    clearance: float = 0.35
    unrouted: float = 20000.0
    cross_body: float = 20000.0


@dataclass(frozen=True)
class QualityMetrics:
    wire_length: float
    bends: int
    crossings: int
    bundled_bends: int
    detour_excess: float
    spread: float
    overlaps: int
    clearance_deficit: float
    unrouted_pins: int
    body_crossings: int = 0


def _net_terminal_points(wires: List[WireSegment], net_name: str) -> List[Point2]:
    points: List[Point2] = []
    for wire in wires:
        if wire.net_name != net_name:
            continue
        for point in (wire.start.as_tuple(), wire.end.as_tuple()):
            if point not in points:
                points.append(point)
    return points


def manhattan_mst_length(points: List[Point2]) -> float:
    """Manhattan minimum spanning tree over a point set (lower bound)."""
    remaining = list(points)
    if len(remaining) < 2:
        return 0.0
    connected = [remaining.pop(0)]
    total = 0.0
    while remaining:
        best = min(
            (
                (abs(a[0] - b[0]) + abs(a[1] - b[1]), index)
                for a in connected
                for index, b in enumerate(remaining)
            ),
            key=lambda item: (item[0], item[1]),
        )
        distance, index = best
        total += distance
        connected.append(remaining.pop(index))
    return total


def _mst_length(points: List[Point2]) -> float:
    return manhattan_mst_length(points)


def _bend_and_endpoint_metrics(schematic: Schematic, wires: List[WireSegment]):
    """Return (bends, bundled_bends) from the merged wire graph.

    A bend is a node where exactly two wire ends meet at 90 degrees.  Nets
    whose terminal component sets coincide with another net form a bundle;
    bends on such nets are tracked separately because they disturb the
    parallel run of the bundle.
    """
    adjacency: Dict[Point2, Set[Point2]] = {}
    for wire in wires:
        if wire.start == wire.end:
            continue
        a, b = wire.start.as_tuple(), wire.end.as_tuple()
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    endpoint_groups: Dict[Tuple[str, ...], List[str]] = {}
    for net in schematic.nets:
        endpoint_ids = tuple(sorted({c.component_id for c in net.connections}))
        if len(endpoint_ids) >= 2:
            endpoint_groups.setdefault(endpoint_ids, []).append(net.name)
    bundled_nets = {
        name for names in endpoint_groups.values() if len(names) >= 2 for name in names
    }

    wire_net: Dict[Point2, Set[str]] = {}
    for wire in wires:
        for point in (wire.start.as_tuple(), wire.end.as_tuple()):
            wire_net.setdefault(point, set()).add(wire.net_name)

    bends = 0
    bundled_bends = 0
    for node, neighbors in adjacency.items():
        if len(neighbors) != 2:
            continue
        (ax, ay), (bx, by) = neighbors
        if (ax == node[0]) != (bx == node[0]):
            bends += 1
            if wire_net.get(node, set()) & bundled_nets:
                bundled_bends += 1
    return bends, bundled_bends


def _crossing_count(wires: List[WireSegment]) -> int:
    """Distinct orthogonal crossings between wires of different nets."""
    horizontal = []
    vertical = []
    for wire in wires:
        if wire.start == wire.end:
            continue
        if wire.is_horizontal:
            horizontal.append(wire)
        elif wire.is_vertical:
            vertical.append(wire)
    crossings: Set[Tuple[str, str, float, float]] = set()
    for h in horizontal:
        hy = h.start.y
        h_min, h_max = sorted((h.start.x, h.end.x))
        for v in vertical:
            if h.net_name == v.net_name:
                continue
            vx = v.start.x
            v_min, v_max = sorted((v.start.y, v.end.y))
            if h_min <= vx <= h_max and v_min <= hy <= v_max:
                a, b = sorted((h.net_name, v.net_name))
                crossings.add((a, b, vx, hy))
    return len(crossings)


def _unrouted_pin_count(schematic: Schematic, wires: List[WireSegment]) -> int:
    """Pins of a net that no wire of that net touches."""
    unrouted = 0
    for net in schematic.nets:
        if len(net.connections) < 2:
            continue
        nodes = {
            point
            for wire in wires
            if wire.net_name == net.name
            for point in (wire.start.as_tuple(), wire.end.as_tuple())
        }
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            if not comp:
                continue
            pin = comp.pins.get(conn.pin_id) if conn.pin_id else comp.get_pin(conn.pin_name)
            if pin and pin.position.as_tuple() not in nodes:
                unrouted += 1
    return unrouted


def _mst_lower_bound(schematic: Schematic) -> Dict[str, float]:
    """Manhattan MST length over the *pin* positions of every net."""
    bounds: Dict[str, float] = {}
    for net in schematic.nets:
        points: List[Point2] = []
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            if not comp:
                continue
            pin = comp.pins.get(conn.pin_id) if conn.pin_id else comp.get_pin(conn.pin_name)
            if pin:
                xy = pin.position.as_tuple()
                if xy not in points:
                    points.append(xy)
        bounds[net.name] = _mst_length(points)
    return bounds


def _detour_excess(schematic: Schematic, wires: List[WireSegment]) -> float:
    """Routed wire length beyond the minimal connection tree, summed per net."""
    routed: Dict[str, float] = {}
    for wire in wires:
        routed[wire.net_name] = routed.get(wire.net_name, 0.0) + wire.start.manhattan_to(wire.end)
    bounds = _mst_lower_bound(schematic)
    return sum(max(0.0, length - bounds.get(name, 0.0)) for name, length in routed.items())


MIN_CLEARANCE = 40.0


def clearance_deficit(schematic: Schematic, min_gap: float = MIN_CLEARANCE) -> float:
    """Squared shortfall of body-to-body clearance, summed over all pairs."""
    comps = list(schematic.components.values())
    deficit = 0.0
    for i, a in enumerate(comps):
        for b in comps[i + 1 :]:
            dx = max(a.x - (b.x + b.width), b.x - (a.x + a.width), 0.0)
            dy = max(a.y - (b.y + b.height), b.y - (a.y + a.height), 0.0)
            gap = (dx * dx + dy * dy) ** 0.5
            if gap < min_gap:
                deficit += (min_gap - gap) ** 2
    return deficit


def overlap_count(schematic: Schematic) -> int:
    comps = list(schematic.components.values())
    count = 0
    for i, a in enumerate(comps):
        for b in comps[i + 1 :]:
            if a.rect.intersects(b.rect):
                count += 1
    return count


def _wire_body_crossings(schematic: Schematic, wires: List[WireSegment]) -> int:
    """Wire segments whose interior passes through a component body.

    A correct full route never produces these; the metric exists so that
    incrementally scored candidates, which keep foreign wires fixed, cannot
    silently place a body on top of an existing wire.
    """
    violations = 0
    for wire in wires:
        if wire.start == wire.end:
            continue
        x0, x1 = sorted((wire.start.x, wire.end.x))
        y0, y1 = sorted((wire.start.y, wire.end.y))
        for comp in schematic.components.values():
            left, right = comp.x, comp.x + comp.width
            top, bottom = comp.y, comp.y + comp.height
            if x0 == x1:  # vertical
                if left < x0 < right and max(y0, top) < min(y1, bottom):
                    violations += 1
            elif y0 == y1:  # horizontal
                if top < y0 < bottom and max(x0, left) < min(x1, right):
                    violations += 1
    return violations


def compute_metrics(schematic: Schematic, wires: Optional[List[WireSegment]] = None) -> QualityMetrics:
    """Compute every structural metric for the current drawing.

    ``wires`` may carry a candidate wire set (for example a mix of fixed
    foreign wires and incrementally routed local nets) so search candidates
    can be scored without a full reroute.  By default the schematic's own
    wires are used.
    """
    wire_list = wires if wires is not None else schematic.wires
    comps = list(schematic.components.values())
    if not comps:
        return QualityMetrics(0.0, 0, 0, 0, 0.0, 0.0, 0, 0.0, 0, 0)
    wire_length = sum(w.start.manhattan_to(w.end) for w in wire_list)
    bends, bundled_bends = _bend_and_endpoint_metrics(schematic, wire_list)
    min_x = min(c.x for c in comps)
    min_y = min(c.y for c in comps)
    max_x = max(c.x + c.width for c in comps)
    max_y = max(c.y + c.height for c in comps)
    return QualityMetrics(
        wire_length=wire_length,
        bends=bends,
        crossings=_crossing_count(wire_list),
        bundled_bends=bundled_bends,
        detour_excess=_detour_excess(schematic, wire_list),
        spread=(max_x - min_x) + (max_y - min_y),
        overlaps=overlap_count(schematic),
        clearance_deficit=clearance_deficit(schematic),
        unrouted_pins=_unrouted_pin_count(schematic, wire_list),
        body_crossings=_wire_body_crossings(schematic, wire_list),
    )


def quality_score(metrics: QualityMetrics, weights: Optional[ScoreWeights] = None) -> float:
    """Map metrics to a single scalar where lower is better."""
    w = weights or ScoreWeights()
    return (
        w.length * metrics.wire_length
        + w.bend * metrics.bends
        + w.crossing * metrics.crossings
        + w.bundled_bend * metrics.bundled_bends
        + w.detour * metrics.detour_excess
        + w.spread * metrics.spread
        + w.overlap * metrics.overlaps
        + w.clearance * metrics.clearance_deficit
        + w.unrouted * metrics.unrouted_pins
        + w.cross_body * metrics.body_crossings
    )


def format_metrics(metrics: QualityMetrics) -> str:
    return (
        f"len={metrics.wire_length:.0f} bends={metrics.bends} "
        f"cross={metrics.crossings} bundled={metrics.bundled_bends} "
        f"detour={metrics.detour_excess:.0f} spread={metrics.spread:.0f} "
        f"overlaps={metrics.overlaps} unrouted={metrics.unrouted_pins} "
        f"body_cross={metrics.body_crossings}"
    )
