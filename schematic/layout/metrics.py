"""Quality metrics and readability scoring for routed schematics.

The metrics are computed purely from the final geometry (components, wire
segments, junctions), so they can score the output of *any* engine version --
this is what the before/after comparison tooling relies on.

All wire geometry is rasterised onto the routing grid (``GRID_SIZE``) as unit
edges.  Every engine in this project emits grid-aligned orthogonal wires, which
makes crossing, overlap and spacing detection exact and cheap.

The readability score is ``100 * exp(-P)`` where ``P`` is a weighted sum of
per-connection penalty rates (see ``ScoreWeights``).  Hard violations (wrong or
missing connectivity, overlaps, ambiguous touches) halve the score so that an
invalid drawing can never out-score a valid one.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from schematic.models import GRID_SIZE, Schematic
from schematic.layout.semantics import Semantics, analyze

Node = Tuple[int, int]
Edge = Tuple[Node, Node]

_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass
class ScoreWeights:
    """Weights of the readability penalty ``P`` (per connection unless noted)."""

    crossings: float = 0.45
    bends: float = 0.12
    small_jogs: float = 0.6
    near_crossings: float = 0.35
    tight_parallel_pairs: float = 0.25
    dense_regions: float = 0.25
    detour: float = 0.6            # applied to (detour_ratio - 1), not per connection
    bundle_incoherence: float = 0.5  # applied to (1 - parallelism_score)
    violation_factor: float = 0.5  # multiplier when any hard violation exists


@dataclass
class QualityReport:
    components: int = 0
    nets: int = 0
    connections: int = 0
    width: float = 0.0
    height: float = 0.0
    area: float = 0.0
    component_area_ratio: float = 0.0
    wire_length: float = 0.0
    lower_bound_length: float = 0.0
    detour_ratio: float = 1.0
    bends: int = 0
    bends_per_connection: float = 0.0
    max_bends_single_net: int = 0
    crossings: int = 0
    crossings_per_connection: float = 0.0
    near_crossings: int = 0
    acute_angles: int = 0
    small_jogs: int = 0
    junctions: int = 0
    min_spacing: Optional[float] = None
    avg_spacing: Optional[float] = None
    tight_parallel_pairs: int = 0
    bundles: int = 0
    bundled_nets: int = 0
    parallelism_score: Optional[float] = None
    component_overlaps: int = 0
    min_component_gap: Optional[float] = None
    dense_regions: int = 0
    violations: Dict[str, int] = field(default_factory=dict)
    readability_score: float = 0.0

    @property
    def violation_count(self) -> int:
        return sum(self.violations.values())

    def as_dict(self) -> dict:
        d = asdict(self)
        d["violation_count"] = self.violation_count
        return d

    def format_table(self) -> str:
        rows = [
            ("Readability score", f"{self.readability_score:.1f} / 100"),
            ("Components / nets / connections", f"{self.components} / {self.nets} / {self.connections}"),
            ("Drawing size", f"{self.width:.0f} x {self.height:.0f} px"),
            ("Total wire length", f"{self.wire_length:.0f} px (detour ratio {self.detour_ratio:.2f})"),
            ("Bends", f"{self.bends} ({self.bends_per_connection:.2f} per connection, max {self.max_bends_single_net} in one net)"),
            ("Crossings", f"{self.crossings} ({self.crossings_per_connection:.2f} per connection)"),
            ("Near-crossings", str(self.near_crossings)),
            ("Acute angles", str(self.acute_angles)),
            ("Small zig-zag jogs", str(self.small_jogs)),
            ("Junction dots", str(self.junctions)),
            ("Wire spacing min / avg", f"{_fmt(self.min_spacing)} / {_fmt(self.avg_spacing)} px"),
            ("Tight unrelated parallels", str(self.tight_parallel_pairs)),
            ("Bundles / bundled nets", f"{self.bundles} / {self.bundled_nets}"),
            ("Parallelism score", _fmt(self.parallelism_score, 2)),
            ("Component overlaps", str(self.component_overlaps)),
            ("Min component gap", f"{_fmt(self.min_component_gap)} px"),
            ("Dense regions", str(self.dense_regions)),
            ("Routing violations", ", ".join(f"{k}={v}" for k, v in self.violations.items() if v) or "none"),
        ]
        width = max(len(r[0]) for r in rows)
        return "\n".join(f"{k.ljust(width)}  {v}" for k, v in rows)


def _fmt(v: Optional[float], digits: int = 0) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def _g(v: float) -> Tuple[int, bool]:
    q = v / GRID_SIZE
    r = round(q)
    return int(r), abs(q - r) > 1e-6


class _NetGeometry:
    """Rasterised wire graph of one net."""

    def __init__(self, name: str):
        self.name = name
        self.edges: Set[Edge] = set()
        self.adj: Dict[Node, Set[Tuple[int, int]]] = {}

    def add_edge(self, a: Node, b: Node) -> None:
        e = (a, b) if a <= b else (b, a)
        if e in self.edges:
            return
        self.edges.add(e)
        self.adj.setdefault(a, set()).add((b[0] - a[0], b[1] - a[1]))
        self.adj.setdefault(b, set()).add((a[0] - b[0], a[1] - b[1]))

    def shape(self, n: Node) -> str:
        dirs = self.adj.get(n)
        if not dirs:
            return ""
        if len(dirs) == 1:
            return "E"
        if len(dirs) == 2:
            (ax, ay), (bx, by) = sorted(dirs)
            if ax + bx == 0 and ay + by == 0:
                return "H" if ay == 0 else "V"
            return "B"
        return "J"


def _rasterize(schematic: Schematic) -> Tuple[Dict[str, _NetGeometry], int, int]:
    nets: Dict[str, _NetGeometry] = {}
    off_grid = 0
    diagonal = 0
    for w in schematic.wires:
        geo = nets.setdefault(w.net_name, _NetGeometry(w.net_name))
        (x1, o1), (y1, o2) = _g(w.start.x), _g(w.start.y)
        (x2, o3), (y2, o4) = _g(w.end.x), _g(w.end.y)
        if o1 or o2 or o3 or o4:
            off_grid += 1
        if x1 != x2 and y1 != y2:
            diagonal += 1
            continue
        if x1 == x2 and y1 == y2:
            continue
        if y1 == y2:
            lo, hi = sorted((x1, x2))
            for x in range(lo, hi):
                geo.add_edge((x, y1), (x + 1, y1))
        else:
            lo, hi = sorted((y1, y2))
            for y in range(lo, hi):
                geo.add_edge((x1, y), (x1, y + 1))
    return nets, off_grid, diagonal


def _pin_terminals(schematic: Schematic) -> Tuple[Dict[str, List[Node]], Dict[Node, Set[str]]]:
    """Terminal grid nodes per net, and the set of nets allowed at each terminal."""
    per_net: Dict[str, List[Node]] = {}
    allowed: Dict[Node, Set[str]] = {}
    for c in schematic.components.values():
        for p in c.pins.values():
            node = (_g(p.position.x)[0], _g(p.position.y)[0])
            allowed.setdefault(node, set())
    for net in schematic.nets:
        nodes: List[Node] = []
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            if comp is None:
                continue
            pin = comp.pins.get(conn.pin_id) if conn.pin_id else None
            if pin is None:
                pin = comp.get_pin(conn.pin_name)
            if pin is None:
                continue
            node = (_g(pin.position.x)[0], _g(pin.position.y)[0])
            if node not in nodes:
                nodes.append(node)
            allowed.setdefault(node, set()).add(net.name)
        per_net[net.name] = nodes
    return per_net, allowed


def _count_acute_angles(schematic: Schematic) -> int:
    """Counts pairs of same-net segments meeting at an angle below 90 degrees."""
    ends: Dict[Tuple[str, float, float], List[Tuple[float, float]]] = {}
    for w in schematic.wires:
        dx, dy = w.end.x - w.start.x, w.end.y - w.start.y
        if dx == 0 and dy == 0:
            continue
        ends.setdefault((w.net_name, round(w.start.x, 2), round(w.start.y, 2)), []).append((dx, dy))
        ends.setdefault((w.net_name, round(w.end.x, 2), round(w.end.y, 2)), []).append((-dx, -dy))
    acute = 0
    for vecs in ends.values():
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                (ax, ay), (bx, by) = vecs[i], vecs[j]
                cos = (ax * bx + ay * by) / (math.hypot(ax, ay) * math.hypot(bx, by))
                if 1e-6 < cos < 1.0 - 1e-6:  # strictly between 0 and 90 degrees
                    acute += 1
    return acute


def _mst_length(points: List[Node]) -> float:
    if len(points) < 2:
        return 0.0
    in_tree = {0}
    best = {i: abs(points[i][0] - points[0][0]) + abs(points[i][1] - points[0][1]) for i in range(1, len(points))}
    total = 0.0
    while best:
        i = min(best, key=lambda k: (best[k], k))
        total += best.pop(i)
        in_tree.add(i)
        for j in best:
            d = abs(points[i][0] - points[j][0]) + abs(points[i][1] - points[j][1])
            if d < best[j]:
                best[j] = d
    return total


def evaluate(
    schematic: Schematic,
    semantics: Optional[Semantics] = None,
    weights: Optional[ScoreWeights] = None,
) -> QualityReport:
    """Computes the full quality report for a placed and routed schematic."""
    sem = semantics or analyze(schematic)
    weights = weights or ScoreWeights()
    rep = QualityReport()
    rep.components = len(schematic.components)
    nets, off_grid, diagonal = _rasterize(schematic)
    terminals, allowed_at = _pin_terminals(schematic)
    violations: Dict[str, int] = {
        "unrouted_pins": 0,
        "disconnected_nets": 0,
        "wire_through_component": 0,
        "collinear_overlap": 0,
        "dual_nodes": 0,
        "t_touches": 0,
        "foreign_terminal_contact": 0,
        "diagonal_segments": diagonal,
        "off_grid_segments": off_grid,
        "component_overlaps": 0,
    }

    routable = [n for n in schematic.nets if len(terminals.get(n.name, [])) >= 2]
    rep.nets = len(routable)
    rep.connections = sum(len(terminals[n.name]) - 1 for n in routable)

    # --- Connectivity ---------------------------------------------------
    for net in routable:
        geo = nets.get(net.name)
        terms = terminals[net.name]
        if geo is None:
            violations["unrouted_pins"] += len(terms)
            violations["disconnected_nets"] += 1
            continue
        missing = [t for t in terms if t not in geo.adj]
        violations["unrouted_pins"] += len(missing)
        start = next((t for t in terms if t in geo.adj), None)
        seen: Set[Node] = set()
        if start is not None:
            stack = [start]
            seen.add(start)
            while stack:
                n = stack.pop()
                for dx, dy in geo.adj.get(n, ()):
                    m = (n[0] + dx, n[1] + dy)
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
        if missing or any(t not in seen for t in terms) or (seen and len(seen) != len(geo.adj)):
            violations["disconnected_nets"] += 1

    # --- Per-node occupancy ----------------------------------------------
    node_nets: Dict[Node, List[Tuple[str, str]]] = {}
    edge_owner: Dict[Edge, str] = {}
    for name in sorted(nets):
        geo = nets[name]
        for e in geo.edges:
            if e in edge_owner and edge_owner[e] != name:
                violations["collinear_overlap"] += 1
            edge_owner.setdefault(e, name)
        for n in geo.adj:
            node_nets.setdefault(n, []).append((name, geo.shape(n)))

    crossings = 0
    for n, occ in node_nets.items():
        allowed = allowed_at.get(n)
        if allowed is not None:
            for name, _ in occ:
                if name not in allowed:
                    violations["foreign_terminal_contact"] += 1
        if len(occ) < 2:
            continue
        straight = [s for _, s in occ if s in ("H", "V")]
        other = [s for _, s in occ if s not in ("H", "V")]
        if not other and len(occ) == 2 and set(straight) == {"H", "V"}:
            crossings += 1
        elif other and straight:
            violations["t_touches"] += 1
        else:
            violations["dual_nodes"] += 1
    rep.crossings = crossings

    # --- Wires through component bodies ---------------------------------
    rects = []
    for c in schematic.components.values():
        rects.append((c.x / GRID_SIZE, c.y / GRID_SIZE, (c.x + c.width) / GRID_SIZE, (c.y + c.height) / GRID_SIZE))
    for e in edge_owner:
        mx = (e[0][0] + e[1][0]) / 2.0
        my = (e[0][1] + e[1][1]) / 2.0
        for (x1, y1, x2, y2) in rects:
            if x1 - 1e-6 <= mx <= x2 + 1e-6 and y1 - 1e-6 <= my <= y2 + 1e-6:
                violations["wire_through_component"] += 1
                break

    # --- Bends, junctions, jogs -----------------------------------------
    total_bends = 0
    max_bends = 0
    jogs = 0
    junctions = 0
    for name in sorted(nets):
        geo = nets[name]
        nb = 0
        for n in geo.adj:
            s = geo.shape(n)
            if s == "B":
                nb += 1
            elif s == "J":
                junctions += 1
        total_bends += nb
        max_bends = max(max_bends, nb)
        # Runs of length one between two direction changes are zig-zags.
        for n in geo.adj:
            if geo.shape(n) not in ("B", "J"):
                continue
            for dx, dy in geo.adj[n]:
                m = (n[0] + dx, n[1] + dy)
                if geo.shape(m) in ("B", "J") and n < m:
                    jogs += 1
    rep.bends = total_bends
    rep.max_bends_single_net = max_bends
    rep.small_jogs = jogs
    rep.junctions = junctions

    # --- Near crossings ---------------------------------------------------
    near = 0
    for name in sorted(nets):
        geo = nets[name]
        for n, dirs in geo.adj.items():
            s = geo.shape(n)
            if s not in ("B", "J", "E"):
                continue
            for dx, dy in _DIRS:
                if (dx, dy) in dirs or (-dx, -dy) not in dirs:
                    continue
                m = (n[0] + dx, n[1] + dy)
                hit = False
                for other, oshape in node_nets.get(m, ()):
                    if other == name or sem.related(name, other):
                        continue
                    if (dx != 0 and oshape == "V") or (dy != 0 and oshape == "H"):
                        hit = True
                        break
                if hit:
                    near += 1
                    break
    rep.near_crossings = near
    rep.acute_angles = _count_acute_angles(schematic)

    # --- Spacing between parallel wires -----------------------------------
    horiz: Dict[int, Dict[int, str]] = {}
    vert: Dict[int, Dict[int, str]] = {}
    for e, name in edge_owner.items():
        (ax, ay), (bx, by) = e
        if ay == by:
            horiz.setdefault(ay, {})[ax] = name
        else:
            vert.setdefault(ax, {})[ay] = name
    # Spacing is measured between *unrelated* nets only: bundle mates running
    # at the 20 px pin pitch are intentional, unrelated wires that close are not.
    spacing_samples: List[int] = []
    tight: Dict[Tuple[str, str], int] = {}
    max_look = 10
    for lines in (horiz, vert):
        for line, cells in lines.items():
            for pos, name in cells.items():
                nearest = None
                for k in range(1, max_look + 1):
                    for sign in (-1, 1):
                        other_cells = lines.get(line + sign * k)
                        if other_cells is None:
                            continue
                        other = other_cells.get(pos)
                        if other is not None and other != name and not sem.related(name, other):
                            nearest = k
                            if k == 1:
                                key = (name, other) if name < other else (other, name)
                                tight[key] = tight.get(key, 0) + 1
                            break
                    if nearest is not None:
                        break
                if nearest is not None:
                    spacing_samples.append(nearest)
    if spacing_samples:
        rep.min_spacing = min(spacing_samples) * GRID_SIZE
        rep.avg_spacing = sum(spacing_samples) / len(spacing_samples) * GRID_SIZE
    # Each tight unit edge is seen from both sides, so >= 4 means >= 2 units.
    rep.tight_parallel_pairs = sum(1 for v in tight.values() if v >= 4)

    # --- Bundles ------------------------------------------------------------
    # Coherence of a bundle member = share of its wire that runs within two
    # grid steps of some mate (a ribbon of 11 wires is perfectly coherent even
    # though its outermost wires are far apart).
    member_scores: List[float] = []
    bundled: Set[str] = set()
    rep.bundles = len(sem.bundles)
    for bid in sorted(sem.bundles):
        members = [m for m in sem.bundles[bid].nets if m in nets]
        if len(members) < 2:
            continue
        mate_edges: Dict[str, Set[Edge]] = {m: nets[m].edges for m in members}
        for a in members:
            ga = nets[a]
            if not ga.edges:
                continue
            others = [mate_edges[m] for m in members if m != a]
            close = 0
            for e in ga.edges:
                (ax, ay), (bx, by) = e
                hit = False
                for k in (1, 2):
                    if ay == by:
                        cand = (((ax, ay + k), (bx, by + k)), ((ax, ay - k), (bx, by - k)))
                    else:
                        cand = (((ax + k, ay), (bx + k, by)), ((ax - k, ay), (bx - k, by)))
                    if any(cand[0] in o or cand[1] in o for o in others):
                        hit = True
                        break
                close += hit
            score = close / len(ga.edges)
            member_scores.append(score)
            if score >= 0.5:
                bundled.add(a)
    rep.bundled_nets = len(bundled)
    rep.parallelism_score = sum(member_scores) / len(member_scores) if member_scores else None

    # --- Lengths and detours ------------------------------------------------
    rep.wire_length = len(edge_owner) * GRID_SIZE
    lb = 0.0
    for net in routable:
        pts = terminals[net.name]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
        lb += max(hpwl, _mst_length(pts) / 1.5)
    rep.lower_bound_length = lb * GRID_SIZE
    rep.detour_ratio = rep.wire_length / rep.lower_bound_length if rep.lower_bound_length > 0 else 1.0

    # --- Components ---------------------------------------------------------
    comps = list(schematic.components.values())
    overlaps = 0
    min_gap: Optional[float] = None
    for i in range(len(comps)):
        a = comps[i]
        for b in comps[i + 1:]:
            ox = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
            oy = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
            if ox > 0 and oy > 0:
                overlaps += 1
                continue
            gap = max(-ox, -oy)
            if min_gap is None or gap < min_gap:
                min_gap = gap
    rep.component_overlaps = overlaps
    violations["component_overlaps"] = overlaps
    rep.min_component_gap = min_gap

    # --- Extent, whitespace, density --------------------------------------
    xs: List[float] = []
    ys: List[float] = []
    for c in comps:
        xs += [c.x, c.x + c.width]
        ys += [c.y, c.y + c.height]
    for e in edge_owner:
        xs += [e[0][0] * GRID_SIZE, e[1][0] * GRID_SIZE]
        ys += [e[0][1] * GRID_SIZE, e[1][1] * GRID_SIZE]
    if xs:
        rep.width = max(xs) - min(xs)
        rep.height = max(ys) - min(ys)
        rep.area = rep.width * rep.height
        if rep.area > 0:
            rep.component_area_ratio = sum(c.width * c.height for c in comps) / rep.area

    cell = 5
    cell_edges: Dict[Tuple[int, int], int] = {}
    cell_nets: Dict[Tuple[int, int], Set[str]] = {}
    for e, name in edge_owner.items():
        key = (min(e[0][0], e[1][0]) // cell, min(e[0][1], e[1][1]) // cell)
        cell_edges[key] = cell_edges.get(key, 0) + 1
        cell_nets.setdefault(key, set()).add(name)
    rep.dense_regions = sum(1 for k, v in cell_edges.items() if v >= 20 and len(cell_nets[k]) >= 6)

    rep.violations = violations
    conn = max(1, rep.connections)
    rep.bends_per_connection = rep.bends / conn
    rep.crossings_per_connection = rep.crossings / conn

    # --- Readability score ----------------------------------------------------
    penalty = (
        weights.crossings * rep.crossings
        + weights.bends * rep.bends
        + weights.small_jogs * rep.small_jogs
        + weights.near_crossings * rep.near_crossings
        + weights.tight_parallel_pairs * rep.tight_parallel_pairs
        + weights.dense_regions * rep.dense_regions
    ) / conn
    penalty += weights.detour * max(0.0, rep.detour_ratio - 1.0)
    if rep.parallelism_score is not None:
        penalty += weights.bundle_incoherence * (1.0 - rep.parallelism_score)
    score = 100.0 * math.exp(-penalty)
    if rep.violation_count:
        score *= weights.violation_factor
    rep.readability_score = round(score, 2)
    return rep


def pair_coherence(schematic: Schematic, a: str, b: str, reach: int = 2) -> float:
    """Share of the two nets' wire that runs within ``reach`` grid steps of the
    other net, in parallel (1.0 = they travel together all the way)."""
    nets, _, _ = _rasterize(schematic)
    ga, gb = nets.get(a), nets.get(b)
    if ga is None or gb is None or not ga.edges or not gb.edges:
        return 0.0

    def near(src: _NetGeometry, dst: _NetGeometry) -> int:
        hits = 0
        for (p, q) in src.edges:
            horizontal = p[1] == q[1]
            for k in range(1, reach + 1):
                for sgn in (-1, 1):
                    d = (0, sgn * k) if horizontal else (sgn * k, 0)
                    e = ((p[0] + d[0], p[1] + d[1]), (q[0] + d[0], q[1] + d[1]))
                    if e in dst.edges:
                        hits += 1
                        break
                else:
                    continue
                break
        return hits

    return (near(ga, gb) + near(gb, ga)) / (len(ga.edges) + len(gb.edges))
