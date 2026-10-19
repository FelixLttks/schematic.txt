"""Layout pipeline: netlist -> placement -> routing -> labels -> report.

``layout_schematic`` works in place on a parsed ``Schematic``:

1. merge nets that share a pin, resolve every connection to a pin;
2. per connected sub-circuit, ``restarts`` independent placements
   (analytic start + simulated annealing + polish, different seeds, run in
   parallel processes);
3. the ``routed_candidates`` placements with the lowest estimated cost are
   routed for real and scored with the same weights; the best drawing wins;
4. sub-circuits are packed side by side, unconnected parts in a row below;
5. net labels, quality report.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from schematic.models import Point, Schematic, WireJunction, WireSegment
from schematic.layout.config import DEFAULT_CONFIG, LayoutConfig
from schematic.layout.labels import place_net_labels
from schematic.layout.metrics import QualityReport, evaluate
from schematic.layout.netlist import Netlist, build_netlist, merge_shared_pin_nets
from schematic.layout.placement import Layout, PartModel, place_group
from schematic.layout.router import DX, DY, RNet, RTerminal, Router
from schematic.layout.shapes import GRID, VEC, Shape, apply_shape
from schematic.styles import net_color

Pos = Tuple[int, int]
DIR_INDEX = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}


@dataclass
class LayoutResult:
    report: QualityReport
    candidates: List[Tuple[str, float, float]] = field(default_factory=list)  # (name, estimate, routed cost)
    strategy: str = ""


@dataclass
class _Placed:
    """A complete placement: position, variant and shape of every part."""
    pos: Dict[str, Pos]
    shape: Dict[str, Shape]
    estimate: float


def _place_all(sch: Schematic, nl: Netlist, models: Dict[str, PartModel], cfg: LayoutConfig,
               seed: int) -> _Placed:
    parts = list(sch.components)
    groups = nl.groups(parts)
    pos: Dict[str, Pos] = {}
    shape: Dict[str, Shape] = {}
    estimate = 0.0
    x_cursor = 0
    bottom = 0
    for gi, g in enumerate(groups):
        layout: Layout = place_group(g, models, nl, cfg, seed * 1000 + gi)
        estimate += layout.total
        feet = [layout.foot(p) for p in g]
        gx0 = min(f[0] for f in feet)
        gy0 = min(f[1] for f in feet)
        gx1 = max(f[2] for f in feet)
        gy1 = max(f[3] for f in feet)
        for p in g:
            x, y = layout.pos[p]
            pos[p] = (x - gx0 + x_cursor, y - gy0)
            shape[p] = layout.shape[p]
        x_cursor += (gx1 - gx0) + cfg.group_gap
        bottom = max(bottom, gy1 - gy0)
    # Unconnected parts: a row below everything.
    x = 0
    y = bottom + cfg.group_gap if groups else 0
    for p in parts:
        if p in pos:
            continue
        m = models[p]
        s = m.shape(m.variants[0])
        f = s.footprint
        pos[p] = (x - f[0], y - f[1])
        shape[p] = s
        x += (f[2] - f[0]) + cfg.clearance + 2
    return _Placed(pos, shape, estimate)


def _route(sch: Schematic, nl: Netlist, placed: _Placed, cfg: LayoutConfig) -> Router:
    feet = []
    for p, (x, y) in placed.pos.items():
        f = placed.shape[p].footprint
        feet.append((x + f[0], y + f[1], x + f[2], y + f[3]))
    m = 8
    ox = min(f[0] for f in feet) - m
    oy = min(f[1] for f in feet) - m
    w = max(f[2] for f in feet) + m - ox + 1
    h = max(f[3] for f in feet) + m - oy + 1
    r = Router(w, h, ox, oy, cfg.weights)
    for p, (x, y) in placed.pos.items():
        s = placed.shape[p]
        r.block_rect(x, y, x + s.w, y + s.h)
        for b in s.text:
            r.text_rect(x + b[0], y + b[1], x + b[2], y + b[3])
    for p, (x, y) in placed.pos.items():
        s = placed.shape[p]
        for pid, ps in s.pins.items():
            n = nl.pin_net.get((p, pid), -2)
            r.add_pin(x + ps.tx, y + ps.ty, DIR_INDEX[VEC[ps.side]], n)
    for info in nl.nets:
        terms = []
        for c, pid in info.terminals:
            x, y = placed.pos[c]
            ps = placed.shape[c].pins[pid]
            terms.append(RTerminal(c, pid, x + ps.tx, y + ps.ty, DIR_INDEX[VEC[ps.side]]))
        r.nets.append(RNet(info.index, info.name, info.factor, info.rail, terms, bundle=info.bundle))
    r.route_all(cfg.reroute_passes)
    return r


def _cut_line(feet: List[Tuple[int, int, int, int]], start: int, step: int, axis: int) -> Optional[int]:
    """First coordinate from ``start`` in direction ``step`` where a cut
    across the drawing touches no footprint, along ``axis`` (0: x, 1: y)."""
    lo = min(f[axis] for f in feet) - 1
    hi = max(f[axis + 2] for f in feet) + 1
    v = start
    while lo <= v <= hi:
        if not any(f[axis] < v <= f[axis + 2] for f in feet):
            return v
        v += step
    return None


def _widen(placed: _Placed, router: Router, amount: int) -> bool:
    """Opens space where the router failed: a cut through free grid lines
    just in front of every pin that could not be connected; everything
    beyond the cut moves away.  Alignments are preserved exactly (a whole
    side of the drawing moves together) and no overlap can arise."""
    feet = {}
    for p, (x, y) in placed.pos.items():
        f = placed.shape[p].footprint
        feet[p] = (x + f[0], y + f[1], x + f[2], y + f[3])
    cuts: List[Tuple[int, int, int]] = []          # (axis, coordinate, sign)
    for net in router.nets:
        for t in net.failed_terms:
            dx, dy = DX[t.d], DY[t.d]
            if dx:
                v = _cut_line(list(feet.values()), t.x + dx * 2 + (1 if dx < 0 else 0), dx, 0)
                if v is not None:
                    cuts.append((0, v, dx))
            else:
                v = _cut_line(list(feet.values()), t.y + dy * 2 + (1 if dy < 0 else 0), dy, 1)
                if v is not None:
                    cuts.append((1, v, dy))
    if not cuts:
        return False
    for axis, v, sign in dict.fromkeys(cuts):
        for p in placed.pos:
            x, y = placed.pos[p]
            f = feet[p]
            if f[axis] >= v:
                placed.pos[p] = (x + amount, y) if axis == 0 else (x, y + amount)
        for p, (x, y) in placed.pos.items():
            f = placed.shape[p].footprint
            feet[p] = (x + f[0], y + f[1], x + f[2], y + f[3])
    return True


def _spread(placed: _Placed, amount: int) -> None:
    """Last resort: opens ``amount`` grid lines at every free cut line
    between components, horizontally and vertically (uniform spreading;
    relative order and alignments are kept)."""
    for axis in (0, 1):
        feet = []
        for p, (x, y) in placed.pos.items():
            f = placed.shape[p].footprint
            feet.append((p, x + f[0], y + f[1], x + f[2], y + f[3]))
        lo = min(f[1 + axis] for f in feet)
        hi = max(f[3 + axis] for f in feet)
        cuts = [v for v in range(lo + 1, hi + 1)
                if not any(f[1 + axis] < v <= f[3 + axis] for f in feet)
                and any(f[1 + axis] >= v for f in feet)]
        # one cut per gap between parts
        gaps = [v for i, v in enumerate(cuts) if i == 0 or cuts[i - 1] != v - 1]
        for p, *f in feet:
            k = sum(1 for v in gaps if f[axis] >= v) * amount
            x, y = placed.pos[p]
            placed.pos[p] = (x + k, y) if axis == 0 else (x, y + k)


def _route_widening(sch: Schematic, nl: Netlist, placed: _Placed, cfg: LayoutConfig,
                    widen: bool = True) -> Router:
    """Routes; where pins stay unconnected, opens corridors in front of them
    and routes again; finally spreads the whole drawing."""
    router = _route(sch, nl, placed, cfg)
    for attempt in range(6 if widen else 0):
        if all(n.routed for n in router.nets):
            break
        if attempt < 3:
            if not _widen(placed, router, 2):
                _spread(placed, 1)
        else:
            _spread(placed, 1)
        router = _route(sch, nl, placed, cfg)
    return router


def _emit(sch: Schematic, nl: Netlist, placed: _Placed, router: Router) -> None:
    for p, (x, y) in placed.pos.items():
        apply_shape(sch.components[p], placed.shape[p], x, y)
    sch.wires = []
    sch.junctions = []
    for net in router.nets:
        color = net_color(net.name)
        adj: Dict[int, List[int]] = {}
        for a, b in net.edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        term_nodes = {router.node(t.x, t.y) for t in net.terminals}
        # break points: not a straight pass-through
        def is_break(n: int) -> bool:
            nb = adj[n]
            if len(nb) != 2 or n in term_nodes:
                return True
            return (nb[0] - n) != -(nb[1] - n)
        seen = set()
        for a in sorted(adj):
            if not is_break(a):
                continue
            for b in sorted(adj[a]):
                e = (min(a, b), max(a, b))
                if e in seen:
                    continue
                path = [a, b]
                seen.add(e)
                while not is_break(path[-1]):
                    nxt = [c for c in adj[path[-1]] if c != path[-2]][0]
                    seen.add((min(path[-1], nxt), max(path[-1], nxt)))
                    path.append(nxt)
                (x1, y1), (x2, y2) = router.xy(path[0]), router.xy(path[-1])
                sch.wires.append(WireSegment(Point(x1 * GRID, y1 * GRID), Point(x2 * GRID, y2 * GRID),
                                             net_name=net.name, color=color))
        for n, nb in adj.items():
            if len(nb) >= 3:
                x, y = router.xy(n)
                sch.junctions.append(WireJunction(Point(x * GRID, y * GRID), net_name=net.name))
    xs, ys = [], []
    for c in sch.components.values():
        xs += [c.x, c.x + c.width]
        ys += [c.y, c.y + c.height]
    for wseg in sch.wires:
        xs += [wseg.start.x, wseg.end.x]
        ys += [wseg.start.y, wseg.end.y]
    if xs:
        sch.width = max(xs) - min(xs)
        sch.height = max(ys) - min(ys)


def layout_schematic(schematic: Schematic, config: Optional[LayoutConfig] = None) -> LayoutResult:
    cfg = config or DEFAULT_CONFIG
    merge_shared_pin_nets(schematic)
    nl = build_netlist(schematic, cfg.weights)
    models = {cid: PartModel(c) for cid, c in schematic.components.items()}
    if not schematic.components:
        schematic.width, schematic.height = 600.0, 400.0
        return LayoutResult(report=evaluate(schematic, nl, cfg.weights))

    workers = cfg.workers if cfg.workers > 0 else max(1, min(8, (os.cpu_count() or 2) - 1))
    jobs = [(schematic, nl, models, cfg, cfg.seed + r) for r in range(max(1, cfg.restarts))]
    placed_all = _map(_restart_job, jobs, workers)
    placements = sorted(((p, r) for r, p in enumerate(placed_all)), key=lambda e: (e[0].estimate, e[1]))
    chosen = placements[:max(1, cfg.routed_candidates)]
    # Route the best placements for real and score the drawings with the
    # same metrics as the final report; the cheapest drawing wins.
    routed = _map(_route_job, [(schematic, nl, p, cfg) for p, _ in chosen], workers)
    best: Optional[Tuple[float, int, _Placed]] = None
    tried: List[Tuple[str, float, float]] = []
    for (placed0, r), (cost, placed) in zip(chosen, routed):
        tried.append((f"seed{cfg.seed + r}", placed0.estimate, cost))
        if best is None or cost < best[0] - 1e-9:
            best = (cost, r, placed)
    assert best is not None
    _, r, placed = best
    router = _route_widening(schematic, nl, placed, cfg, widen=False)
    _emit(schematic, nl, placed, router)
    if cfg.net_labels:
        place_net_labels(schematic)
    report = evaluate(schematic, nl, cfg.weights)
    return LayoutResult(report=report, candidates=tried, strategy=f"seed{cfg.seed + r}")


def _restart_job(job) -> _Placed:
    schematic, nl, models, cfg, seed = job
    return _place_all(schematic, nl, models, cfg, seed)


def _route_job(job) -> Tuple[float, _Placed]:
    """Routes a placement (opening corridors where needed) and scores the
    drawing with the report metrics; returns the cost and the (possibly
    widened) placement."""
    schematic, nl, placed, cfg = job
    placed = copy.deepcopy(placed)
    router = _route_widening(schematic, nl, placed, cfg)
    sch = copy.deepcopy(schematic)
    _emit(sch, nl, placed, router)
    return evaluate(sch, nl, cfg.weights).cost + 1000.0 * sum(1 for n in router.nets if not n.routed), placed


def _map(fn, jobs, workers: int):
    """Runs independent jobs in parallel processes; results come back in job
    order, so the outcome never depends on scheduling.

    POSIX: ``fork`` -- the children reuse the already loaded code and never
    re-run the caller's main script (``spawn`` would, and a script without a
    ``__main__`` guard would start itself recursively).  Elsewhere, and on
    any failure, the jobs run sequentially."""
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    try:
        import multiprocessing as mp
        import warnings
        from concurrent.futures import ProcessPoolExecutor
        if "fork" not in mp.get_all_start_methods():
            return [fn(j) for j in jobs]
        with warnings.catch_warnings():
            # fork from a threaded server: the children only compute
            warnings.simplefilter("ignore", DeprecationWarning)
            with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=mp.get_context("fork")) as ex:
                return list(ex.map(fn, jobs))
    except (OSError, RuntimeError, ImportError):
        return [fn(j) for j in jobs]
