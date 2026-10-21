"""Fast estimate of how a connection will be routed.

Placement needs to compare thousands of arrangements, so it cannot run the
real router each time.  Instead every pin-to-pin connection gets the cheapest
*route shape* that respects both pin directions -- straight, L, Z / U, three
or four bends -- with the free coordinates chosen from a small candidate set
(midpoints, just outside the bodies in the way).  The cost uses the same
weights as the router; a shape through a body is penalised, so placement
learns to keep corridors free.

All coordinates are integer grid units.
"""

from __future__ import annotations

from collections.abc import Sequence

Pt = tuple[int, int]
Rect = tuple[int, int, int, int]  # x0, y0, x1, y1 (closed body outline)
HSeg = tuple[int, int, int]  # y, x_lo, x_hi
VSeg = tuple[int, int, int]  # x, y_lo, y_hi


def clean(points: Sequence[Pt]) -> list[Pt]:
    """Drops repeated and collinear interior points.  Returns an empty list
    for a polyline that folds back onto itself (not drawable)."""
    out: list[Pt] = []
    for p in points:
        if out and out[-1] == p:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == p[0]) or (a[1] == b[1] == p[1]):
                if (b[0] - a[0]) * (p[0] - b[0]) < 0 or (b[1] - a[1]) * (p[1] - b[1]) < 0:
                    return []
                out[-1] = p
                continue
        out.append(p)
    return out


def length(points: Sequence[Pt]) -> int:
    return sum(
        abs(points[i + 1][0] - points[i][0]) + abs(points[i + 1][1] - points[i][1])
        for i in range(len(points) - 1)
    )


def hits(points: Sequence[Pt], rects: Sequence[Rect]) -> int:
    """Number of (segment, body) pairs where the segment runs over the body
    for a positive length (touching an outline at a single point is fine:
    that is how a wire leaves a symbol's terminal)."""
    n = 0
    for i in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        if y1 == y2:
            lo, hi = (x1, x2) if x1 < x2 else (x2, x1)
            for r in rects:
                if r[1] <= y1 <= r[3] and max(lo, r[0]) < min(hi, r[2]):
                    n += 1
        else:
            lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
            for r in rects:
                if r[0] <= x1 <= r[2] and max(lo, r[1]) < min(hi, r[3]):
                    n += 1
    return n


def point_hits(points: Sequence[Pt], marks: Sequence[Pt]) -> int:
    """Number of marked grid nodes (foreign terminals and the nodes in front
    of foreign pins) that the polyline passes through."""
    n = 0
    for i in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        if y1 == y2:
            lo, hi = (x1, x2) if x1 < x2 else (x2, x1)
            for mx, my in marks:
                if my == y1 and lo <= mx <= hi:
                    n += 1
        else:
            lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
            for mx, my in marks:
                if mx == x1 and lo <= my <= hi:
                    n += 1
    return n


def _ahead(v: int, origin: int, s: int) -> bool:
    return (v - origin) * s >= 1


def _interval(ax: int, s: int, bx: int, t: int) -> tuple[int | None, int | None]:
    """Feasible range of a coordinate that must lie ahead of ``ax`` along
    ``s`` and ahead of ``bx`` along ``t``."""
    lo: int | None = None
    hi: int | None = None
    for origin, d in ((ax, s), (bx, t)):
        if d > 0:
            lo = origin + 1 if lo is None else max(lo, origin + 1)
        else:
            hi = origin - 1 if hi is None else min(hi, origin - 1)
    return lo, hi


def _in(v: int, lo: int | None, hi: int | None) -> bool:
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def _shapes_h(
    a: Pt, s: int, b: Pt, db: Pt, ra: Rect | None, rb: Rect | None, near: Sequence[Rect], tier: int
) -> list[list[Pt]]:
    """Candidate polylines with ``tier`` bends (0..4) when the first pin
    points along x (direction ``s``)."""
    ax, ay = a
    bx, by = b
    out: list[list[Pt]] = []
    if db[1] == 0:
        t = db[0]
        if tier == 0:
            if ay == by and t == -s and _ahead(bx, ax, s):
                out.append([a, b])
        elif tier == 2:
            if ay == by:
                return out
            lo, hi = _interval(ax, s, bx, t)
            if lo is None or hi is None or lo <= hi:
                if lo is not None and hi is not None:
                    ms = [(lo + hi) // 2, lo, hi]
                elif lo is not None:
                    ms = [lo, lo + 1]
                else:
                    assert hi is not None
                    ms = [hi, hi - 1]
                for r in near:
                    for m in (r[0] - 1, r[2] + 1):
                        if _in(m, lo, hi):
                            ms.append(m)
                for m in dict.fromkeys(ms):
                    out.append([a, (m, ay), (m, by), b])
        elif tier == 4:
            ns = [(ay + by) // 2]
            for ro in (ra, rb, *tuple(near)):
                if ro is not None:
                    ns += [ro[1] - 1, ro[3] + 1]
            for n in dict.fromkeys(ns):
                for m1 in (ax + s, ax + 2 * s):
                    for m2 in (bx + t, bx + 2 * t):
                        out.append([a, (m1, ay), (m1, n), (m2, n), (m2, by), b])
    else:
        t = db[1]
        if tier == 1:
            if _ahead(bx, ax, s) and _ahead(ay, by, t):
                out.append([a, (bx, ay), b])
        elif tier == 3:
            ms = [ax + s, ax + 2 * s]
            ns = [by + t, by + 2 * t]
            if _ahead(bx, ax, s):
                ms.append((ax + bx) // 2)
            for r in near:
                for m in (r[0] - 1, r[2] + 1):
                    if _ahead(m, ax, s):
                        ms.append(m)
                for n in (r[1] - 1, r[3] + 1):
                    if _ahead(n, by, t):
                        ns.append(n)
            for m in dict.fromkeys(ms):
                for n in dict.fromkeys(ns):
                    out.append([a, (m, ay), (m, n), (bx, n), b])
    return out


def _t(p: Pt) -> Pt:
    return (p[1], p[0])


def _tr(r: Rect | None) -> Rect | None:
    return None if r is None else (r[1], r[0], r[3], r[2])


_CACHE: dict[tuple, tuple[float, int, int, list[Pt]]] = {}


def route(
    a: Pt,
    da: Pt,
    b: Pt,
    db: Pt,
    rects: Sequence[Rect],
    bend_w: float,
    len_w: float,
    obst_w: float,
    ra: Rect | None = None,
    rb: Rect | None = None,
    marks: Sequence[Pt] = (),
) -> tuple[float, int, int, list[Pt]]:
    """Cheapest estimated route between two terminals.

    ``da`` / ``db`` are the outward directions of the pins, ``rects`` the
    bodies that may be in the way, ``ra`` / ``rb`` the bodies the two pins
    belong to, ``marks`` grid nodes no foreign wire may touch (terminals and
    the nodes in front of pins of other nets).  Returns ``(cost, bends,
    hits, polyline)``.

    Shapes are tried by number of bends; a tier is only generated while it
    can still beat the best shape found so far (bends dominate the cost)."""
    x0, x1 = min(a[0], b[0]) - 3, max(a[0], b[0]) + 3
    y0, y1 = min(a[1], b[1]) - 3, max(a[1], b[1]) + 3
    near = tuple(r for r in rects if r[0] <= x1 and r[2] >= x0 and r[1] <= y1 and r[3] >= y0)
    mk = tuple(m for m in marks if x0 <= m[0] <= x1 and y0 <= m[1] <= y1)
    key = (a, da, b, db, near, mk, ra, rb, bend_w, len_w)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    horizontal = da[0] != 0
    if horizontal:
        tiers = (0, 2, 4) if db[1] == 0 else (1, 3)
    else:
        tiers = (0, 2, 4) if db[0] == 0 else (1, 3)
    manhattan = abs(a[0] - b[0]) + abs(a[1] - b[1])
    best: tuple[float, int, int, list[Pt]] | None = None
    tn = None
    for tier in tiers:
        if best is not None and bend_w * tier + len_w * manhattan >= best[0]:
            break
        if horizontal:
            cands = _shapes_h(a, da[0], b, db, ra, rb, near, tier)
        else:
            if tn is None:
                tn = [_tr(r) for r in near]
            cands = [
                [_t(p) for p in poly]
                for poly in _shapes_h(_t(a), da[1], _t(b), _t(db), _tr(ra), _tr(rb), tn, tier)
            ]  # type: ignore[arg-type]
        scored = []
        for poly in cands:
            pts = clean(poly)
            if not pts:
                continue
            bends = len(pts) - 2
            scored.append((bend_w * bends + len_w * length(pts), bends, pts))
        scored.sort(key=lambda e: (e[0], e[1]))
        for base, bends, pts in scored:
            if best is not None and base >= best[0]:
                break
            h = hits(pts, near) + (point_hits(pts, mk) if mk else 0)
            cost = base + obst_w * h
            if best is None or cost < best[0]:
                best = (cost, bends, h, pts)
    if best is None:
        # Degenerate geometry: price as a detour of four bends.
        pts = [a, (a[0], b[1]), b] if a[0] != b[0] and a[1] != b[1] else [a, b]
        best = (bend_w * 4 + len_w * manhattan + obst_w, 4, 1, pts)
    if len(_CACHE) > 200000:
        _CACHE.clear()
    _CACHE[key] = best
    return best


def segments(points: Sequence[Pt]) -> tuple[list[HSeg], list[VSeg]]:
    hs: list[HSeg] = []
    vs: list[VSeg] = []
    for i in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        if y1 == y2:
            hs.append((y1, min(x1, x2), max(x1, x2)))
        else:
            vs.append((x1, min(y1, y2), max(y1, y2)))
    return hs, vs


def crossings(
    ha: Sequence[HSeg], va: Sequence[VSeg], hb: Sequence[HSeg], vb: Sequence[VSeg]
) -> tuple[int, int, int]:
    """(proper crossings, conflicts, steps side by side) between two nets'
    estimated segments.

    A conflict is a contact the router cannot draw: the nets share a line
    (collinear overlap) or one net's wire ends / turns on the other's.  The
    router must separate them, which in practice costs a crossing or a
    detour.  "Side by side" counts grid steps where the two run parallel one
    grid line apart."""
    c = 0
    o = 0
    adj = 0
    for y, xl, xh in ha:
        for x, yl, yh in vb:
            if xl <= x <= xh and yl <= y <= yh:
                if xl < x < xh and yl < y < yh:
                    c += 1
                else:
                    o += 1
        for y2, xl2, xh2 in hb:
            d = y - y2
            if -1 <= d <= 1:
                lo, hi = max(xl, xl2), min(xh, xh2)
                if d == 0:
                    if lo <= hi:
                        o += 1
                elif lo < hi:
                    adj += hi - lo
    for x, yl, yh in va:
        for y, xl, xh in hb:
            if xl <= x <= xh and yl <= y <= yh:
                if xl < x < xh and yl < y < yh:
                    c += 1
                else:
                    o += 1
        for x2, yl2, yh2 in vb:
            d = x - x2
            if -1 <= d <= 1:
                lo, hi = max(yl, yl2), min(yh, yh2)
                if d == 0:
                    if lo <= hi:
                        o += 1
                elif lo < hi:
                    adj += hi - lo
    return c, o, adj


def _transpose_all(pts: Sequence[Pt]) -> list[Pt]:
    return [(p[1], p[0]) for p in pts]


def _tap(p: Pt, d: Pt, s: int, owner: Rect | None) -> tuple[list[Pt], int, int]:
    """Standard shape joining pin ``p`` (direction ``d``) to a vertical trunk
    at x = ``s``; returns (polyline, bends, y where it meets the trunk)."""
    if d[0] != 0 and (s - p[0]) * d[0] >= 1:
        return [p, (s, p[1])], 0, p[1]  # straight
    if d[1] != 0:
        y = p[1] + d[1]
        if s == p[0]:
            return [p, (s, y)], 0, y  # pin points along the trunk
        return [p, (p[0], y), (s, y)], 1, y  # L
    # horizontal pin facing away from the trunk: U around its own body
    x1 = p[0] + d[0]
    if owner is not None:
        up = abs(p[1] - (owner[1] - 1))
        down = abs((owner[3] + 1) - p[1])
        y = owner[1] - 1 if up <= down else owner[3] + 1
    else:
        y = p[1] - 1
    return [p, (x1, p[1]), (x1, y), (s, y)], 2, y


def trunk(
    points: Sequence[Pt],
    dirs: Sequence[Pt],
    rects: Sequence[Rect],
    bend_w: float,
    len_w: float,
    obst_w: float,
    junction_w: float,
    owners: Sequence[Rect | None],
    marks: Sequence[Pt] = (),
    bound: float = float("inf"),
) -> tuple[float, int, int, list[list[Pt]]] | None:
    """Single-trunk Steiner tree estimate of a multi-terminal net.

    A bus or supply is drawn as one straight trunk with a branch to every
    pin; a pin facing the trunk joins it with a straight wire, others with
    an L or a U.  Both trunk orientations and a few trunk positions are
    tried; returns the cheapest ``(cost, bends, hits, polylines)`` or None
    when no trunk beats ``bound``."""
    best: tuple[float, int, int, list[list[Pt]]] | None = None
    for vertical in (True, False):
        pts = list(points) if vertical else _transpose_all(points)
        ds = list(dirs) if vertical else _transpose_all(dirs)
        if sum(1 for d in ds if d[0] != 0) < 2:
            continue
        rs = list(rects) if vertical else [(r[1], r[0], r[3], r[2]) for r in rects]
        os_ = list(owners) if vertical else [None if r is None else (r[1], r[0], r[3], r[2]) for r in owners]
        mk = list(marks) if vertical else _transpose_all(marks)
        xs = sorted(p[0] for p in pts)
        cands = {xs[len(xs) // 2]}
        for p, d in zip(pts, ds, strict=False):
            if d[0] != 0:
                cands.add(p[0] + 2 * d[0])
                cands.add(p[0] + 3 * d[0])
        # Cost of every trunk position by arithmetic only (the same numbers
        # the polylines of ``_tap`` give); polylines only for the best few.
        geo = []
        for p, d, o in zip(pts, ds, os_, strict=False):
            if d[0] == 0:
                geo.append((0, p, d, 0))
            else:
                if o is not None:
                    up = abs(p[1] - (o[1] - 1))
                    down = abs((o[3] + 1) - p[1])
                    uy = o[1] - 1 if up <= down else o[3] + 1
                else:
                    uy = p[1] - 1
                geo.append((1, p, d, uy))
        n_junction = junction_w * max(0, len(pts) - 2)
        scored = []
        for x in cands:
            if not xs[0] - 2 <= x <= xs[-1] + 2:
                continue
            bends = 2  # trunk ends turn into the outer taps
            total = 0
            y_min = y_max = None
            for kind, p, d, uy in geo:
                if kind == 0:  # vertical pin: along the trunk or an L
                    y = p[1] + d[1]
                    if x == p[0]:
                        total += 1
                    else:
                        total += 1 + abs(x - p[0])
                        bends += 1
                elif (x - p[0]) * d[0] >= 1:  # facing the trunk: straight
                    y = p[1]
                    total += abs(x - p[0])
                else:  # facing away: U
                    y = uy
                    total += 1 + abs(uy - p[1]) + abs(x - p[0] - d[0])
                    bends += 2
                if y_min is None or y < y_min:
                    y_min = y
                if y_max is None or y > y_max:
                    y_max = y
            assert y_min is not None and y_max is not None
            total += y_max - y_min
            scored.append((bend_w * bends + len_w * total + n_junction, x, bends))
        scored.sort(key=lambda e: (e[0], e[1]))
        for base, x, bends in scored[:4]:
            limit = bound if best is None else min(bound, best[0])
            if base >= limit:
                break
            polys = []
            ys = []
            for p, d, o in zip(pts, ds, os_, strict=False):
                poly, _, y = _tap(p, d, x, o)
                polys.append(poly)
                ys.append(y)
            polys.append([(x, min(ys)), (x, max(ys))])
            h = sum(hits(pl, rs) for pl in polys)
            if mk:
                h += sum(point_hits(pl[1:], mk) for pl in polys)
            cost = base + obst_w * h
            if cost < limit:
                out = polys if vertical else [_transpose_all(pl) for pl in polys]
                best = (cost, bends, h, out)
    return best
