"""Layout pipeline: structure -> blocks -> arrangement -> routing -> labels.

``layout_schematic`` works in place on a parsed ``Schematic``:

1. merge nets that share a pin, resolve every connection to a pin;
2. structure (``structure.py``): supply and ground nets with many terminals
   become net tags at their pins; the remaining wiring splits the parts into
   islands (connected sub-circuits) and those into functional blocks;
3. every block is laid out on its own -- a small problem, solved quickly by
   the annealing of ``placement.py`` -- for each side its outside
   connections may leave on, when that side is first needed;
4. per island the blocks are arranged as units: the most connected block
   first; around a single-part core (a hub) every block on the side where
   its pins are, in the order of those pins; any other block at the
   cheapest spot beside a placed partner (all four sides, aligned to its
   partners); then a few rounds of moving single blocks and a polish of
   single parts (straight connections, pin sides).  Different blocks keep
   a wider gap than parts inside a block, so the groups stay visible;
5. the arrangement as placed, polished and improved, over several trials,
   is routed for real and the cheapest kept;
6. islands are packed into rows, unconnected parts in a row below;
7. routing with corridor widening where a pin stays open, optionally a
   refinement against the routed result (``thorough``), net labels, report.

Compared with annealing the whole drawing at once, every search here is
small: a block has a handful of parts, an island a handful of blocks.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field

from schematic_txt.colors import net_color
from schematic_txt.layout.config import DEFAULT_CONFIG, LayoutConfig
from schematic_txt.layout.labels import place_net_labels
from schematic_txt.layout.metrics import QualityReport, evaluate
from schematic_txt.layout.netlist import Netlist, build_netlist, merge_shared_pin_nets
from schematic_txt.layout.placement import Layout, PartModel, Placer, construction_order
from schematic_txt.layout.router import DX, DY, RNet, Router, RTerminal
from schematic_txt.layout.shapes import GRID, VEC, Shape, apply_shape
from schematic_txt.layout.structure import Structure, analyse
from schematic_txt.models import NetTag, Point, Schematic, Side, WireJunction, WireSegment

Pos = tuple[int, int]
DIR_INDEX = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
SIDES = (Side.LEFT, Side.RIGHT, Side.TOP, Side.BOTTOM)


@dataclass
class LayoutResult:
    report: QualityReport
    candidates: list[tuple[str, float, float]] = field(default_factory=list)  # (name, estimate, routed cost)
    strategy: str = ""
    blocks: list[list[str]] = field(default_factory=list)  # functional blocks, all islands


@dataclass
class _Placed:
    """A complete placement: position, variant and shape of every part."""

    pos: dict[str, Pos]
    shape: dict[str, Shape]
    estimate: float
    var: dict[str, int] = field(default_factory=dict)
    ports: dict[str, tuple] = field(default_factory=dict)  # free box pins: ((pin, side), ...)


@dataclass
class _BlockLayout:
    """One internal arrangement of a block, relative to the top-left corner
    of its footprint."""

    offs: dict[str, Pos]
    var: dict[str, int]
    ports: dict[str, tuple]
    w: int
    h: int
    estimate: float


# ===================================================================== blocks
def _external_nets(block: list[str], nl: Netlist) -> list[int]:
    inside = set(block)
    out: dict[int, None] = {}
    for p in block:
        for n in nl.part_nets.get(p, []):
            if any(q not in inside for q in nl.nets[n].parts):
                out[n] = None
    return list(out)


def _layout_block(
    block: list[str],
    models: dict[str, PartModel],
    nl: Netlist,
    cfg: LayoutConfig,
    side: Side | None,
    seed: int,
) -> list[_BlockLayout]:
    """``block_restarts`` annealing runs with the nets leaving the block
    pulled to ``side``; the distinct results, best estimate first."""
    escape = {n: side for n in _external_nets(block, nl)} if side is not None else {}
    if cfg.block_compactness != 1.0:
        # a group should look like one: inside a block, area costs more
        cfg = copy.copy(cfg)
        cfg.weights = copy.copy(cfg.weights)
        cfg.weights.perimeter *= cfg.block_compactness
    out: list[_BlockLayout] = []
    runs = max(1, cfg.block_restarts if len(block) > 1 else 1)
    for r in range(runs):
        layout = Layout(models, nl, cfg, block, escape=escape)
        placer = Placer(layout, random.Random(seed * 101 + r))
        placer.analytic(construction_order(block, nl), reach=6.0, jitter=3.0)
        layout.recompute()
        placer.polish(rounds=3)
        if len(block) > 1:
            placer.anneal(max(cfg.moves_per_part * len(block), cfg.min_block_moves))
            placer.polish()
        feet = [layout.foot(p) for p in block]
        x0 = min(f[0] for f in feet)
        y0 = min(f[1] for f in feet)
        x1 = max(f[2] for f in feet)
        y1 = max(f[3] for f in feet)
        bl = _BlockLayout(
            offs={p: (layout.pos[p][0] - x0, layout.pos[p][1] - y0) for p in block},
            var={p: layout.var[p] for p in block},
            ports={p: layout.ports[p] for p in block if models[p].free},
            w=x1 - x0,
            h=y1 - y0,
            estimate=layout.total,
        )
        if all((o.offs, o.var, o.ports) != (bl.offs, bl.var, bl.ports) for o in out):
            out.append(bl)
    out.sort(key=lambda b: b.estimate)
    return out


# =============================================================== arrangement
OPPOSITE = {Side.LEFT: Side.RIGHT, Side.RIGHT: Side.LEFT, Side.TOP: Side.BOTTOM, Side.BOTTOM: Side.TOP}


class _Arranger:
    """Places the blocks of one island as rigid units.

    A block placed on side S of a partner is laid out with its outside
    connections leaving towards the partner (side ``OPPOSITE[S]``); these
    internal layouts are computed on first use and shared between trials
    (``cache``): trial ``rank`` takes the rank-th best layout where there
    are several."""

    def __init__(
        self,
        blocks: list[list[str]],
        models: dict[str, PartModel],
        nl: Netlist,
        cfg: LayoutConfig,
        seed: int,
        rank: int = 0,
        cache: dict | None = None,
    ):
        self.blocks = blocks
        self.models = models
        self.cfg = cfg
        self.nl = nl
        self.seed = seed
        self.rank = rank
        self.cache = cache if cache is not None else {}
        parts = [p for b in blocks for p in b]
        self.block_of = {p: i for i, b in enumerate(blocks) for p in b}
        self.L = Layout(models, nl, cfg, parts, block_of=self.block_of)
        self.variants: list[list[_BlockLayout]] = [[] for _ in blocks]
        self._variant_of: dict[tuple[int, Side | None], int] = {}
        self.choice: dict[int, int] = {}  # block -> variant index
        self.ring: set[int] = set()  # blocks in a column beside the core
        # connection strength between blocks
        self.bw: list[dict[int, float]] = [{} for _ in blocks]
        for info in nl.nets:
            if len(info.terminals) < 2:
                continue
            s = 1.0 / (len(info.terminals) - 1)
            bs = [self.block_of[c] for c, _ in info.terminals if c in self.block_of]
            for a in bs:
                for b in bs:
                    if a != b:
                        self.bw[a][b] = self.bw[a].get(b, 0.0) + s

    # ------------------------------------------------------------ variants
    def variant(self, bi: int, toward: Side | None) -> int:
        """Index of the internal layout whose outside connections leave on
        ``toward`` (``None``: no preference).  Free box pins only face left
        or right, so a lone box has no layout towards the top or bottom."""
        block = self.blocks[bi]
        lone_box = len(block) == 1 and self.models[block[0]].free
        if not _external_nets(block, self.nl) or (lone_box and toward in (Side.TOP, Side.BOTTOM)):
            toward = None
        key = (bi, toward)
        if key not in self._variant_of:
            if key not in self.cache:
                k = SIDES.index(toward) + 1 if toward is not None else 0
                self.cache[key] = _layout_block(
                    block, self.models, self.nl, self.cfg, toward, self.seed + 31 * bi + 7 * k
                )
            alts = self.cache[key]
            bl = alts[self.rank % len(alts)]
            same = next(
                (
                    i
                    for i, o in enumerate(self.variants[bi])
                    if (o.offs, o.var, o.ports) == (bl.offs, bl.var, bl.ports)
                ),
                None,
            )
            if same is None:
                self.variants[bi].append(bl)
                same = len(self.variants[bi]) - 1
            self._variant_of[key] = same
        return self._variant_of[key]

    # ------------------------------------------------------------ geometry
    def frame(self, bi: int) -> tuple[int, int, int, int]:
        feet = [self.L.foot(p) for p in self.blocks[bi]]
        return (
            min(f[0] for f in feet),
            min(f[1] for f in feet),
            max(f[2] for f in feet),
            max(f[3] for f in feet),
        )

    def _changes(self, bi: int, vi: int, x: int, y: int):
        bl = self.variants[bi][vi]
        changes = {p: ((x + ox, y + oy), bl.var[p]) for p, (ox, oy) in bl.offs.items()}
        sides = {p: dict(prt) for p, prt in bl.ports.items() if prt}
        return changes, sides

    def _terminals(self, bi: int, vi: int) -> list[tuple[int, int, int]]:
        """(net, x, y) of the block's pins in variant ``vi``, relative to its
        frame, for nets that leave the block."""
        bl = self.variants[bi][vi]
        out = []
        inside = set(self.blocks[bi])
        for p in self.blocks[bi]:
            m = self.models[p]
            s = m.shape(bl.var[p], bl.ports.get(p) if m.free else None)
            ox, oy = bl.offs[p]
            for pid, ps in s.pins.items():
                n = self.nl.pin_net.get((p, pid))
                if n is None or all(q in inside for q in self.nl.nets[n].parts):
                    continue
                out.append((n, ox + ps.tx, oy + ps.ty))
        return out

    def _placed_terms(self, skip: int) -> dict[int, list[tuple[int, int]]]:
        out: dict[int, list[tuple[int, int]]] = {}
        for p in self.L.placed():
            if self.block_of[p] == skip:
                continue
            for pid in self.L.shape[p].pins:
                n = self.nl.pin_net.get((p, pid))
                if n is not None:
                    out.setdefault(n, []).append(self.L.terminal(p, pid)[0])
        return out

    def candidates(self, bi: int, around: list[int]) -> list[tuple[int, int, int]]:
        """(variant, x, y) beside every block in ``around``, on all four
        sides (facing it), slid so that a wire to a placed part is straight,
        or centred, or flush with an edge."""
        gap = self.cfg.block_gap + 1
        placed_terms = self._placed_terms(bi)
        out: dict[tuple[int, int, int], None] = {}
        for side in SIDES:
            vi = self.variant(bi, OPPOSITE[side])
            bl = self.variants[bi][vi]
            terms = self._terminals(bi, vi)
            for bj in around:
                fx0, fy0, fx1, fy1 = self.frame(bj)
                if side in (Side.LEFT, Side.RIGHT):
                    x = fx1 + gap if side == Side.RIGHT else fx0 - gap - bl.w
                    slide = {fy0, fy1 - bl.h, (fy0 + fy1 - bl.h) // 2}
                    for n, _tx, ty in terms:
                        for _px, py in placed_terms.get(n, ()):
                            slide.add(py - ty)
                    for y in slide:
                        out[(vi, x, y)] = None
                else:
                    y = fy1 + gap if side == Side.BOTTOM else fy0 - gap - bl.h
                    slide = {fx0, fx1 - bl.w, (fx0 + fx1 - bl.w) // 2}
                    for n, tx, _ty in terms:
                        for px, _py in placed_terms.get(n, ()):
                            slide.add(px - tx)
                    for x in slide:
                        out[(vi, x, y)] = None
        return list(out)

    def _try(self, bi: int, vi: int, x: int, y: int) -> tuple[float, dict] | None:
        changes, sides = self._changes(bi, vi, x, y)
        return self.L.apply(changes, sides=sides)

    def _best(self, bi: int, cands, improve_only: bool = False) -> tuple[float, int, int, int] | None:
        best = None
        for vi, x, y in cands:
            res = self._try(bi, vi, x, y)
            if res is None:
                continue
            d, tok = res
            self.L.revert(tok)
            if improve_only and d >= -1e-6:
                continue
            if best is None or d < best[0] - 1e-9:
                best = (d, vi, x, y)
        return best

    def _commit(self, bi: int, vi: int, x: int, y: int, check: bool = True) -> None:
        changes, sides = self._changes(bi, vi, x, y)
        self.L.apply(changes, check=check, sides=sides)
        self.choice[bi] = vi

    def _reside_boxes(self) -> None:
        """Free box pins face their partners again (cheap heuristic, kept
        only when it helps)."""
        L = self.L
        for p in L.placed():
            if not L.models[p].free:
                continue
            geo = L.geo_sides(p)
            if geo == L.sides_of(p):
                continue
            res = L.apply({}, sides={p: geo})
            if res is not None and res[0] > 1e-9:
                L.revert(res[1])

    # ------------------------------------------------------------- search
    def order(self) -> list[int]:
        n = len(self.blocks)
        strength = [sum(self.bw[i].values()) for i in range(n)]
        first = max(range(n), key=lambda i: (strength[i], len(self.blocks[i]), -i))
        order = [first]
        placed = {first}
        while len(order) < n:
            nxt = max(
                (i for i in range(n) if i not in placed),
                key=lambda i: (sum(self.bw[i].get(j, 0.0) for j in placed), strength[i], -i),
            )
            order.append(nxt)
            placed.add(nxt)
        return order

    def _hub_side(self, bi: int, core: int) -> tuple[Side | None, float]:
        """The side of the core where the pins wired to block ``bi`` are
        (majority, weighted like the connections), and their mean position
        along that side; ``None`` when the core's pins are free (a box: its
        pins follow the arrangement)."""
        votes: dict[Side, list[tuple[float, float]]] = {}
        inside = set(self.blocks[bi])
        for p in self.blocks[core]:
            m = self.models[p]
            for pid, ps in self.L.shape[p].pins.items():
                n = self.nl.pin_net.get((p, pid))
                if n is None or pid in m.free or not any(q in inside for q in self.nl.nets[n].parts):
                    continue
                (tx, ty), _ = self.L.terminal(p, pid)
                wgt = 1.0 / (len(self.nl.nets[n].terminals) - 1)
                votes.setdefault(ps.side, []).append((wgt, ty if ps.side in (Side.LEFT, Side.RIGHT) else tx))
        if not votes:
            return None, 0.0
        side = max(SIDES, key=lambda s: (sum(w for w, _ in votes.get(s, ())), -SIDES.index(s)))
        vs = votes[side]
        return side, sum(w * a for w, a in vs) / sum(w for w, _ in vs)

    def _aligned(self, bi: int, vi: int, side: Side, core: int, want: int) -> int:
        """Position (top or left edge) of block ``bi`` in layout ``vi`` beside
        the core at which most of its wires to the core run straight
        (weighted like the connections); among equals the one nearest
        ``want``."""
        vertical = side in (Side.LEFT, Side.RIGHT)
        core_terms: dict[int, list[tuple[int, int]]] = {}
        for p in self.blocks[core]:
            for pid in self.L.shape[p].pins:
                n = self.nl.pin_net.get((p, pid))
                if n is not None:
                    core_terms.setdefault(n, []).append(self.L.terminal(p, pid)[0])
        score: dict[int, float] = {}
        for n, tx, ty in self._terminals(bi, vi):
            wgt = 1.0 / (len(self.nl.nets[n].terminals) - 1)
            for px, py in core_terms.get(n, ()):
                off = py - ty if vertical else px - tx
                score[off] = score.get(off, 0.0) + wgt
        if not score:
            return want
        return max(score, key=lambda o: (round(score[o], 9), -abs(o - want), -o))

    def _place_beside(self, bi: int, vi: int, side: Side, at: int, core_frame) -> bool:
        """Commits block ``bi`` beside the core on ``side`` with its top or
        left edge at ``at``, or at the nearest legal position."""
        gap = self.cfg.block_gap + 1
        hx0, hy0, hx1, hy1 = core_frame
        bl = self.variants[bi][vi]
        for k in range(120):
            a = at + (k + 1) // 2 * (1 if k % 2 else -1)
            if side == Side.LEFT:
                x, y = hx0 - gap - bl.w, a
            elif side == Side.RIGHT:
                x, y = hx1 + gap, a
            elif side == Side.TOP:
                x, y = a, hy0 - gap - bl.h
            else:
                x, y = a, hy1 + gap
            res = self._try(bi, vi, x, y)
            if res is not None:
                self.L.revert(res[1])
                self._commit(bi, vi, x, y)
                return True
        return False

    def place_ring(self, core: int) -> list[int]:
        """Blocks wired to a single-part core sit beside it, each on the
        side where its pins are, in the order of those pins along the side,
        stacked with the block gap; returns the blocks placed."""
        hub_frame = self.frame(core)
        hx0, hy0, hx1, hy1 = hub_frame
        # the core's own column: blocks wired more strongly to the core than
        # to any other block (the rest goes further out, beside its partners)
        ring = [
            bi
            for bi in range(len(self.blocks))
            if bi != core
            and self.bw[bi].get(core)
            and self.bw[bi][core] >= max((x for j, x in self.bw[bi].items() if j != core), default=0.0)
        ]
        by_side: dict[Side, list[tuple[float, int, bool]]] = {s: [] for s in SIDES}
        free: list[int] = []
        for bi in ring:
            side, along = self._hub_side(bi, core)
            if side is None:
                free.append(bi)
            else:
                by_side[side].append((along, bi, True))

        def size(bi: int, side: Side) -> int:
            bl = self.variants[bi][self.variant(bi, OPPOSITE[side])]
            return bl.h if side in (Side.LEFT, Side.RIGHT) else bl.w

        # blocks on free box pins: the shorter of left / right column
        for bi in sorted(free, key=lambda b: (-self.bw[b][core], b)):
            lens = {s: sum(size(b, s) for _, b, _ in by_side[s]) for s in (Side.LEFT, Side.RIGHT)}
            s = Side.LEFT if lens[Side.LEFT] <= lens[Side.RIGHT] else Side.RIGHT
            by_side[s].append((float(len(by_side[s])), bi, False))
        hub = self.blocks[core][0]
        if free and self.models[hub].free:
            # the box's free pins face their block before anything is placed
            # (turning them later would resize the box into its neighbours)
            side_of_block = {bi: s for s in SIDES for _, bi, _ in by_side[s]}
            sides: dict[str, Side] = {}
            rest: list[str] = []
            for pid in self.models[hub].free:
                n = self.nl.pin_net.get((hub, pid))
                peers = [self.block_of[q] for q in self.nl.nets[n].parts if q != hub] if n is not None else []
                target = next((side_of_block[b] for b in peers if b in side_of_block), None)
                if target is None:
                    rest.append(pid)
                else:
                    sides[pid] = target
            for pid in rest:
                n_left = sum(1 for v in sides.values() if v == Side.LEFT)
                sides[pid] = Side.LEFT if n_left <= len(sides) - n_left else Side.RIGHT
            self.L.apply({}, check=False, sides={hub: sides})
            hub_frame = self.frame(core)
            hx0, hy0, hx1, hy1 = hub_frame
        placed = []
        gap = self.cfg.block_gap + 1
        for side in SIDES:
            col = sorted(by_side[side])
            if not col:
                continue
            vertical = side in (Side.LEFT, Side.RIGHT)
            span = sum(size(b, side) for _, b, _ in col) + (len(col) - 1) * gap
            lo, hi = (hy0, hy1) if vertical else (hx0, hx1)
            cursor = (lo + hi - span) // 2  # the column stacked, centred on the core
            targets: list[int] = []
            weights: list[float] = []
            for along, bi, fixed in col:
                vi = self.variant(bi, OPPOSITE[side])
                # opposite its pins on the core (free box pins: in the stack),
                # with as many wires straight as possible
                want = round(along) - size(bi, side) // 2 if fixed else cursor
                targets.append(self._aligned(bi, vi, side, core, want))
                weights.append(self.bw[bi][core])
                cursor += size(bi, side) + gap
            # every block as near its target as the order and the gaps allow,
            # the strongly wired ones first
            at = _column_positions(targets, weights, [size(b, side) for _, b, _ in col], gap)
            for (_, bi, _), a in zip(col, at, strict=True):
                if self._place_beside(bi, self.variant(bi, OPPOSITE[side]), side, a, hub_frame):
                    placed.append(bi)
                    self.ring.add(bi)
            self._reside_boxes()
        return placed

    def place(self) -> None:
        order = self.order()
        first = order[0]
        self._commit(first, self.variant(first, None), 0, 0, check=False)
        placed = [first]
        if len(self.blocks[first]) == 1 and sum(1 for b in self.bw[first] if b != first) >= 3:
            placed += self.place_ring(first)
        for bi in order[1:]:
            if bi in placed:
                continue
            around = [j for j in placed if self.bw[bi].get(j)] or list(placed)
            best = self._best(bi, self.candidates(bi, around))
            if best is None:
                # nowhere beside its partners: to the right of everything
                x = max(self.frame(j)[2] for j in placed) + self.cfg.block_gap + 2
                self._commit(bi, self.variant(bi, None), x, self.frame(placed[0])[1], check=False)
            else:
                _, vi, x, y = best
                self._commit(bi, vi, x, y)
            placed.append(bi)
            self._reside_boxes()

    def improve(self, rounds: int) -> None:
        """Moves single blocks while the drawing gets cheaper: a block in a
        column beside the core slides along it, any other block may move
        beside any of its partners."""
        order = self.order()
        for _ in range(rounds):
            improved = False
            for bi in order[1:]:
                fx0, fy0, _, _ = self.frame(bi)
                cur = self.choice[bi]
                cands: set[tuple[int, int, int]] = set()
                if bi not in self.ring:
                    partners = [j for j in self.bw[bi] if j in self.choice] or [order[0]]
                    cands.update(self.candidates(bi, partners))
                for k in (1, 2, 3, 5):
                    for dx, dy in ((k, 0), (-k, 0), (0, k), (0, -k)):
                        cands.add((cur, fx0 + dx, fy0 + dy))
                cands.discard((cur, fx0, fy0))
                best = self._best(bi, sorted(cands), improve_only=True)
                if best is not None:
                    _, vi, x, y = best
                    self._commit(bi, vi, x, y)
                    self._reside_boxes()
                    improved = True
            if not improved:
                break


def _routed_cost(nl: Netlist, layout: Layout, cfg: LayoutConfig) -> float:
    """Cost of a (partial) placement routed for real, with the router's own
    pricing of every net."""
    placed = _Placed(dict(layout.pos), dict(layout.shape), layout.total, dict(layout.var), dict(layout.ports))
    router = _route_widening(None, nl, placed, cfg, widen=False, repair=False)
    return sum(router.net_cost(n) for n in router.nets)


def _column_positions(targets: list[int], weights: list[float], sizes: list[int], gap: int) -> list[int]:
    """Positions of blocks stacked in a column, in the given order, without
    overlap (each starts at least ``gap`` after the previous one ends), that
    minimise the weighted distance to their targets: weighted L1 isotonic
    regression by pooling adjacent violators (Ayer et al. 1955), on targets
    shifted by the space the blocks before need."""
    offs: list[int] = []
    o = 0
    for size in sizes:
        offs.append(o)
        o += size + gap

    def median(pool: list[tuple[int, float]]) -> int:
        pool = sorted(pool)
        half = sum(w for _, w in pool) / 2.0
        acc = 0.0
        for v, w in pool:
            acc += w
            if acc >= half - 1e-9:
                return v
        return pool[-1][0]

    pools: list[tuple[list[tuple[int, float]], int]] = []  # (members, count)
    for t, w, off in zip(targets, weights, offs, strict=True):
        pools.append(([(t - off, w)], 1))
        while len(pools) > 1 and median(pools[-2][0]) > median(pools[-1][0]):
            (a, na), (b, nb) = pools[-2], pools[-1]
            pools[-2:] = [(a + b, na + nb)]
    out: list[int] = []
    for members, count in pools:
        out += [median(members)] * count
    return [z + off for z, off in zip(out, offs, strict=True)]


def _place_island(
    blocks: list[list[str]], models: dict[str, PartModel], nl: Netlist, cfg: LayoutConfig, seed: int
) -> Layout:
    """Arranges the blocks ``arrange_trials`` times (trial t takes the t-th
    best layout of every block) and compares, per trial, the arrangement as placed,
    polished (single parts) and improved (blocks moved, then polished) by
    routing each for real: the estimate that steers the moves misjudges long
    wires in large drawings, the router does not."""
    snaps: list = []
    arr = None
    cache: dict = {}
    # a single block has nothing to arrange: its own restarts are the trials
    for t in range(max(1, cfg.arrange_trials) if len(blocks) > 1 else 1):
        arr = _Arranger(blocks, models, nl, cfg, seed, rank=t, cache=cache)
        arr.place()
        L = arr.L
        first = L.snapshot()
        snaps.append(first)
        Placer(L, random.Random(seed + t)).polish()
        snaps.append(L.snapshot())
        if len(blocks) > 1 and cfg.arrange_rounds > 0:
            L.load(first)
            arr.improve(cfg.arrange_rounds)
            Placer(L, random.Random(seed + t)).polish()
            snaps.append(L.snapshot())
    assert arr is not None
    L = arr.L
    unique = []
    for sn in snaps:
        if sn not in unique:
            unique.append(sn)
    if len(unique) > 1:
        scored = []
        for i, sn in enumerate(unique):
            L.load(sn)
            scored.append((_routed_cost(nl, L, cfg), i))
        L.load(unique[min(scored)[1]])
    elif L.snapshot() != unique[0]:
        L.load(unique[0])
    return L


def _place_all(
    sch: Schematic, nl: Netlist, st: Structure, models: dict[str, PartModel], cfg: LayoutConfig
) -> _Placed:
    pos: dict[str, Pos] = {}
    shape: dict[str, Shape] = {}
    var: dict[str, int] = {}
    ports: dict[str, tuple] = {}
    estimate = 0.0
    # islands, largest first, packed into rows of a sensible width
    islands = []
    for gi, blocks in enumerate(st.blocks):
        layout = _place_island(blocks, models, nl, cfg, cfg.seed * 1000 + gi)
        parts = [p for b in blocks for p in b]
        feet = [layout.foot(p) for p in parts]
        box = (
            min(f[0] for f in feet),
            min(f[1] for f in feet),
            max(f[2] for f in feet),
            max(f[3] for f in feet),
        )
        islands.append((gi, layout, parts, box))
        estimate += layout.total
    islands.sort(key=lambda e: (-(e[3][2] - e[3][0]) * (e[3][3] - e[3][1]), e[0]))
    area = sum((b[2] - b[0] + cfg.group_gap) * (b[3] - b[1] + cfg.group_gap) for *_, b in islands)
    widest = max((b[2] - b[0] for *_, b in islands), default=0)
    row_w = max(widest, int(math.sqrt(area * 1.6)))
    x_cursor = y_cursor = row_h = 0
    for _gi, layout, parts, box in islands:
        w, h = box[2] - box[0], box[3] - box[1]
        if x_cursor > 0 and x_cursor + w > row_w:
            x_cursor = 0
            y_cursor += row_h + cfg.group_gap
            row_h = 0
        for p in parts:
            x, y = layout.pos[p]
            pos[p] = (x - box[0] + x_cursor, y - box[1] + y_cursor)
            shape[p] = layout.shape[p]
            var[p] = layout.var[p]
            if models[p].free:
                ports[p] = layout.ports.get(p, ())
        x_cursor += w + cfg.group_gap
        row_h = max(row_h, h)
    bottom = y_cursor + row_h
    # Unconnected parts: a row below everything.
    x = 0
    y = bottom + cfg.group_gap if islands else 0
    for p in st.loose:
        m = models[p]
        # free box pins all on the left (a complete assignment, so the
        # refinement can treat the part like any other)
        prt = tuple((pid, Side.LEFT) for pid in m.free) if m.free else None
        s = m.shape(m.variants[0], prt)
        f = s.footprint
        pos[p] = (x - f[0], y - f[1])
        shape[p] = s
        var[p] = m.variants[0]
        if prt is not None:
            ports[p] = prt
        x += (f[2] - f[0]) + cfg.clearance + 2
    return _Placed(pos, shape, estimate, var, ports)


# =================================================================== routing
def _route(
    sch: Schematic | None, nl: Netlist, placed: _Placed, cfg: LayoutConfig, repair: bool = True
) -> Router:
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
        for tag in s.tags.values():
            b = tag.box
            r.block_rect(x + math.ceil(b[0]), y + math.ceil(b[1]), x + math.floor(b[2]), y + math.floor(b[3]))
    for p, (x, y) in placed.pos.items():
        s = placed.shape[p]
        for pid, ps in s.pins.items():
            n = nl.pin_net.get((p, pid), -2)
            r.add_pin(x + ps.tx, y + ps.ty, DIR_INDEX[VEC[ps.side]], n)
    for info in nl.nets:
        if any(c not in placed.pos for c, _ in info.terminals):
            continue  # routing part of the drawing
        terms = []
        for c, pid in info.terminals:
            x, y = placed.pos[c]
            ps = placed.shape[c].pins[pid]
            terms.append(RTerminal(c, pid, x + ps.tx, y + ps.ty, DIR_INDEX[VEC[ps.side]]))
        r.nets.append(RNet(info.index, info.name, info.factor, info.rail, terms, bundle=info.bundle))
    r.route_all(cfg.reroute_passes, repair)
    return r


def _cut_line(feet: list[tuple[int, int, int, int]], start: int, step: int, axis: int) -> int | None:
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
    cuts: list[tuple[int, int, int]] = []  # (axis, coordinate, sign)
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
    for axis, v, _sign in dict.fromkeys(cuts):
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
        cuts = [
            v
            for v in range(lo + 1, hi + 1)
            if not any(f[1 + axis] < v <= f[3 + axis] for f in feet) and any(f[1 + axis] >= v for f in feet)
        ]
        # one cut per gap between parts
        gaps = [v for i, v in enumerate(cuts) if i == 0 or cuts[i - 1] != v - 1]
        for p, *f in feet:
            k = sum(1 for v in gaps if f[axis] >= v) * amount
            x, y = placed.pos[p]
            placed.pos[p] = (x + k, y) if axis == 0 else (x, y + k)


def _route_widening(
    sch: Schematic | None,
    nl: Netlist,
    placed: _Placed,
    cfg: LayoutConfig,
    widen: bool = True,
    repair: bool = True,
) -> Router:
    """Routes; where pins stay unconnected, opens corridors in front of them
    and routes again; finally spreads the whole drawing."""
    router = _route(sch, nl, placed, cfg, repair)
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
    sch.net_tags = []
    for p, (x, y) in placed.pos.items():
        s = placed.shape[p]
        apply_shape(sch.components[p], s, x, y)
        for pid, tag in s.tags.items():
            ps = s.pins[pid]
            b = tag.box
            sch.net_tags.append(
                NetTag(
                    net_name=tag.net,
                    component_id=p,
                    pin_id=pid,
                    terminal=Point((x + ps.tx) * GRID, (y + ps.ty) * GRID),
                    stub_end=Point((x + tag.sx) * GRID, (y + tag.sy) * GRID),
                    side=tag.side,
                    box=((x + b[0]) * GRID, (y + b[1]) * GRID, (x + b[2]) * GRID, (y + b[3]) * GRID),
                )
            )
    sch.wires = []
    sch.junctions = []
    for net in router.nets:
        color = net_color(net.name)
        adj: dict[int, list[int]] = {}
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
                    nxt = next(c for c in adj[path[-1]] if c != path[-2])
                    seen.add((min(path[-1], nxt), max(path[-1], nxt)))
                    path.append(nxt)
                (x1, y1), (x2, y2) = router.xy(path[0]), router.xy(path[-1])
                sch.wires.append(
                    WireSegment(
                        Point(x1 * GRID, y1 * GRID),
                        Point(x2 * GRID, y2 * GRID),
                        net_name=net.name,
                        color=color,
                    )
                )
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
    for tag in sch.net_tags:
        xs += [tag.box[0], tag.box[2]]
        ys += [tag.box[1], tag.box[3]]
    if xs:
        sch.width = max(xs) - min(xs)
        sch.height = max(ys) - min(ys)


# ==================================================================== driver
def layout_schematic(schematic: Schematic, config: LayoutConfig | None = None) -> LayoutResult:
    cfg = config or DEFAULT_CONFIG
    merge_shared_pin_nets(schematic)
    parts = list(schematic.components)
    full = build_netlist(schematic, cfg.weights)
    st = analyse(
        parts,
        full,
        symbols=cfg.net_tags,
        ground_symbol_min=cfg.ground_tag_min_terminals,
        supply_symbol_min=cfg.supply_tag_min_terminals,
    )
    tagged = {full.nets[i].name for i in st.symbol_nets}
    nl = build_netlist(schematic, cfg.weights, symbols=tagged)
    models = {cid: PartModel(c, nl.symbols_of(cid)) for cid, c in schematic.components.items()}
    blocks = [b for bs in st.blocks for b in bs]
    if not schematic.components:
        schematic.width, schematic.height = 600.0, 400.0
        schematic.net_tags = []
        return LayoutResult(report=evaluate(schematic, nl, cfg.weights))

    placed = _place_all(schematic, nl, st, models, cfg)
    strategy = f"{len(blocks)} blocks in {len(st.islands)} islands"
    tried = [("arranged", placed.estimate, 0.0)]
    if cfg.refine_rounds > 0:
        cost, placed = _route_job((schematic, nl, placed, cfg))
        tried = [("arranged", placed.estimate, cost)]
        block_of = {p: i for i, b in enumerate(blocks) for p in b}
        cost, placed, kept = _refine(schematic, nl, models, placed, cost, cfg, block_of)
        if kept:
            strategy += f" + {kept} refinements"
    router = _route_widening(schematic, nl, placed, cfg)
    _emit(schematic, nl, placed, router)
    if cfg.net_labels:
        place_net_labels(schematic)
    report = evaluate(schematic, nl, cfg.weights)
    return LayoutResult(report=report, candidates=tried, strategy=strategy, blocks=blocks)


def _refine(
    schematic: Schematic,
    nl: Netlist,
    models: dict[str, PartModel],
    placed: _Placed,
    cost: float,
    cfg: LayoutConfig,
    block_of: dict[str, int],
) -> tuple[float, _Placed, int]:
    """Local search on the finished drawing against the real objective.

    The estimate guides the arrangement but predicts the routed drawing
    only roughly, so the result can be improved further with the measure
    that is reported: per round, part moves (shift, align, attach, swap,
    rotate, slide, flip pin sides) are sampled, the ``refine_batch`` most
    promising by estimate are routed for real and the best is kept if the
    scored drawing gets cheaper.  Stops after ``refine_patience`` rounds
    without improvement.  Returns ``(cost, placement, improvements kept)``."""
    parts = list(schematic.components)
    layout = Layout(models, nl, cfg, parts, block_of=block_of)
    rng = random.Random(cfg.seed)
    placer = Placer(layout, rng)
    kept = 0
    stale = 0

    def load(p: _Placed) -> None:
        layout.load((dict(p.pos), dict(p.var), dict(p.ports)))

    load(placed)
    for _ in range(cfg.refine_rounds):
        cands: list[tuple[float, int, _Placed]] = []
        seen = set()
        for _ in range(cfg.refine_batch * 4):
            mv = placer._random_move()
            if not mv:
                continue
            res = layout.apply(mv[0], sides=mv[1])
            if res is None:
                continue
            d, tok = res
            key = (
                tuple(sorted(layout.pos.items())),
                tuple(sorted(layout.var.items())),
                tuple(sorted(layout.ports.items())),
            )
            if key not in seen:
                seen.add(key)
                cands.append(
                    (
                        d,
                        len(cands),
                        _Placed(
                            dict(layout.pos),
                            dict(layout.shape),
                            layout.total,
                            dict(layout.var),
                            dict(layout.ports),
                        ),
                    )
                )
            layout.revert(tok)
        if not cands:
            break
        cands.sort(key=lambda c: (c[0], c[1]))
        batch = [c[2] for c in cands[: cfg.refine_batch]]
        # quick verdict (no widening, no repair): a change that leaves a pin
        # open loses anyway, and one that routes cleanly routes identically
        routed = [_route_job((schematic, nl, p, cfg, True)) for p in batch]
        i_best = min(range(len(routed)), key=lambda i: (routed[i][0], i))
        new_cost, new_placed = routed[i_best]
        if new_cost < cost - 1e-6:
            cost, placed = new_cost, new_placed
            load(placed)
            kept += 1
            stale = 0
        else:
            stale += 1
            if stale >= cfg.refine_patience:
                break
    return cost, placed, kept


def _route_job(job) -> tuple[float, _Placed]:
    """Routes a placement (opening corridors where needed) and scores the
    drawing with the report metrics; returns the cost and the (possibly
    widened) placement."""
    schematic, nl, placed, cfg, *quick = job
    quick = bool(quick and quick[0])
    placed = copy.deepcopy(placed)
    router = _route_widening(schematic, nl, placed, cfg, widen=not quick, repair=not quick)
    sch = copy.deepcopy(schematic)
    _emit(sch, nl, placed, router)
    return evaluate(sch, nl, cfg.weights).cost + 1000.0 * sum(1 for n in router.nets if not n.routed), placed
