"""Objective-driven placement engine with one uniform component model.

Every component — IC, module, resistor, capacitor, switch, fuse — is the same
kind of object for this engine: a body with pins, a position and an
orientation.  Placement is a local search over exactly these degrees of
freedom, and every candidate is scored with the structural objective in
:mod:`schematic.quality`, measured on the actually routed drawing.

There are no component-class placement rules: symbolic parts simply have
smaller bodies, and whether a part ends up inline in a wire, as a branch next
to one or beside an IC emerges from the same objective as everything else.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Sequence, Set, Tuple

from schematic.models import (
    GRID_SIZE,
    Component,
    Net,
    NetConnection,
    Pin,
    Point,
    Schematic,
    Side,
    WireJunction,
    WireSegment,
    snap_to_grid,
)
from schematic.layout.pin_allocator import (
    allocate_pins_and_dimensions,
    mirror_component,
    resolve_equivalent_pins,
)
from schematic.layout.router import SchematicRouter
from schematic.quality import (
    QualityMetrics,
    clearance_deficit,
    compute_metrics,
    manhattan_mst_length,
    overlap_count,
    quality_score,
)

MARGIN = 100.0
COLUMN_GAP = 240.0
ROW_GAP = 100.0
MAX_PASSES = 4
PIN_ALIGN_PEERS = 2
LONG_PROBE = 320.0
SHORTLIST_SIZE = 5


class PlacementEngine:
    """Local search over (x, y, orientation) of every component."""

    def __init__(self, schematic: Schematic, seed_count: Optional[int] = None):
        self.schematic = schematic
        count = len(schematic.components)
        self.seed_count = seed_count if seed_count is not None else (3 if count <= 6 else 2)
        self.current_wires: List[WireSegment] = []
        self.current_junctions: List[WireJunction] = []
        self.current_metrics: Optional[QualityMetrics] = None
        self.current_score: float = math.inf

    # ------------------------------------------------------------------ API
    def run(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            self.schematic.width = 600.0
            self.schematic.height = 400.0
            return

        allocate_pins_and_dimensions(self.schematic, use_geometry=False)
        if len(comps) == 1:
            comps[0].x = MARGIN
            comps[0].y = MARGIN
            allocate_pins_and_dimensions(self.schematic, use_geometry=True)
            resolve_equivalent_pins(self.schematic)
            self._route_full()
            self._finalize_bounds()
            return

        best_snapshot = None
        best_score = math.inf
        for seed in range(self.seed_count):
            self._build_seed_layout(seed)
            self._local_search()
            score = self._route_full()
            if score < best_score:
                best_score = score
                best_snapshot = self._snapshot()

        if best_snapshot is not None:
            self._restore(best_snapshot)
            resolve_equivalent_pins(self.schematic)
            allocate_pins_and_dimensions(self.schematic, use_geometry=True, redistribute_sides=False)
            self._route_full()
        self._finalize_bounds()

    # ----------------------------------------------------------- seed layout
    def _build_seed_layout(self, seed: int) -> None:
        schematic = self.schematic
        rng = random.Random(seed)
        allocate_pins_and_dimensions(schematic, use_geometry=False)

        comps = list(schematic.components.values())
        graph = self._build_graph()
        hub = max(comps, key=lambda c: (len(c.pins) * 2 + self._degree(graph, c.id), len(c.type_name)))
        hub.x = MARGIN + 1200.0
        hub.y = MARGIN + 80.0
        allocate_pins_and_dimensions(schematic, use_geometry=True)

        levels, sides = self._levels_and_sides(hub, graph)
        ideal_y = self._ideal_rows(hub, graph, levels)

        order = sorted((c.id for c in comps), key=lambda cid: (levels.get(cid, 0), sides.get(cid, Side.LEFT).value, ideal_y.get(cid, 0.0), cid))
        lane: Dict[str, int] = {}
        for side in (Side.LEFT, Side.RIGHT):
            for depth in sorted({levels[cid] for cid in order if sides[cid] == side}):
                members = [cid for cid in order if sides[cid] == side and levels[cid] == depth]
                members.sort(key=lambda cid: (-graph[cid].get(hub.id, 0.0), ideal_y.get(cid, 0.0), cid))
                lane_ends: List[float] = []
                for cid in members:
                    comp = schematic.components[cid]
                    desired_top = ideal_y.get(cid, hub.center.y) - comp.height / 2.0
                    free = [i for i, end in enumerate(lane_ends) if desired_top >= end + ROW_GAP]
                    if free:
                        chosen = min(free, key=lambda i: abs(lane_ends[i] - desired_top))
                    elif len(lane_ends) < 2:
                        chosen = len(lane_ends)
                        lane_ends.append(-1e9)
                    else:
                        chosen = min(range(2), key=lambda i: max(lane_ends[i] + ROW_GAP, desired_top) - desired_top)
                    lane[cid] = chosen
                    lane_ends[chosen] = max(lane_ends[chosen] + ROW_GAP, desired_top) + comp.height

        slots: Dict[Tuple[Side, int, int], List[str]] = {}
        for cid in order:
            slots.setdefault((sides[cid], levels[cid], lane[cid]), []).append(cid)

        left_keys = sorted((k for k in slots if k[0] == Side.LEFT), key=lambda k: (k[1], k[2]))
        right_keys = sorted((k for k in slots if k[0] == Side.RIGHT), key=lambda k: (k[1], k[2]))
        left_width = sum(max(schematic.components[cid].width for cid in slots[key]) + COLUMN_GAP for key in left_keys)
        hub.x = snap_to_grid(MARGIN + left_width + 80.0)

        def set_columns(keys: Sequence[Tuple[Side, int, int]], side: Side) -> None:
            cursor = hub.x if side == Side.LEFT else hub.x + hub.width
            for key in keys:
                width = max(schematic.components[cid].width for cid in slots[key])
                if side == Side.LEFT:
                    cursor -= COLUMN_GAP + width
                    x = cursor
                else:
                    x = cursor + COLUMN_GAP
                    cursor = x + width
                for cid in sorted(slots[key]):
                    comp = schematic.components[cid]
                    jitter = rng.choice((-40.0, 0.0, 40.0)) if seed else 0.0
                    comp.x = snap_to_grid(max(MARGIN, x + jitter))

        set_columns(left_keys, Side.LEFT)
        set_columns(right_keys, Side.RIGHT)

        placed: Dict[int, List[Component]] = {}
        column_members: Dict[Tuple[Side, int, int], List[str]] = {}
        for cid in order:
            column_members.setdefault((sides[cid], levels[cid], lane[cid]), []).append(cid)
        for key, members in column_members.items():
            members.sort(key=lambda cid: (ideal_y.get(cid, 0.0), cid))
            column: List[Component] = []
            for cid in members:
                comp = schematic.components[cid]
                desired = ideal_y.get(cid, hub.center.y) - comp.height / 2.0
                if seed:
                    desired += rng.choice((-80.0, 0.0, 80.0))
                candidates = [snap_to_grid(max(MARGIN, desired + delta)) for radius in range(0, 2001, 20) for delta in ((0.0,) if radius == 0 else (-radius, radius))]
                valid = [
                    y
                    for y in candidates
                    if all(not self._too_close(comp.x, y, comp, other) for other in column)
                ]
                comp.y = min(valid, key=lambda y: (abs(y - desired), y)) if valid else snap_to_grid(max(MARGIN, desired))
                column.append(comp)

        # Orientation diversity across seeds: a mirrored seed explores the
        # alternative terminal directions from the very first move.
        if seed == 1:
            for comp in sorted(comps, key=lambda c: c.id):
                if len(comp.pins) == 2:
                    mirror_component(comp)
        elif seed >= 2:
            for index, comp in enumerate(sorted(comps, key=lambda c: c.id)):
                if index % 2 == 0:
                    mirror_component(comp)

        allocate_pins_and_dimensions(schematic, use_geometry=True)
        resolve_equivalent_pins(schematic)
        allocate_pins_and_dimensions(schematic, use_geometry=True, redistribute_sides=False)

    def _build_graph(self) -> Dict[str, Dict[str, float]]:
        graph: Dict[str, Dict[str, float]] = {cid: {} for cid in sorted(self.schematic.components)}
        for net in self.schematic.nets:
            members = sorted({c.component_id for c in net.connections if c.component_id in graph})
            if len(members) < 2:
                continue
            weight = 1.0 / (len(members) - 1)
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    graph[a][b] = graph[a].get(b, 0.0) + weight
                    graph[b][a] = graph[b].get(a, 0.0) + weight
        return graph

    def _degree(self, graph: Dict[str, Dict[str, float]], cid: str) -> float:
        return sum(graph.get(cid, {}).values())

    def _levels_and_sides(self, hub: Component, graph: Dict[str, Dict[str, float]]):
        levels = {hub.id: 0}
        sides: Dict[str, Side] = {hub.id: Side.LEFT}

        votes: Dict[str, Dict[Side, float]] = {}
        for net in self.schematic.nets:
            hub_conns = [c for c in net.connections if c.component_id == hub.id]
            peers = [c for c in net.connections if c.component_id != hub.id and c.component_id in graph]
            for hub_conn in hub_conns:
                pin = self._pin_of(hub_conn)
                side = pin.side if pin else Side.LEFT
                for conn in peers:
                    vote = votes.setdefault(conn.component_id, {})
                    vote[side] = vote.get(side, 0.0) + 1.0 / max(1, len(peers))
        for cid, vote in votes.items():
            sides[cid] = max(vote, key=vote.get)

        frontier = [hub.id]
        while frontier:
            current = frontier.pop(0)
            for neighbor in sorted(graph.get(current, {})):
                if neighbor not in levels:
                    levels[neighbor] = levels[current] + 1
                    frontier.append(neighbor)
        for cid in sorted(graph):
            if cid not in levels:
                levels[cid] = max(levels.values(), default=0) + 1

        pending = [cid for cid in sorted(graph) if cid != hub.id and cid not in sides]
        while pending:
            progressed = False
            for cid in pending[:]:
                neighbor_votes = {Side.LEFT: 0.0, Side.RIGHT: 0.0}
                for peer, weight in sorted(graph[cid].items()):
                    if peer in sides:
                        neighbor_votes[sides[peer]] += weight
                if sum(neighbor_votes.values()) > 0:
                    sides[cid] = max(neighbor_votes, key=neighbor_votes.get)
                    pending.remove(cid)
                    progressed = True
            if not progressed:
                cid = pending.pop(0)
                left = sum(1 for side in sides.values() if side == Side.LEFT)
                right = sum(1 for side in sides.values() if side == Side.RIGHT)
                sides[cid] = Side.LEFT if left <= right else Side.RIGHT
        return levels, sides

    def _ideal_rows(self, hub: Component, graph: Dict[str, Dict[str, float]], levels: Dict[str, int]) -> Dict[str, float]:
        # anchored values from direct hub links
        direct: Dict[str, List[float]] = {}
        for net in self.schematic.nets:
            hub_conns = [c for c in net.connections if c.component_id == hub.id]
            other_ids = {c.component_id for c in net.connections if c.component_id != hub.id}
            for conn in hub_conns:
                pin = self._pin_of(conn)
                if pin is None:
                    continue
                for cid in other_ids:
                    direct.setdefault(cid, []).append(pin.position.y)
        rows: Dict[str, float] = {cid: sum(ys) / len(ys) for cid, ys in direct.items() if ys}
        for _ in range(len(graph) + 1):
            changed = False
            for cid in sorted(graph):
                if cid in rows or cid == hub.id:
                    continue
                peers = [(rows[peer], weight) for peer, weight in sorted(graph[cid].items()) if peer in rows]
                if peers:
                    rows[cid] = sum(y * w for y, w in peers) / sum(w for _, w in peers)
                    changed = True
            if not changed:
                break
        for cid in graph:
            rows.setdefault(cid, hub.center.y + (levels[cid] % 3 - 1) * 160.0)
        return rows

    @staticmethod
    def _too_close(x: float, y: float, comp: Component, other: Component) -> bool:
        return not (
            x + comp.width + ROW_GAP <= other.x
            or other.x + other.width + ROW_GAP <= x
            or y + comp.height + ROW_GAP <= other.y
            or other.y + other.height + ROW_GAP <= y
        )

    # -------------------------------------------------------- local search
    def _local_search(self) -> None:
        self._route_full()
        for _ in range(MAX_PASSES):
            improved = False
            for comp in self._visit_order():
                if self._try_moves(comp):
                    improved = True
            if not improved:
                break

    def _visit_order(self) -> List[Component]:
        return sorted(
            self.schematic.components.values(),
            key=lambda c: (-len(c.pins), c.y, c.x, c.id),
        )

    def _try_moves(self, comp: Component) -> bool:
        origin = (comp.x, comp.y)
        moves = self._propose_moves(comp)
        if not moves:
            return False

        ranked: List[Tuple[float, Tuple[float, float, bool]]] = []
        for move in moves:
            self._apply(comp, origin, move)
            self._reallocate()
            ranked.append((self._proxy_score(), move))
        self._apply(comp, origin, (0.0, 0.0, False))
        self._reallocate()

        ranked.sort(key=lambda item: (item[0], abs(item[1][0]) + abs(item[1][1]), item[1][1], item[1][0], item[1][2]))
        shortlist: List[Tuple[float, Tuple[float, float, bool]]] = []

        def include(item: Optional[Tuple[float, Tuple[float, float, bool]]]) -> None:
            if item is not None and all(item[1] != prior[1] for prior in shortlist):
                shortlist.append(item)

        include(ranked[0] if ranked else None)
        include(next((item for item in ranked if item[1][2]), None))
        include(next((item for item in ranked if abs(item[1][0]) + abs(item[1][1]) >= LONG_PROBE), None))
        for item in ranked[1:]:
            if len(shortlist) >= SHORTLIST_SIZE:
                break
            include(item)

        best: Optional[Tuple[float, Tuple[float, float, bool], List[WireSegment], List[WireJunction], QualityMetrics]] = None
        for _, move in shortlist:
            self._apply(comp, origin, move)
            self._reallocate()
            score, wires, junctions, metrics = self._route_incremental(comp)
            if best is None or score < best[0]:
                best = (score, move, wires, junctions, metrics)

        self._apply(comp, origin, (0.0, 0.0, False))
        self._reallocate()

        if best is not None and best[0] < self.current_score - 1e-6:
            _, move, wires, junctions, metrics = best
            self._apply(comp, origin, move)
            self._reallocate()
            self.current_wires = wires
            self.current_junctions = junctions
            self.current_metrics = metrics
            self.current_score = best[0]
            return True
        return False

    def _apply(self, comp: Component, origin: Tuple[float, float], move: Tuple[float, float, bool]) -> None:
        dx, dy, mirror = move
        comp.x = snap_to_grid(origin[0] + dx)
        comp.y = snap_to_grid(origin[1] + dy)
        if mirror:
            mirror_component(comp)

    def _reallocate(self) -> None:
        allocate_pins_and_dimensions(self.schematic, use_geometry=True, redistribute_sides=False)

    def _propose_moves(self, comp: Component) -> List[Tuple[float, float, bool]]:
        """Generic move set: orientation, peer alignment, corridors, steps.

        The same proposals are generated for every component; no rule knows
        what kind of body it is moving.
        """
        moves: Dict[Tuple[float, float, bool], None] = {}

        def add(dx: float, dy: float, mirror: bool = False) -> None:
            dx = snap_to_grid(dx)
            dy = snap_to_grid(dy)
            if dx == 0.0 and dy == 0.0 and not mirror:
                return
            if comp.x + dx < MARGIN or comp.y + dy < MARGIN:
                return
            moves[(dx, dy, mirror)] = None

        def add_center(cx: float, cy: float) -> None:
            add(cx - comp.center.x, cy - comp.center.y)

        if not any(pin.side_specified for pin in comp.pins.values()):
            add(0.0, 0.0, True)

        per_net: Dict[str, Tuple[Pin, List[Pin]]] = {}
        for net in self.schematic.nets:
            own = [c for c in net.connections if c.component_id == comp.id]
            if not own:
                continue
            pin = self._pin_of(own[0])
            if pin is None:
                continue
            peer_pins = [self._pin_of(c) for c in net.connections if c.component_id != comp.id]
            peer_pins = [p for p in peer_pins if p is not None]
            peer_pins.sort(key=lambda p: abs(p.position.x - pin.position.x) + abs(p.position.y - pin.position.y))
            per_net[net.name] = (pin, peer_pins)
            for peer in peer_pins[:PIN_ALIGN_PEERS]:
                add(0.0, peer.position.y - pin.position.y)
                add(peer.position.x - pin.position.x, 0.0)

        if len(per_net) == 2:
            (pin_a, peers_a), (pin_b, peers_b) = list(per_net.values())
            target_a = peers_a[0] if peers_a else pin_a
            target_b = peers_b[0] if peers_b else pin_b
            add_center((target_a.position.x + target_b.position.x) / 2.0, (target_a.position.y + target_b.position.y) / 2.0)
            add(0.0, target_a.position.y - pin_a.position.y)
            add(0.0, target_b.position.y - pin_b.position.y)
            add(target_a.position.x - pin_a.position.x, 0.0)
            add(target_b.position.x - pin_b.position.x, 0.0)
            horizontal = abs(target_a.position.y - target_b.position.y) <= abs(target_a.position.x - target_b.position.x)
            for fraction in (0.25, 0.4, 0.6, 0.75):
                if horizontal:
                    cx = target_a.position.x + (target_b.position.x - target_a.position.x) * fraction
                    add_center(cx, target_a.position.y)
                    add_center(cx, target_b.position.y)
                else:
                    cy = target_a.position.y + (target_b.position.y - target_a.position.y) * fraction
                    add_center(target_a.position.x, cy)
                    add_center(target_b.position.x, cy)

        for step in (80.0, 160.0, 320.0, 640.0):
            add(step, 0.0)
            add(-step, 0.0)
            add(0.0, step)
            add(0.0, -step)
        return list(moves)

    def _proxy_score(self) -> float:
        """Cheap screening score: minimal wiring plus body separation."""
        total = 0.0
        for net in self.schematic.nets:
            points: List[Tuple[float, float]] = []
            for conn in net.connections:
                pin = self._pin_of(conn)
                if pin is not None:
                    xy = pin.position.as_tuple()
                    if xy not in points:
                        points.append(xy)
            total += manhattan_mst_length(points)
        total += 0.5 * clearance_deficit(self.schematic)
        total += 100000.0 * overlap_count(self.schematic)
        return total

    # ------------------------------------------------------------- routing
    def _route_full(self) -> float:
        self._reallocate()
        router = SchematicRouter(self.schematic)
        router.route()
        self.current_wires = list(self.schematic.wires)
        self.current_junctions = list(self.schematic.junctions)
        self.current_metrics = compute_metrics(self.schematic)
        self.current_score = quality_score(self.current_metrics)
        return self.current_score

    def _route_incremental(self, comp: Component):
        """Re-route only the nets of ``comp``; every other wire stays fixed."""
        affected = self._affected_nets(comp)
        kept_wires = [w for w in self.current_wires if w.net_name not in affected]
        kept_junctions = [j for j in self.current_junctions if j.net_name not in affected]
        router = SchematicRouter(self.schematic)
        router.prepare()
        router.load_existing_routes(kept_wires, kept_junctions)
        router.route_nets([net for net in router.sorted_nets if net.name in affected])
        wires = list(router.segments)
        metrics = compute_metrics(self.schematic, wires=wires)
        return quality_score(metrics), wires, list(router.junctions), metrics

    def _affected_nets(self, comp: Component) -> Set[str]:
        return {
            net.name
            for net in self.schematic.nets
            if any(conn.component_id == comp.id for conn in net.connections)
        }

    # ------------------------------------------------------------ snapshots
    def _snapshot(self):
        return {
            "pos": {cid: (comp.x, comp.y) for cid, comp in self.schematic.components.items()},
            "sides": {
                cid: {pin_id: pin.side for pin_id, pin in comp.pins.items()}
                for cid, comp in self.schematic.components.items()
            },
        }

    def _restore(self, snapshot) -> None:
        for cid, (x, y) in snapshot["pos"].items():
            comp = self.schematic.components.get(cid)
            if comp is not None:
                comp.x = x
                comp.y = y
        for cid, sides in snapshot["sides"].items():
            comp = self.schematic.components.get(cid)
            if comp is None:
                continue
            for pin_id, side in sides.items():
                if pin_id in comp.pins:
                    comp.pins[pin_id].side = side

    # -------------------------------------------------------------- helpers
    def _pin_of(self, conn: NetConnection) -> Optional[Pin]:
        comp = self.schematic.components.get(conn.component_id)
        if not comp:
            return None
        return comp.pins.get(conn.pin_id) if conn.pin_id else comp.get_pin(conn.pin_name)

    def _finalize_bounds(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            return
        self.schematic.width = max(max(c.x + c.width for c in comps) + MARGIN, 850.0)
        self.schematic.height = max(max(c.y + c.height for c in comps) + MARGIN, 600.0)


def place_schematic(schematic: Schematic) -> None:
    """Place and route a schematic with the uniform objective-driven engine."""
    PlacementEngine(schematic).run()
