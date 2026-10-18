"""Fast routing estimates used as the placement objective.

Placement cannot afford to run the real router for every candidate move, so
it scores a placement with *estimated* routes: every net is decomposed into
pin-to-pin connections (a Manhattan MST for multi-pin nets) and each
connection gets the minimal-bend orthogonal shape that respects both pins'
escape directions (straight, L, Z, U or S shape).  The estimate is ignorant of
other wires, but it captures what placement controls: whether pins face each
other, whether connected pins are aligned, whether a route must cut through
or wrap around a component, and which connections will cross.

Costs are kept per net and per pair of nets, so moving one component only
re-evaluates the nets attached to it (incremental evaluation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from schematic.models import Side
from schematic.layout.config import PlacementWeights
from schematic.layout.pin_allocator import OUTWARD, snap

Pt = Tuple[float, float]
# Normalised axis-parallel segment (x1 <= x2, y1 <= y2) plus the direction of the
# perpendicular "stub" leaving each end: for a vertical segment -1 = left,
# +1 = right; for a horizontal one -1 = up, +1 = down; 0 = none (pin end).
Seg = Tuple[float, float, float, float, int, int]
Rect = Tuple[float, float, float, float]  # x1, y1, x2, y2

ESCAPE = 20.0


def route_shape(a: Pt, aside: Side, b: Pt, bside: Side) -> Tuple[int, List[Pt]]:
    """Minimal-bend orthogonal polyline from pin ``a`` to pin ``b``.

    The first segment leaves ``a`` along its outward direction and the last
    one enters ``b`` against ``b``'s outward direction.
    """
    (ax, ay), (bx, by) = a, b
    dax, day = OUTWARD[aside]
    dbx, dby = OUTWARD[bside]
    a1 = (ax + dax * ESCAPE, ay + day * ESCAPE)
    b1 = (bx + dbx * ESCAPE, by + dby * ESCAPE)

    if day == 0 and dby == 0:
        return _parallel_axes(a, b, a1, b1, dax, dbx, horizontal=True)
    if dax == 0 and dbx == 0:
        pts_t = _parallel_axes((ay, ax), (by, bx), (a1[1], a1[0]), (b1[1], b1[0]), day, dby, horizontal=True)
        return pts_t[0], [(p[1], p[0]) for p in pts_t[1]]
    # Perpendicular escape directions: an L when the corner lies ahead of both pins.
    corner = (bx, ay) if day == 0 else (ax, by)
    ahead_a = (corner[0] - ax) * dax + (corner[1] - ay) * day > 0
    ahead_b = (corner[0] - bx) * dbx + (corner[1] - by) * dby > 0
    if ahead_a and ahead_b:
        return 1, [a, corner, b]
    if day == 0:
        return 3, [a, a1, (a1[0], b1[1]), b1, b]
    return 3, [a, a1, (b1[0], a1[1]), b1, b]


def _parallel_axes(a: Pt, b: Pt, a1: Pt, b1: Pt, da: int, db: int, horizontal: bool) -> Tuple[int, List[Pt]]:
    """Both pins escape along x (callers transpose for the y case)."""
    (ax, ay), (bx, by) = a, b
    if da == -db:
        if ay == by and (bx - ax) * da > 0:
            return 0, [a, b]
        # A Z needs its jog column between the two escape points; closer than
        # that the wire has to go round (S shape).
        if (b1[0] - a1[0]) * da >= 0:
            mx = snap((a1[0] + b1[0]) / 2.0)
            if (mx - a1[0]) * da < 0:
                mx = a1[0]
            return 2, [a, (mx, ay), (mx, by), b]
        # b sits behind a: route around through the space between the rows.
        my = snap((ay + by) / 2.0) if ay != by else ay + 3 * ESCAPE
        return 4, [a, a1, (a1[0], my), (b1[0], my), b1, b]
    # Same escape direction: a U shape that turns beyond the outermost pin.
    ux = max(a1[0], b1[0]) if da > 0 else min(a1[0], b1[0])
    if ay == by:
        return 4, [a, a1, (a1[0], ay - 3 * ESCAPE), (b1[0], ay - 3 * ESCAPE), b1, b]
    return 2, [a, (ux, ay), (ux, by), b]


def _clean_ortho(pts: Sequence[Pt]) -> List[Pt]:
    out: List[Pt] = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
        while len(out) >= 3 and (
            (out[-3][0] == out[-2][0] == out[-1][0]) or (out[-3][1] == out[-2][1] == out[-1][1])
        ):
            out.pop(-2)
    return out


def _detour_poly(
    a: Pt,
    aside: Side,
    b: Pt,
    bside: Side,
    poly: List[Pt],
    new_segs: List[Seg],
    rects: Dict[str, Rect],
) -> Tuple[List[Pt], List[Seg], Set[str]]:
    """When an estimated route cuts through a component body, replace it with
    the shorter perimeter detour around that body so crossing estimates reflect
    where the wire actually goes rather than slicing across the body's pins."""
    px1, py1 = min(p[0] for p in poly), min(p[1] for p in poly)
    px2, py2 = max(p[0] for p in poly), max(p[1] for p in poly)
    h_rects: List[Rect] = []
    v_rects: List[Rect] = []
    hit_ids: Set[str] = set()
    for cid, r in rects.items():
        if r[0] >= px2 or r[2] <= px1 or r[1] >= py2 or r[3] <= py1:
            continue
        for sg in new_segs:
            if seg_hits_rect(sg, r):
                hit_ids.add(cid)
                if sg[1] == sg[3]:
                    h_rects.append(r)
                else:
                    v_rects.append(r)
    if not hit_ids:
        return poly, new_segs, hit_ids
    dax, day = OUTWARD[aside]
    dbx, dby = OUTWARD[bside]
    a1 = (a[0] + dax * ESCAPE, a[1] + day * ESCAPE)
    b1 = (b[0] + dbx * ESCAPE, b[1] + dby * ESCAPE)
    if h_rects:
        ry1 = min(r[1] for r in h_rects)
        ry2 = max(r[3] for r in h_rects)
        y_top, y_bot = ry1 - ESCAPE, ry2 + ESCAPE
        yd = y_top if abs(a1[1] - y_top) + abs(b1[1] - y_top) <= abs(a1[1] - y_bot) + abs(b1[1] - y_bot) else y_bot
        cand = _clean_ortho([a, a1, (a1[0], yd), (b1[0], yd), b1, b])
        cand_segs = polyline_segments(cand)
        if not any(seg_hits_rect(sg, r) for r in h_rects for sg in cand_segs):
            return cand, cand_segs, hit_ids
    if v_rects:
        rx1 = min(r[0] for r in v_rects)
        rx2 = max(r[2] for r in v_rects)
        x_left, x_right = rx1 - ESCAPE, rx2 + ESCAPE
        xd = x_left if abs(a1[0] - x_left) + abs(b1[0] - x_left) <= abs(a1[0] - x_right) + abs(b1[0] - x_right) else x_right
        cand = _clean_ortho([a, a1, (xd, a1[1]), (xd, b1[1]), b1, b])
        cand_segs = polyline_segments(cand)
        if not any(seg_hits_rect(sg, r) for r in v_rects for sg in cand_segs):
            return cand, cand_segs, hit_ids
    return poly, new_segs, hit_ids


def polyline_segments(pts: Sequence[Pt]) -> List[Seg]:
    clean: List[Pt] = []
    for p in pts:
        if not clean or clean[-1] != p:
            clean.append(p)
    segs: List[Seg] = []
    for i in range(len(clean) - 1):
        (x1, y1), (x2, y2) = clean[i], clean[i + 1]
        prev = clean[i - 1] if i > 0 else None
        nxt = clean[i + 2] if i + 2 < len(clean) else None
        if x1 == x2:  # vertical: stubs are horizontal neighbours
            s1 = 0 if prev is None else (1 if prev[0] > x1 else -1 if prev[0] < x1 else 0)
            s2 = 0 if nxt is None else (1 if nxt[0] > x2 else -1 if nxt[0] < x2 else 0)
        else:
            s1 = 0 if prev is None else (1 if prev[1] > y1 else -1 if prev[1] < y1 else 0)
            s2 = 0 if nxt is None else (1 if nxt[1] > y2 else -1 if nxt[1] < y2 else 0)
        if (x1, y1) <= (x2, y2):
            segs.append((x1, y1, x2, y2, s1, s2))
        else:
            segs.append((x2, y2, x1, y1, s2, s1))
    return segs


def detour_cost(sg: Seg, r: Rect, w: PlacementWeights) -> float:
    """Approximate cost of the detour the router makes around a body that the
    estimated segment cuts through: two extra bends plus the way round."""
    if sg[1] == sg[3]:
        side = min(sg[1] - r[1], r[3] - sg[1])
    else:
        side = min(sg[0] - r[0], r[2] - sg[0])
    return 2.0 * w.bend + w.length * 2.0 * (side / 20.0 + 1.0)


def polyline_length(pts: Sequence[Pt]) -> float:
    return sum(abs(x2 - x1) + abs(y2 - y1) for (x1, y1), (x2, y2) in zip(pts, pts[1:]))


def seg_hits_rect(s: Seg, r: Rect) -> bool:
    """True when the segment passes through the open interior of the rectangle."""
    x1, y1, x2, y2 = s[0], s[1], s[2], s[3]
    rx1, ry1, rx2, ry2 = r
    if y1 == y2:
        return ry1 < y1 < ry2 and max(x1, rx1) < min(x2, rx2)
    return rx1 < x1 < rx2 and max(y1, ry1) < min(y2, ry2)


def _shared_line_crossings(a: Seg, b: Seg, lo_i: int, hi_i: int) -> int:
    """Crossings forced between two collinear, overlapping segments.

    The router will put them on neighbouring tracks in whichever order is
    better; for each order count the end stubs that pierce the other
    segment's span, and return the smaller count (exact for a pair)."""
    a_lo, a_hi, b_lo, b_hi = a[lo_i], a[hi_i], b[lo_i], b[hi_i]

    def pierce(stub_seg: Seg, s_lo: float, s_hi: float, other_lo: float, other_hi: float, toward: int) -> int:
        n = 0
        if stub_seg[4] == toward and other_lo < s_lo < other_hi:
            n += 1
        if stub_seg[5] == toward and other_lo < s_hi < other_hi:
            n += 1
        return n

    # a on the low side (left / above) of b: b's stubs towards a, a's stubs towards b.
    first = pierce(b, b_lo, b_hi, a_lo, a_hi, -1) + pierce(a, a_lo, a_hi, b_lo, b_hi, 1)
    second = pierce(a, a_lo, a_hi, b_lo, b_hi, -1) + pierce(b, b_lo, b_hi, a_lo, a_hi, 1)
    return min(first, second)


def count_crossings(sa: Sequence[Seg], sb: Sequence[Seg]) -> float:
    """Crossings between the estimated routes of two different nets."""
    ha = [x for x in sa if x[1] == x[3]]
    va = [x for x in sa if x[1] != x[3]]
    hb = [x for x in sb if x[1] == x[3]]
    vb = [x for x in sb if x[1] != x[3]]
    return crossings_split(ha, va, hb, vb)


def crossings_split(ha: Sequence[Seg], va: Sequence[Seg], hb: Sequence[Seg], vb: Sequence[Seg]) -> float:
    n = 0.0
    for h in ha:
        hx1, hy, hx2 = h[0], h[1], h[2]
        for v in vb:
            vx = v[0]
            if hx1 < vx < hx2 and v[1] < hy < v[3]:
                n += 1
        for h2 in hb:
            if h2[1] == hy and min(hx2, h2[2]) - max(hx1, h2[0]) > 0:
                n += _shared_line_crossings(h, h2, 0, 2)
    for v in va:
        vx, vy1, vy2 = v[0], v[1], v[3]
        for h in hb:
            if vy1 < h[1] < vy2 and h[0] < vx < h[2]:
                n += 1
        for v2 in vb:
            if v2[0] == vx and min(vy2, v2[3]) - max(vy1, v2[1]) > 0:
                n += _shared_line_crossings(v, v2, 1, 3)
    return n


def mst_pairs(points: Sequence[Pt]) -> List[Tuple[int, int]]:
    """Deterministic Prim MST over Manhattan distances."""
    n = len(points)
    if n < 2:
        return []
    best: Dict[int, Tuple[float, int]] = {}
    for j in range(1, n):
        best[j] = (abs(points[j][0] - points[0][0]) + abs(points[j][1] - points[0][1]), 0)
    pairs: List[Tuple[int, int]] = []
    while best:
        j = min(best, key=lambda k: (best[k][0], k))
        _, i = best.pop(j)
        pairs.append((i, j))
        for k in best:
            d = abs(points[k][0] - points[j][0]) + abs(points[k][1] - points[j][1])
            if d < best[k][0]:
                best[k] = (d, j)
    return pairs


@dataclass
class NetEstimate:
    cost: float = 0.0
    bends: int = 0
    segments: List[Seg] = field(default_factory=list)
    bbox: Rect = (0.0, 0.0, 0.0, 0.0)
    hits: FrozenSet[str] = frozenset()
    h: List[Seg] = field(default_factory=list)
    v: List[Seg] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.segments and not self.h and not self.v:
            self.h = [x for x in self.segments if x[1] == x[3]]
            self.v = [x for x in self.segments if x[1] != x[3]]


@dataclass
class Terminal:
    comp: str
    pin: str


class Estimator:
    """Incremental estimated-routing objective over component positions.

    ``pins[comp][pin] = (dx, dy, side)`` are terminal offsets relative to the
    component's top-left corner; ``rects[comp] = (dx1, dy1, dx2, dy2)`` the
    body extents relative to the same corner.  Positions live in ``pos``.
    """

    def __init__(
        self,
        nets: Dict[str, List[Terminal]],
        factors: Dict[str, float],
        pins: Dict[str, Dict[str, Tuple[float, float, Side]]],
        rects: Dict[str, Rect],
        pos: Dict[str, Pt],
        weights: PlacementWeights,
        follow: Optional[Dict[str, Tuple[str, float, float]]] = None,
        anchor_pos: Optional[Dict[str, Pt]] = None,
        roots: Optional[Dict[str, int]] = None,
    ):
        self.nets = {k: v for k, v in nets.items() if len(v) >= 2}
        # Multi-pin nets with a root terminal are estimated as a star from that
        # pin (how buses are drawn: taps off the driver's line); the others,
        # e.g. power rails, as a Manhattan MST.
        self.roots = roots or {}
        self.factors = factors
        self.pins = pins
        self.rects = rects
        self.pos = dict(pos)
        self.w = weights
        # Satellites follow their anchor component: follow[sat] = (anchor, dx, dy).
        self.follow = follow or {}
        self.anchor_pos = dict(anchor_pos or pos)
        self.comp_nets: Dict[str, List[str]] = {}
        for name in sorted(self.nets):
            for t in self.nets[name]:
                owner = self.follow[t.comp][0] if t.comp in self.follow else t.comp
                lst = self.comp_nets.setdefault(owner, [])
                if name not in lst:
                    lst.append(name)
        self.est: Dict[str, NetEstimate] = {}
        self.cross: Dict[Tuple[str, str], int] = {}
        self.names = sorted(self.nets)
        self.recompute_all()

    # ----------------------------------------------------------------- geometry
    def abs_pos(self, comp: str) -> Pt:
        if comp in self.follow:
            anchor, dx, dy = self.follow[comp]
            ax, ay = self.pos[anchor]
            return ax + dx, ay + dy
        return self.pos[comp]

    def abs_rect(self, comp: str) -> Rect:
        x, y = self.abs_pos(comp)
        r = self.rects[comp]
        return (x + r[0], y + r[1], x + r[2], y + r[3])

    def terminal_point(self, t: Terminal) -> Tuple[float, float, Side]:
        x, y = self.abs_pos(t.comp)
        dx, dy, side = self.pins[t.comp][t.pin]
        return x + dx, y + dy, side

    # ------------------------------------------------------------- evaluation
    def estimate_net(self, name: str, rects: Dict[str, Rect]) -> NetEstimate:
        terms = self.nets[name]
        pts = [self.terminal_point(t) for t in terms]
        if len(pts) == 2:
            pairs = [(0, 1)]
        elif name in self.roots:
            r = self.roots[name]
            pairs = [(r, j) for j in range(len(pts)) if j != r]
        else:
            pairs = mst_pairs([(p[0], p[1]) for p in pts])
        factor = self.factors.get(name, 1.0)
        w = self.w
        cost = 0.0
        bends_total = 0
        segs: List[Seg] = []
        hits: Set[str] = set()
        for i, j in pairs:
            ax, ay, aside = pts[i]
            bx, by, bside = pts[j]
            raw_bends, poly = route_shape((ax, ay), aside, (bx, by), bside)
            new_segs = polyline_segments(poly)
            poly, new_segs, detoured = _detour_poly((ax, ay), aside, (bx, by), bside, poly, new_segs, rects)
            hits.update(detoured)
            bends = max(0, len(poly) - 2) if detoured else raw_bends
            bends_total += bends
            length = polyline_length(poly)
            c = w.bend * bends + w.length * length / 20.0
            if raw_bends == 2 and poly[1][1] == ay and abs(ay - by) <= 20.0 and aside in (Side.LEFT, Side.RIGHT):
                c += w.near_miss
            elif raw_bends == 2 and poly[1][0] == ax and abs(ax - bx) <= 20.0 and aside in (Side.TOP, Side.BOTTOM):
                c += w.near_miss
            if raw_bends >= 3:
                c += w.misfacing
            px1, py1 = min(p[0] for p in poly), min(p[1] for p in poly)
            px2, py2 = max(p[0] for p in poly), max(p[1] for p in poly)
            for cid, r in rects.items():
                if r[0] >= px2 or r[2] <= px1 or r[1] >= py2 or r[3] <= py1:
                    continue
                for sg in new_segs:
                    if seg_hits_rect(sg, r):
                        hits.add(cid)
                        c += detour_cost(sg, r, w)
            cost += c
            segs.extend(new_segs)
        cost = cost * factor + w.obstacle * len(hits) * max(factor, 0.5)
        if segs:
            bbox = (min(s[0] for s in segs), min(s[1] for s in segs), max(s[2] for s in segs), max(s[3] for s in segs))
        else:
            bbox = (0.0, 0.0, 0.0, 0.0)
        return NetEstimate(cost=cost, bends=bends_total, segments=segs, bbox=bbox, hits=frozenset(hits))

    def _pair_crossings(self, a: NetEstimate, b: NetEstimate) -> int:
        if a.bbox[2] < b.bbox[0] or b.bbox[2] < a.bbox[0] or a.bbox[3] < b.bbox[1] or b.bbox[3] < a.bbox[1]:
            return 0
        return crossings_split(a.h, a.v, b.h, b.v)

    def _pair_weight(self, a: str, b: str) -> float:
        fa = self.factors.get(a, 1.0)
        fb = self.factors.get(b, 1.0)
        return self.w.crossing * fa * fb

    def all_rects(self) -> Dict[str, Rect]:
        return {c: self.abs_rect(c) for c in self.rects}

    def recompute_all(self) -> None:
        rects = self.all_rects()
        self.est = {n: self.estimate_net(n, rects) for n in self.names}
        self.cross = {}
        for i, a in enumerate(self.names):
            for b in self.names[i + 1:]:
                c = self._pair_crossings(self.est[a], self.est[b])
                if c:
                    self.cross[(a, b)] = c

    def total(self) -> float:
        net_cost = sum(e.cost for e in self.est.values())
        cross_cost = sum(self._pair_weight(a, b) * c for (a, b), c in self.cross.items())
        return net_cost + cross_cost + self.global_cost()

    def global_cost(self) -> float:
        rects = list(self.all_rects().values())
        if not rects:
            return 0.0
        x1 = min(r[0] for r in rects)
        y1 = min(r[1] for r in rects)
        x2 = max(r[2] for r in rects)
        y2 = max(r[3] for r in rects)
        w = max(1.0, (x2 - x1) / 20.0)
        h = max(1.0, (y2 - y1) / 20.0)
        aspect = max(w / h, h / w)
        cost = self.w.area * w * h
        if aspect > self.w.max_aspect:
            cost += self.w.aspect * (aspect - self.w.max_aspect)
        if self.w.stability:
            for c, (px, py) in self.pos.items():
                ax, ay = self.anchor_pos.get(c, (px, py))
                cost += self.w.stability * (abs(px - ax) + abs(py - ay)) / 20.0
        return cost

    def move_delta(self, moves: Dict[str, Pt], bound: float = float("inf")) -> Tuple[float, dict]:
        """Cost change of moving components; returns (delta, undo/commit token).

        With a finite ``bound`` the evaluation stops early (returning +inf)
        as soon as the move provably cannot beat it: even if every crossing
        of the affected nets disappeared, the delta would stay above ``bound``.
        The token can always be passed to ``revert``."""
        before_pos = {c: self.pos[c] for c in moves}
        old_total_global = self.global_cost()
        moved: Set[str] = set(moves)
        affected: List[str] = []
        for c in sorted(moved):
            for n in self.comp_nets.get(c, ()):
                if n not in affected:
                    affected.append(n)
        old_rects = {c: self.abs_rect(c) for c in self._moved_with_followers(moved)}
        for c, p in moves.items():
            self.pos[c] = p
        new_rects_all = self.all_rects()
        new_rects = {c: new_rects_all[c] for c in old_rects}

        new_est: Dict[str, NetEstimate] = {}
        for n in affected:
            new_est[n] = self.estimate_net(n, new_rects_all)
        # Unaffected nets only change through bodies moving onto / off their routes.
        for n in self.names:
            if n in new_est:
                continue
            e = self.est[n]
            bb = e.bbox
            for c, r in new_rects.items():
                if c not in e.hits and (r[0] >= bb[2] or r[2] <= bb[0] or r[1] >= bb[3] or r[3] <= bb[1]):
                    continue
                hit_now = any(seg_hits_rect(sg, r) for sg in e.segments)
                if hit_now or c in e.hits:
                    new_est[n] = self.estimate_net(n, new_rects_all)
                    break
        delta = sum(new_est[n].cost - self.est[n].cost for n in new_est)
        delta += self.global_cost() - old_total_global
        aff_set = set(new_est)
        if bound != float("inf"):
            removable = sum(self._pair_weight(*key) * c for key, c in self.cross.items()
                            if key[0] in aff_set or key[1] in aff_set)
            if delta - removable > bound:
                return float("inf"), {"before_pos": before_pos, "new_est": {}, "new_cross": {}}
        new_cross: Dict[Tuple[str, str], int] = {}
        for a in new_est:
            ea = new_est[a]
            for b in self.names:
                if b == a or (b in aff_set and b < a):
                    continue
                eb = new_est.get(b, self.est[b])
                key = (a, b) if a < b else (b, a)
                c = self._pair_crossings(ea, eb)
                new_cross[key] = c
        for key, c in new_cross.items():
            delta += self._pair_weight(*key) * (c - self.cross.get(key, 0))
        token = {"before_pos": before_pos, "new_est": new_est, "new_cross": new_cross}
        return delta, token

    def _moved_with_followers(self, moved: Set[str]) -> List[str]:
        out = sorted(moved)
        for sat, (anchor, _, _) in self.follow.items():
            if anchor in moved:
                out.append(sat)
        return out

    def commit(self, token: dict) -> None:
        for n, e in token["new_est"].items():
            self.est[n] = e
        for key, c in token["new_cross"].items():
            if c:
                self.cross[key] = c
            else:
                self.cross.pop(key, None)

    def revert(self, token: dict) -> None:
        for c, p in token["before_pos"].items():
            self.pos[c] = p

    def crossing_count(self) -> int:
        return sum(self.cross.values())
