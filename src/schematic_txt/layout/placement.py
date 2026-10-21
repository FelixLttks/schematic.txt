"""Placement: where every component goes and how it is turned.

Problem
-------
Variables per component: grid position (top-left), variant (rotation /
mirror, see ``shapes``) and, for boxes, the side of every free pin.  Pins on
one side are ordered by the height of their peers (barycentric ordering), so
a bundle leaves a box without crossing itself.

Hard constraint: footprints (body, terminals, text) keep ``clearance`` free
grid lines between each other.

Objective: the estimated routing cost of the drawing (``estimate.route`` for
every connection, multi-terminal nets as the cheaper of a minimum spanning
tree and a single trunk) plus crossings, conflicts and side-by-side runs of
unrelated nets, plus a small perimeter term -- with the weights from
``config.Weights`` that also drive the router and score the result.

Method
------
1. *Analytic start*: quadratic wire length with pin directions (every
   connection pulls the peer's terminal in front of the pin), Gauss-Seidel
   sweeps, best variant per part, then legalisation.
2. *Polish* (steepest descent).
3. *Simulated annealing* (Kirkpatrick et al. 1983; for graph drawing
   Davidson & Harel 1996) over structured moves: shift, align a connection
   (also a whole straight-wired chain), move next to a neighbour, swap,
   rotate, slide a whole side of the drawing, flip pin sides (single pin,
   bundle, a box together with its partner boxes).  The objective is updated
   incrementally (only nets near the moved parts are re-estimated); a test
   checks it against a full recomputation.  Deterministic: a seeded
   generator, input order, never hash or name order.
4. *Polish* again, so every connection that can be made straight is.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from schematic_txt.layout.config import LayoutConfig
from schematic_txt.layout.estimate import HSeg, Pt, Rect, VSeg, crossings, route, segments, trunk
from schematic_txt.layout.netlist import Netlist
from schematic_txt.layout.shapes import (
    VEC,
    Shape,
    box_shape,
    classify,
    image_shape,
    switch3_shape,
    symbol2_shape,
)
from schematic_txt.models import Component, Side

Pos = tuple[int, int]


# ====================================================================== parts
class PartModel:
    """Shapes of one component in all its variants."""

    def __init__(self, comp: Component):
        self.comp = comp
        self.id = comp.id
        self.kind = classify(comp)
        self._cache: dict[object, Shape] = {}
        self.free: list[str] = []
        declared = any(p.side_specified for p in comp.pins.values())
        if self.kind == "symbol2":
            rots = [0, 1, 2, 3] if comp.rotatable else [0]
            if declared:
                rots = [r for r in (0, 1, 2, 3) if self._matches(symbol2_shape(comp, r))] or [0]
            self.variants = rots
        elif self.kind == "switch3":
            self.variants = [0, 1] if comp.rotatable and not declared else [0]
            if declared:
                self.variants = [r for r in (0, 1) if self._matches(switch3_shape(comp, r))] or [0]
        elif self.kind == "image":
            # quarter turns only: a photo upside down is hard to read
            self.variants = [0, 90, 270] if comp.rotatable else [0]
        else:
            self.variants = [0]
            self.free = [p.id for p in comp.pins.values() if not p.side_specified]

    def __reduce__(self):
        # rebuilt from the component in worker processes, so the shape
        # cache is not shipped along (it refills itself)
        return (PartModel, (self.comp,))

    def _matches(self, shape: Shape) -> bool:
        return all(shape.pins[p.id].side == p.side for p in self.comp.pins.values() if p.side_specified)

    def shape(self, variant: int, ports: tuple[tuple[str, Side], ...] | None = None) -> Shape:
        key = (variant, ports)
        s = self._cache.get(key)
        if s is not None:
            return s
        if self.kind == "symbol2":
            s = symbol2_shape(self.comp, variant)
        elif self.kind == "switch3":
            s = switch3_shape(self.comp, variant)
        elif self.kind == "image":
            s = image_shape(self.comp, variant)
        else:
            sides: dict[str, Side] = {}
            orders: dict[str, float] = {}
            for p in self.comp.pins.values():
                if p.side_specified:
                    sides[p.id] = p.side
                    orders[p.id] = -1e6 + p.order
            for rank, (pid, side) in enumerate(ports or ()):
                sides[pid] = side
                orders[pid] = float(rank)
            for pid in self.free:
                sides.setdefault(pid, Side.LEFT)
                orders.setdefault(pid, 1e6)
            s = box_shape(self.comp, sides, orders)
        self._cache[key] = s
        return s


# ================================================================= estimator
@dataclass
class _NetEst:
    cost: float = 0.0
    bends: int = 0
    length: int = 0
    hits: int = 0
    hs: list[HSeg] = field(default_factory=list)
    vs: list[VSeg] = field(default_factory=list)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)


def _mst(points: Sequence[Pt]) -> list[tuple[int, int]]:
    n = len(points)
    if n < 2:
        return []
    in_tree = [False] * n
    best = [math.inf] * n
    parent = [-1] * n
    best[0] = 0
    edges: list[tuple[int, int]] = []
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: (best[i], i))
        in_tree[u] = True
        if parent[u] >= 0:
            edges.append((parent[u], u))
        for v in range(n):
            if not in_tree[v]:
                d = abs(points[u][0] - points[v][0]) + abs(points[u][1] - points[v][1])
                if d < best[v]:
                    best[v], parent[v] = d, u
    return edges


class Layout:
    """Placement state with an incrementally maintained estimated cost."""

    def __init__(
        self, models: dict[str, PartModel], netlist: Netlist, cfg: LayoutConfig, parts: Sequence[str]
    ):
        self.models = models
        self.nl = netlist
        self.cfg = cfg
        self.w = cfg.weights
        self.parts = list(parts)
        self.part_set = set(parts)
        self.nets = sorted({n for p in parts for n in netlist.part_nets.get(p, [])})
        self.active: set[str] = set()
        self.pos: dict[str, Pos] = {}
        self.var: dict[str, int] = {}
        self.ports: dict[str, tuple[tuple[str, Side], ...]] = {}
        self.shape: dict[str, Shape] = {}
        self.est: dict[int, _NetEst] = {}
        self.pair: dict[tuple[int, int], float] = {}
        self.total = 0.0
        self._perim = 0.0
        self.box_neighbors: dict[str, list[str]] = {}
        for p in parts:
            ns: dict[str, None] = {}
            for n in netlist.part_nets.get(p, []):
                for q in netlist.nets[n].parts:
                    if q != p and q in self.part_set and models[q].free:
                        ns[q] = None
            self.box_neighbors[p] = list(ns)

    # ------------------------------------------------------------ geometry
    def body(self, p: str) -> Rect:
        x, y = self.pos[p]
        s = self.shape[p]
        return (x, y, x + s.w, y + s.h)

    def foot(self, p: str) -> Rect:
        x, y = self.pos[p]
        f = self.shape[p].footprint
        return (x + f[0], y + f[1], x + f[2], y + f[3])

    def terminal(self, p: str, pid: str) -> tuple[Pt, Pt]:
        x, y = self.pos[p]
        ps = self.shape[p].pins[pid]
        return (x + ps.tx, y + ps.ty), VEC[ps.side]

    def center2(self, p: str) -> Pt:
        """Doubled centre (stays integral)."""
        x, y = self.pos[p]
        s = self.shape[p]
        return (2 * x + s.w, 2 * y + s.h)

    def placed(self) -> list[str]:
        """Active parts in input order (never in hash or name order)."""
        return [p for p in self.parts if p in self.active]

    def _zone(self, p: str) -> Rect:
        f = self.foot(p)
        return (f[0] - 1, f[1] - 1, f[2] + 1, f[3] + 1)

    def legal(self, p: str, others: Iterable[str] | None = None) -> bool:
        c = self.cfg.clearance
        a = self.foot(p)
        for q in others if others is not None else self.placed():
            if q == p or q not in self.active:
                continue
            b = self.foot(q)
            if a[0] - c <= b[2] and b[0] - c <= a[2] and a[1] - c <= b[3] and b[1] - c <= a[3]:
                return False
        return True

    # ------------------------------------------------------------- ports
    def geo_sides(self, p: str) -> dict[str, Side]:
        """Initial guess for free box pins: face the mean of the peers (for
        distribution nets the two nearest).  Only a starting point -- the
        annealing flips sides when that is cheaper."""
        model = self.models[p]
        x, y = self.pos[p]
        cur = self.shape[p]
        cx2 = 2 * x + cur.w
        sides: dict[str, Side] = {}
        loose: list[str] = []
        for pid in model.free:
            n = self.nl.pin_net.get((p, pid))
            pts: list[Pt] = []
            if n is not None:
                info = self.nl.nets[n]
                for q, qp in info.terminals:
                    if q != p and q in self.active:
                        pts.append(self.terminal(q, qp)[0])
                if info.rail and len(pts) > 2:
                    pts.sort(key=lambda t: (abs(2 * t[0] - cx2) + abs(2 * t[1] - 2 * y - cur.h), t))
                    pts = pts[:2]
            if not pts:
                loose.append(pid)
                continue
            assert n is not None  # no net, no peers
            mx2 = 2.0 * sum(t[0] for t in pts) / len(pts)
            if abs(mx2 - cx2) <= cur.w:
                # peers above / below: the side where their pins are (the
                # wire then goes round on that side instead of crossing over)
                pdx = sum(
                    VEC[self.shape[q].pins[qp].side][0]
                    for q, qp in self.nl.nets[n].terminals
                    if q != p and q in self.active
                )
                if pdx:
                    sides[pid] = Side.LEFT if pdx < 0 else Side.RIGHT
                    continue
            sides[pid] = Side.LEFT if mx2 < cx2 else Side.RIGHT
        for pid in loose:
            # Unconnected pins balance the box.
            n_left = sum(1 for s_ in sides.values() if s_ == Side.LEFT)
            n_right = len(sides) - n_left
            sides[pid] = Side.LEFT if n_left <= n_right else Side.RIGHT
        return sides

    def order_ports(self, p: str, sides: dict[str, Side]) -> tuple[tuple[str, Side], ...]:
        """Pins on a side sorted by the height of their peers (barycentre),
        so wires leaving one side do not cross each other."""
        model = self.models[p]
        entries = []
        for pid in model.free:
            n = self.nl.pin_net.get((p, pid))
            ys: list[int] = []
            if n is not None:
                for q, qp in self.nl.nets[n].terminals:
                    if q != p and q in self.active:
                        ys.append(self.terminal(q, qp)[0][1])
            key = sum(ys) / len(ys) if ys else 1e6
            entries.append((pid, sides[pid], key))
        entries.sort(key=lambda e: (e[2], model.free.index(e[0])))
        return tuple((pid, side) for pid, side, _ in entries)

    def sides_of(self, p: str) -> dict[str, Side]:
        return {pid: side for pid, side in self.ports.get(p, ())}

    def _set_shape(self, p: str) -> None:
        m = self.models[p]
        self.shape[p] = m.shape(self.var[p], self.ports.get(p) if m.free else None)

    # ------------------------------------------------------------ estimate
    def _marks(self, nets: Iterable[int] | None = None) -> dict[int, list[Pt]]:
        """Per net: grid nodes of *other* nets' terminals and pin fronts
        (for ``nets`` only, default all)."""
        allm: list[tuple[int, Pt]] = []
        for p in self.placed():
            x, y = self.pos[p]
            for pid, ps in self.shape[p].pins.items():
                n = self.nl.pin_net.get((p, pid), -1)
                tx, ty = x + ps.tx, y + ps.ty
                allm.append((n, (tx, ty)))
                if n >= 0:
                    dx, dy = VEC[ps.side]
                    allm.append((n, (tx + dx, ty + dy)))
        return {n: [m for k, m in allm if k != n] for n in (self.nets if nets is None else nets)}

    def _net_estimate(
        self, n: int, rects: list[Rect], owner_rect: dict[str, Rect], marks: dict[int, list[Pt]] | None = None
    ) -> _NetEst:
        info = self.nl.nets[n]
        terms = [(q, qp) for q, qp in info.terminals if q in self.active]
        e = _NetEst()
        if len(terms) < 2:
            return e
        pts = []
        dirs = []
        for q, qp in terms:
            t, d = self.terminal(q, qp)
            pts.append(t)
            dirs.append(d)
        w = self.w
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bx0, by0, bx1, by1 = min(xs) - 3, min(ys) - 3, max(xs) + 3, max(ys) + 3
        # every estimated route stays within the terminals' box + 3
        rects = [r for r in rects if r[0] <= bx1 and r[2] >= bx0 and r[1] <= by1 and r[3] >= by0]
        mk = [m for m in (marks.get(n, ()) if marks else ()) if bx0 <= m[0] <= bx1 and by0 <= m[1] <= by1]
        polys: list[list[Pt]] = []
        for i, j in _mst(pts):
            cost, bends, hits, poly = route(
                pts[i],
                dirs[i],
                pts[j],
                dirs[j],
                rects,
                w.bend * info.factor,
                w.length * info.factor,
                w.obstacle,
                owner_rect[terms[i][0]],
                owner_rect[terms[j][0]],
                mk,
            )
            e.cost += cost
            e.bends += bends
            e.hits += hits
            polys.append(poly)
        e.cost += w.junction * max(0, len(pts) - 2)
        if len(pts) >= 3:
            tr = trunk(
                pts,
                dirs,
                rects,
                w.bend * info.factor,
                w.length * info.factor,
                w.obstacle,
                w.junction,
                [owner_rect[q] for q, _ in terms],
                mk,
                e.cost,
            )
            if tr is not None and tr[0] < e.cost:
                e.cost, e.bends, e.hits, polys = tr
        for poly in polys:
            hs, vs = segments(poly)
            e.hs += hs
            e.vs += vs
            e.length += sum(s[2] - s[1] for s in hs) + sum(s[2] - s[1] for s in vs)
        e.bbox = (min(xs), min(ys), max(xs), max(ys))
        return e

    def _pair_cost(self, a: int, b: int) -> float:
        ea, eb = self.est.get(a), self.est.get(b)
        if ea is None or eb is None or not (ea.hs or ea.vs) or not (eb.hs or eb.vs):
            return 0.0
        ba, bb = ea.bbox, eb.bbox
        far = ba[0] > bb[2] + 3 or bb[0] > ba[2] + 3 or ba[1] > bb[3] + 3 or bb[1] > ba[3] + 3
        if far and not (ea.hits or eb.hits):
            # Estimated routes stay near their terminals' box; skip far pairs
            # unless a route is known to leave it.
            return 0.0
        c, o, adj = crossings(ea.hs, ea.vs, eb.hs, eb.vs)
        if not (c or o or adj):
            return 0.0
        na, nb = self.nl.nets[a], self.nl.nets[b]
        f = (na.factor + nb.factor) / 2.0
        if na.bundle >= 0 and na.bundle == nb.bundle:
            # bundle mates: side by side is the goal; sharing a line only
            # means the router lays them on neighbouring tracks
            return self.w.crossing * c * f + 0.25 * self.w.overlap * o * f
        return self.w.crossing * c * f + self.w.overlap * o * f + self.w.near * adj

    def _perimeter(self) -> float:
        """Drawing-wide terms: perimeter and turned image parts."""
        if not self.active:
            return 0.0
        fs = [self.foot(p) for p in self.active]
        cost = self.w.perimeter * (
            (max(f[2] for f in fs) - min(f[0] for f in fs)) + (max(f[3] for f in fs) - min(f[1] for f in fs))
        )
        for p in self.placed():
            if self.models[p].kind == "image" and self.var[p]:
                cost += self.w.rotation
        return cost

    def _rects(self) -> tuple[list[Rect], dict[str, Rect]]:
        owner = {p: self.body(p) for p in self.active}
        return [owner[p] for p in self.placed()], owner

    def recompute(self) -> float:
        rects, owner = self._rects()
        marks = self._marks()
        self.est = {n: self._net_estimate(n, rects, owner, marks) for n in self.nets}
        self.pair = {}
        for i, a in enumerate(self.nets):
            for b in self.nets[i + 1 :]:
                c = self._pair_cost(a, b)
                if c:
                    self.pair[(a, b)] = c
        self._perim = self._perimeter()
        self.total = sum(e.cost for e in self.est.values()) + sum(self.pair.values()) + self._perim
        return self.total

    # --------------------------------------------------------------- moves
    def apply(
        self,
        changes: dict[str, tuple[Pos, int]],
        check: bool = True,
        sides: dict[str, dict[str, Side]] | None = None,
    ) -> tuple[float, dict] | None:
        """Applies position / variant changes (and new free-pin sides of
        boxes).  Returns ``(delta, token)`` or ``None`` (state unchanged)
        when the result is illegal."""
        sides = sides or {}
        old_state = {}
        touched: dict[str, None] = {}
        old_rects: list[Rect] = []
        for p in list(changes) + [q for q in sides if q not in changes]:
            if p in self.active:
                old_rects.append(self._zone(p))
            old_state[p] = (
                p in self.active,
                self.pos.get(p),
                self.var.get(p),
                self.ports.get(p),
                self.shape.get(p),
            )
        fresh: list[str] = []
        for p, (pos, var) in changes.items():
            if p not in self.active and self.models[p].free:
                fresh.append(p)
            self.active.add(p)
            self.pos[p] = pos
            self.var[p] = var
            if self.models[p].free and p not in self.ports:
                self.ports[p] = ()
            self._set_shape(p)
            touched[p] = None
        # Sides of free box pins are variables (changed only on request or
        # when a box is first placed); their order follows the geometry:
        # re-sort the moved boxes and the boxes wired to anything that moved.
        boxes: dict[str, None] = {}
        for p in list(changes) + list(sides):
            if self.models[p].free:
                boxes[p] = None
            for q in self.box_neighbors[p]:
                if q in self.active:
                    boxes[q] = None
        for q in boxes:
            if q not in old_state:
                old_state[q] = (True, self.pos[q], self.var[q], self.ports.get(q), self.shape[q])
            if q in sides:
                sd = dict(self.sides_of(q))
                sd.update(sides[q])
            elif q in fresh or len(self.ports.get(q, ())) != len(self.models[q].free):
                sd = self.geo_sides(q)
            else:
                sd = self.sides_of(q)
            ports = self.order_ports(q, sd)
            if ports != self.ports.get(q):
                self.ports[q] = ports
                self._set_shape(q)
                touched[q] = None
                if q not in changes:
                    old_shape = old_state[q][4]
                    assert old_shape is not None
                    f = old_shape.footprint
                    x, y = self.pos[q]
                    old_rects.append((x + f[0] - 1, y + f[1] - 1, x + f[2] + 1, y + f[3] + 1))
        if check:
            others = self.placed()
            for p in touched:
                if not self.legal(p, others):
                    self._restore(old_state)
                    return None
        # Everything a net estimate depends on lies inside the footprints
        # (bodies, terminals) plus one step (the nodes in front of pins).
        new_rects = [self._zone(p) for p in touched]
        affected: dict[int, None] = {}
        for p in touched:
            for n in self.nl.part_nets.get(p, []):
                affected[n] = None
        zone = old_rects + new_rects
        for n in self.nets:
            if n in affected:
                continue
            b = self.est[n].bbox if n in self.est else None
            if b is None or not (self.est[n].hs or self.est[n].vs):
                continue
            for r in zone:
                if r[0] <= b[2] + 3 and r[2] >= b[0] - 3 and r[1] <= b[3] + 3 and r[3] >= b[1] - 3:
                    affected[n] = None
                    break
        rects, owner = self._rects()
        marks = self._marks(affected)
        old_est = {n: self.est.get(n) for n in affected}
        old_pair: dict[tuple[int, int], float] = {}
        delta = 0.0
        for n in affected:
            e = self._net_estimate(n, rects, owner, marks)
            old_e = old_est[n]
            delta += e.cost - (old_e.cost if old_e else 0.0)
            self.est[n] = e
        done: set[tuple[int, int]] = set()
        for n in affected:
            for m in self.nets:
                if m == n:
                    continue
                key = (n, m) if n < m else (m, n)
                if key in done:
                    continue
                done.add(key)
                old = self.pair.get(key, 0.0)
                old_pair[key] = old
                new = self._pair_cost(*key)
                delta += new - old
                if new:
                    self.pair[key] = new
                else:
                    self.pair.pop(key, None)
        old_perim = self._perim
        self._perim = self._perimeter()
        delta += self._perim - old_perim
        self.total += delta
        token = {"state": old_state, "est": old_est, "pair": old_pair, "perim": old_perim}
        return delta, token

    def _restore(self, old_state: dict) -> None:
        for p, (was_active, pos, var, ports, shape) in old_state.items():
            if not was_active:
                self.active.discard(p)
                self.pos.pop(p, None)
                self.var.pop(p, None)
                self.shape.pop(p, None)
                self.ports.pop(p, None)
                continue
            self.pos[p] = pos
            self.var[p] = var
            if ports is None:
                self.ports.pop(p, None)
            else:
                self.ports[p] = ports
            self.shape[p] = shape

    def revert(self, token: dict) -> None:
        self._restore(token["state"])
        for n, e in token["est"].items():
            if e is None:
                self.est.pop(n, None)
            else:
                self.est[n] = e
        for key, v in token["pair"].items():
            if v:
                self.pair[key] = v
            else:
                self.pair.pop(key, None)
        self._perim = token["perim"]
        self.total = sum(e.cost for e in self.est.values()) + sum(self.pair.values()) + self._perim

    def snapshot(self) -> tuple[dict[str, Pos], dict[str, int], dict[str, tuple[tuple[str, Side], ...]]]:
        return dict(self.pos), dict(self.var), dict(self.ports)

    def load(self, snap) -> None:
        pos, var, ports = snap
        self.active = set(pos)
        self.pos, self.var, self.ports = dict(pos), dict(var), dict(ports)
        for p in self.active:
            self._set_shape(p)
        self.recompute()

    def shape_for(self, p: str, var: int) -> Shape:
        m = self.models[p]
        return m.shape(var, self.ports.get(p) if m.free else None)


# ================================================================== placer
class Placer:
    def __init__(self, layout: Layout, rng: random.Random):
        self.L = layout
        self.rng = rng
        self.cfg = layout.cfg

    # ------------------------------------------------------------ helpers
    def _pos_for_center(self, p: str, var: int, c2: Pt) -> Pos:
        s = self.L.shape_for(p, var)
        return ((c2[0] - s.w) // 2, (c2[1] - s.h) // 2)

    def _links(self, p: str) -> list[tuple[str, str, str]]:
        """(own pin, other part, other pin) for every connection of ``p``."""
        out = []
        for n in self.L.nl.part_nets.get(p, []):
            info = self.L.nl.nets[n]
            mine = [pid for q, pid in info.terminals if q == p]
            for q, qp in info.terminals:
                if q != p and q in self.L.active:
                    for pid in mine:
                        out.append((pid, q, qp))
        return out

    def _attach(self, p: str, var: int, pid: str, q: str, qp: str, gap: int) -> Pos | None:
        """Position of ``p`` (variant ``var``) such that its pin ``pid`` sits
        straight in front of ``q``'s pin ``qp`` at distance ``gap``, if the
        two pins can face each other."""
        s = self.L.shape_for(p, var)
        ps = s.pins[pid]
        (tx, ty), (dx, dy) = self.L.terminal(q, qp)
        mdx, mdy = VEC[ps.side]
        if (mdx, mdy) != (-dx, -dy):
            return None
        return (tx + dx * gap - ps.tx, ty + dy * gap - ps.ty)

    def _beside(self, p: str, var: int, q: str, side: Side, offset: int) -> Pos:
        """``p`` next to ``q`` on ``side``, centred, with ``offset`` extra gap."""
        s = self.L.shape_for(p, var)
        fq = self.L.foot(q)
        f = s.footprint
        c = self.cfg.clearance + 1 + offset
        cx = (fq[0] + fq[2]) // 2
        cy = (fq[1] + fq[3]) // 2
        if side == Side.RIGHT:
            return (fq[2] + c - f[0], cy - (f[1] + f[3]) // 2)
        if side == Side.LEFT:
            return (fq[0] - c - f[2], cy - (f[1] + f[3]) // 2)
        if side == Side.TOP:
            return (cx - (f[0] + f[2]) // 2, fq[1] - c - f[3])
        return (cx - (f[0] + f[2]) // 2, fq[3] + c - f[1])

    def _near_legal(self, p: str, var: int, pos: Pos, radius: int, axis: int | None = None) -> Pos | None:
        """Nearest legal position to ``pos`` (spiral), or along one axis only."""
        L = self.L
        saved = (p in L.active, L.pos.get(p), L.var.get(p), L.ports.get(p), L.shape.get(p))
        L.active.add(p)
        L.var[p] = var
        if L.models[p].free and p not in L.ports:
            L.ports[p] = ()
        L._set_shape(p)
        found: Pos | None = None
        offsets: list[Pos] = [(0, 0)]
        for r in range(1, radius + 1):
            if axis == 0:
                offsets += [(r, 0), (-r, 0)]
            elif axis == 1:
                offsets += [(0, r), (0, -r)]
            else:
                ring = [
                    (dx, dy)
                    for dx in range(-r, r + 1)
                    for dy in range(-r, r + 1)
                    if max(abs(dx), abs(dy)) == r
                ]
                ring.sort(key=lambda d: (abs(d[0]) + abs(d[1]), d))
                offsets += ring
        others = L.placed()
        for dx, dy in offsets:
            L.pos[p] = (pos[0] + dx, pos[1] + dy)
            if L.legal(p, others):
                found = L.pos[p]
                break
        L._restore({p: saved})
        return found

    def _try(self, changes: dict[str, tuple[Pos, int]]) -> tuple[float, dict] | None:
        return self.L.apply(changes)

    # ------------------------------------------------------ analytic start
    def analytic(self, order: list[str], reach: float, jitter: float) -> None:
        """Global placement by quadratic wire length with pin directions.

        Every connection pulls the peer's terminal to a point ``reach`` grid
        steps *in front of* the pin (both ways), so parts gather on the side
        of a component where their pins are, at the height of those pins.
        Minimised by Gauss-Seidel sweeps (each part moves to the weighted
        mean of its targets), alternating with the choice of the best
        variant per part.  Then parts are legalised in ``order``, each at
        the free spot nearest to its target.  Free box pins pull from the
        box centre (their side is not known yet)."""
        L, rng = self.L, self.rng
        nl = L.nl
        parts = L.parts
        var = {p: L.models[p].variants[0] for p in parts}
        # centres (float), deterministic initial spread on a circle
        cen: dict[str, tuple[float, float]] = {}
        n = len(parts)
        for i, p in enumerate(order):
            ang = 2 * math.pi * i / max(1, n)
            r = 0.0 if i == 0 else 10.0 + 2.0 * i
            cen[p] = (
                r * math.cos(ang) + rng.uniform(-jitter, jitter),
                r * math.sin(ang) + rng.uniform(-jitter, jitter),
            )

        guess: dict[str, dict[str, int]] = {p: {} for p in parts}  # free pin -> -1 left / +1 right

        def pin_geo(p: str, v: int, pid: str) -> tuple[float, float, int, int]:
            m = L.models[p]
            if pid in m.free:
                side = guess[p].get(pid, 0)
                if not side:
                    return 0.0, 0.0, 0, 0
                half = m.shape(v).w / 2.0 + 1.0
                return side * half, 0.0, side, 0
            sh = m.shape(v)
            ps = sh.pins[pid]
            dx, dy = VEC[ps.side]
            return ps.tx - sh.w / 2.0, ps.ty - sh.h / 2.0, dx, dy

        links: dict[str, list[tuple[str, str, str, float]]] = {p: [] for p in parts}
        for idx in L.nets:
            info = nl.nets[idx]
            terms = [t for t in info.terminals if t[0] in L.part_set]
            if len(terms) < 2:
                continue
            wgt = info.factor / (len(terms) - 1)
            for a, ap in terms:
                for b, bp in terms:
                    if a != b:
                        links[a].append((ap, b, bp, wgt))

        def targets(p: str, v: int) -> tuple[float, float, float, float]:
            sx = sy = sw = 0.0
            energy = 0.0
            for ap, b, bp, wgt in links[p]:
                ox, oy, dx, dy = pin_geo(p, v, ap)
                bx, by = cen[b]
                qx, qy, ex, ey = pin_geo(b, var[b], bp)
                # my terminal should sit `reach` in front of the peer pin
                tx1, ty1 = bx + qx + ex * reach - ox, by + qy + ey * reach - oy
                # and the peer terminal `reach` in front of mine
                tx2, ty2 = bx + qx - dx * reach - ox, by + qy - dy * reach - oy
                sx += wgt * (tx1 + tx2)
                sy += wgt * (ty1 + ty2)
                sw += 2 * wgt
            if sw == 0:
                return cen[p][0], cen[p][1], 0.0, 0.0
            mx, my = sx / sw, sy / sw
            for ap, b, bp, wgt in links[p]:
                ox, oy, dx, dy = pin_geo(p, v, ap)
                bx, by = cen[b]
                qx, qy, ex, ey = pin_geo(b, var[b], bp)
                for tx, ty in (
                    (bx + qx + ex * reach - ox, by + qy + ey * reach - oy),
                    (bx + qx - dx * reach - ox, by + qy - dy * reach - oy),
                ):
                    energy += wgt * ((mx - tx) ** 2 + (my - ty) ** 2)
            return mx, my, sw, energy

        anchor = order[0]
        for it in range(120):
            for p in order:
                if p == anchor:
                    continue
                mx, my, sw, _ = targets(p, var[p])
                if sw:
                    cen[p] = (mx, my)
            if it % 10 == 4:
                # free box pins face the mean of their peers
                for p in order:
                    m = L.models[p]
                    for pid in m.free:
                        n_ = nl.pin_net.get((p, pid))
                        if n_ is None:
                            continue
                        xs_ = [cen[q][0] for q, _ in nl.nets[n_].terminals if q != p and q in cen]
                        if xs_:
                            mx_ = sum(xs_) / len(xs_)
                            guess[p][pid] = -1 if mx_ < cen[p][0] else 1
            if it % 10 == 9:
                for p in order:
                    vs = L.models[p].variants
                    if len(vs) > 1:
                        best_v = min(vs, key=lambda v: (targets(p, v)[3], vs.index(v)))
                        var[p] = best_v
        # Legalise in order: nearest free spot to the analytic position.
        L.active.clear()
        L.pos.clear()
        L.var.clear()
        L.shape.clear()
        L.ports.clear()
        for p in order:
            sh = L.shape_for(p, var[p])
            target = (round(cen[p][0] - sh.w / 2.0), round(cen[p][1] - sh.h / 2.0))
            pos = None
            for radius in (4, 10, 25, 60):
                pos = self._near_legal(p, var[p], target, radius)
                if pos is not None:
                    break
            if pos is None:
                right = max(L.foot(q)[2] for q in L.active) + self.cfg.clearance + 4 if L.active else 0
                pos = (right - sh.footprint[0], target[1])
            L.apply({p: (pos, var[p])}, check=False)
        for p in order:
            if L.models[p].free:
                L.apply({}, check=False, sides={p: L.geo_sides(p)})
        self.legalize(order)
        L.recompute()

    def legalize(self, order: list[str]) -> None:
        """Restores the clearance invariant: every part that conflicts with
        another moves to the nearest legal spot (in ``order``)."""
        L = self.L
        for _ in range(3):
            moved = False
            for p in order:
                if L.legal(p):
                    continue
                for radius in (4, 10, 25, 60):
                    pos = self._near_legal(p, L.var[p], L.pos[p], radius)
                    if pos is not None and L.apply({p: (pos, L.var[p])}) is not None:
                        moved = True
                        break
            if not moved:
                break

    # ------------------------------------------------------------ annealing
    def _side_move(self) -> dict[str, dict[str, Side]] | None:
        """Flips free box pins: one pin, all pins wired to the same parts
        (a bundle), or the whole box back to the geometric guess."""
        L, rng = self.L, self.rng
        boxes = [p for p in L.parts if L.models[p].free]
        if not boxes:
            return None
        b = boxes[rng.randrange(len(boxes))]
        cur = L.sides_of(b)
        free = L.models[b].free
        r = rng.random()
        if r < 0.15:
            geo = L.geo_sides(b)
            return {b: geo} if geo != cur else None
        if r < 0.3:
            return self._reside_group(b)
        pid = free[rng.randrange(len(free))]
        flip = {Side.LEFT: Side.RIGHT, Side.RIGHT: Side.LEFT}
        if r < 0.6:
            return {b: {pid: flip.get(cur[pid], Side.LEFT)}}
        n = L.nl.pin_net.get((b, pid))
        peers = set(L.nl.nets[n].parts) - {b} if n is not None else set()
        group = {}
        for q in free:
            m = L.nl.pin_net.get((b, q))
            if m is not None and set(L.nl.nets[m].parts) - {b} == peers and cur[q] == cur[pid]:
                group[q] = flip.get(cur[q], Side.LEFT)
        return {b: group} if group else None

    def rigid_group(self, p: str, limit: int = 5) -> list[str]:
        """``p`` and the parts joined to it by straight two-pin wires (a chain
        that is already aligned); empty growth beyond ``limit`` parts."""
        L = self.L
        group = [p]
        seen = {p}
        i = 0
        while i < len(group):
            a = group[i]
            i += 1
            for n in L.nl.part_nets.get(a, []):
                info = L.nl.nets[n]
                if len(info.terminals) != 2:
                    continue
                e = L.est.get(n)
                if e is None or e.bends or e.hits or not (e.hs or e.vs):
                    continue
                for q, _ in info.terminals:
                    if q not in seen and q in L.active:
                        seen.add(q)
                        group.append(q)
        return group if len(group) <= limit else [p]

    def _reside_group(self, b: str) -> dict[str, dict[str, Side]] | None:
        """A box and every box wired to it face each other again (both ends
        of a bundle flip together -- one end alone would only get worse)."""
        L = self.L
        out: dict[str, dict[str, Side]] = {}
        for q in [b] + [q for q in L.box_neighbors[b] if q in L.active]:
            geo = L.geo_sides(q)
            if geo != L.sides_of(q):
                out[q] = geo
        return out or None

    def _random_move(self) -> tuple[dict[str, tuple[Pos, int]], dict[str, dict[str, Side]]] | None:
        mv = self._random_move_pos()
        if mv == "sides":
            sd = self._side_move()
            return ({}, sd) if sd else None
        return (mv, {}) if mv else None

    def _random_move_pos(self):
        L, rng = self.L, self.rng
        parts = L.parts
        p = parts[rng.randrange(len(parts))]
        var = L.var[p]
        x, y = L.pos[p]
        r = rng.random()
        if r < 0.1:
            return "sides"
        if r < 0.3:
            dx, dy = 0, 0
            while dx == 0 and dy == 0:
                k = rng.choice((1, 1, 1, 2, 3))
                dx, dy = rng.choice(((k, 0), (-k, 0), (0, k), (0, -k), (k, k), (-k, k), (k, -k), (-k, -k)))
            target = (x + dx, y + dy)
            axis = None
        elif r < 0.55:
            links = self._links(p)
            if not links:
                return None
            pid, q, qp = links[rng.randrange(len(links))]
            s = L.shape[p]
            ps = s.pins[pid]
            (tx, ty), _ = L.terminal(q, qp)
            if ps.side in (Side.LEFT, Side.RIGHT):
                target = (x, ty - ps.ty)
                axis = 0
            else:
                target = (tx - ps.tx, y)
                axis = 1
            group = self.rigid_group(p)
            if len(group) > 1 and q not in group and rng.random() < 0.5:
                # move the whole straight-wired chain so this wire gets straight too
                dx, dy = target[0] - x, target[1] - y
                if dx == 0 and dy == 0:
                    return None
                return {g: ((L.pos[g][0] + dx, L.pos[g][1] + dy), L.var[g]) for g in group}
            if rng.random() < 0.4:
                gap = self.cfg.clearance + rng.choice((1, 2, 3, 4, 6))
                attached = self._attach(p, var, pid, q, qp, gap)
                if attached is not None:
                    target = attached
                    axis = None
        elif r < 0.7:
            nbrs = [q for q in L.nl.neighbors[p] if q in L.active]
            if not nbrs:
                return None
            q = nbrs[rng.randrange(len(nbrs))]
            if len(L.models[p].variants) > 1 and rng.random() < 0.3:
                var = rng.choice(L.models[p].variants)
            target = self._beside(
                p,
                var,
                q,
                rng.choice((Side.LEFT, Side.RIGHT, Side.TOP, Side.BOTTOM)),
                rng.choice((0, 1, 2, 4)),
            )
            axis = None
        elif r < 0.8:
            q = parts[rng.randrange(len(parts))]
            if q == p:
                return None
            cp, cq = L.center2(p), L.center2(q)
            return {
                p: (self._pos_for_center(p, var, cq), var),
                q: (self._pos_for_center(q, L.var[q], cp), L.var[q]),
            }
        elif r < 0.93:
            vs = [v for v in L.models[p].variants if v != var]
            if not vs:
                return None
            var = rng.choice(vs)
            target = self._pos_for_center(p, var, L.center2(p))
            axis = None
        else:
            # Slide everything on one side of a line: opens or closes a
            # corridor without breaking any alignment.
            horizontal = rng.random() < 0.5
            f = L.foot(p)
            line = f[0] if horizontal else f[1]
            d = rng.choice((-2, -1, 1, 2))
            out = {}
            for q in parts:
                fq = L.foot(q)
                if (fq[0] if horizontal else fq[1]) >= line:
                    qx, qy = L.pos[q]
                    out[q] = ((qx + d, qy) if horizontal else (qx, qy + d), L.var[q])
            return out or None
        legal = self._near_legal(p, var, target, 4 if axis is not None else 3, axis)
        if legal is None or (legal == (x, y) and var == L.var[p]):
            return None
        return {p: (legal, var)}

    def anneal(self, moves: int) -> None:
        L, rng = self.L, self.rng
        # Initial temperature from the typical size of uphill steps.
        ups: list[float] = []
        for _ in range(60):
            mv = self._random_move()
            if not mv:
                continue
            res = L.apply(mv[0], sides=mv[1])
            if res is None:
                continue
            d, tok = res
            L.revert(tok)
            if d > 0:
                ups.append(d)
        t0 = self.cfg.start_temperature * ((sum(ups) / len(ups)) if ups else 1.0)
        t_end = 0.01
        best_cost = L.total
        best_snap = L.snapshot()
        for i in range(moves):
            temp = t0 * (t_end / t0) ** (i / max(1, moves - 1)) if t0 > t_end else t_end
            mv = self._random_move()
            if not mv:
                continue
            res = L.apply(mv[0], sides=mv[1])
            if res is None:
                continue
            d, tok = res
            if d <= 0 or rng.random() < math.exp(-d / temp):
                if L.total < best_cost - 1e-9:
                    best_cost = L.total
                    best_snap = L.snapshot()
            else:
                L.revert(tok)
        L.load(best_snap)

    # --------------------------------------------------------------- polish
    def polish(self, rounds: int = 6) -> None:
        L = self.L
        for _ in range(rounds):
            improved = False
            for p in L.parts:
                best: dict[str, tuple[Pos, int]] | None = None
                best_d = -1e-6
                x, y = L.pos[p]
                cands: list[tuple[Pos, int]] = []
                for var in L.models[p].variants:
                    if var != L.var[p]:
                        cands.append((self._pos_for_center(p, var, L.center2(p)), var))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    cands.append(((x + dx, y + dy), L.var[p]))
                for pid, q, qp in self._links(p):
                    ps = L.shape[p].pins[pid]
                    (tx, ty), _ = L.terminal(q, qp)
                    if ps.side in (Side.LEFT, Side.RIGHT):
                        cands.append(((x, ty - ps.ty), L.var[p]))
                    else:
                        cands.append(((tx - ps.tx, y), L.var[p]))
                for pos, var in dict.fromkeys(cands):
                    if pos == (x, y) and var == L.var[p]:
                        continue
                    res = L.apply({p: (pos, var)})
                    if res is None:
                        continue
                    d, tok = res
                    L.revert(tok)
                    if d < best_d:
                        best_d, best = d, {p: (pos, var)}
                # straight-wired chains move as a whole
                group = self.rigid_group(p)
                if len(group) > 1:
                    for pid, q, qp in self._links(p):
                        if q in group:
                            continue
                        ps = L.shape[p].pins[pid]
                        (tx, ty), _ = L.terminal(q, qp)
                        if ps.side in (Side.LEFT, Side.RIGHT):
                            dx, dy = 0, ty - (y + ps.ty)
                        else:
                            dx, dy = tx - (x + ps.tx), 0
                        if dx == 0 and dy == 0:
                            continue
                        mv = {g: ((L.pos[g][0] + dx, L.pos[g][1] + dy), L.var[g]) for g in group}
                        res = L.apply(mv)
                        if res is None:
                            continue
                        d, tok = res
                        L.revert(tok)
                        if d < best_d:
                            best_d, best = d, mv
                if best is not None:
                    L.apply(best)
                    improved = True
                # whole box (and its partner boxes) facing their peers again
                if L.models[p].free:
                    for sd in (({p: L.geo_sides(p)}), self._reside_group(p)):
                        if not sd or all(sd[q] == L.sides_of(q) for q in sd):
                            continue
                        res = L.apply({}, sides=sd)
                        if res is None:
                            continue
                        d, tok = res
                        if d < -1e-6:
                            improved = True
                        else:
                            L.revert(tok)
                # free pin sides
                if L.models[p].free:
                    flip = {Side.LEFT: Side.RIGHT, Side.RIGHT: Side.LEFT}
                    for pid in L.models[p].free:
                        cur = L.sides_of(p)[pid]
                        if cur not in flip:
                            continue
                        res = L.apply({}, sides={p: {pid: flip[cur]}})
                        if res is None:
                            continue
                        d, tok = res
                        if d < -1e-6:
                            improved = True
                        else:
                            L.revert(tok)
            if not improved:
                break


def construction_order(parts: list[str], nl: Netlist) -> list[str]:
    """Most connected part first, then always the part most strongly wired
    to what is already placed (ties: more pins, then input order)."""
    idx = {p: i for i, p in enumerate(parts)}
    strength = {p: sum(nl.neighbors[p].get(q, 0.0) for q in parts if q != p) for p in parts}
    order = [max(parts, key=lambda p: (strength[p], len(nl.part_nets.get(p, [])), -idx[p]))]
    placed = set(order)
    while len(order) < len(parts):

        def key(p: str):
            s = sum(nl.neighbors[p].get(q, 0.0) for q in placed)
            return (s, strength[p], -idx[p])

        nxt = max((p for p in parts if p not in placed), key=key)
        order.append(nxt)
        placed.add(nxt)
    return order


def place_group(
    parts: list[str], models: dict[str, PartModel], nl: Netlist, cfg: LayoutConfig, seed: int
) -> Layout:
    """Places one connected sub-circuit; returns the final layout state."""
    layout = Layout(models, nl, cfg, parts)
    placer = Placer(layout, random.Random(seed))
    placer.analytic(construction_order(parts, nl), reach=6.0, jitter=3.0)
    layout.recompute()
    if len(parts) > 1:
        placer.polish(rounds=3)
        placer.anneal(cfg.moves_per_part * len(parts))
        placer.polish()
    return layout
