"""Initial placements ("seeds") for the placer.

Two complementary strategies, mixed into ``Placer``:

* ``LayeredSeedMixin`` -- port-aware columns grown from the hub (in the
  spirit of Sugiyama-style layered drawing with port constraints): parts
  wired to a declared pin side are placed on that side, parts sharing a bus
  share a column, and within a column the most strongly connected part takes
  its exactly pin-aligned position first (priority method).
* ``StressSeedMixin`` -- stress majorization (Gansner, Koren & North) on the
  weighted connectivity graph, with declared pin sides as separation
  constraints.  Better for meshes and rings that have no single hub.

The host class provides ``comps``, ``cfg``, ``sats``, ``pair_nets`` and the
helpers ``_weights``, ``_footprint``, ``_terminals``, ``_owner``,
``_terminal_abs``, ``_relation`` and ``_bus_mates``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Set, Tuple

from schematic.models import GRID_SIZE, Side
from schematic.layout.pin_allocator import snap

Pt = Tuple[float, float]


class LayeredSeedMixin:
    def _embed_layered(self, g: _Group) -> None:
        """Port-aware column assignment grown from the hub (BFS by connection
        strength); parts sharing a bus land in the same column."""
        self._hub = g.hub
        comps = g.comps
        members = set(comps)
        w = self._weights(comps)
        p2p: Dict[str, Set[str]] = {c: set() for c in comps}
        for net in self.sch.nets:
            if self.sem.nets[net.name].is_rail:
                continue
            owners = {self._owner(c) for c, _ in self._terminals(net)} & members
            if len(owners) == 2:
                a, b = tuple(owners)
                p2p[a].add(b)
                p2p[b].add(a)
        col: Dict[str, int] = {g.hub: 0}
        rank: Dict[str, int] = {g.hub: 0}
        while len(col) < len(comps):
            unplaced = [c for c in comps if c not in col]
            nxt = max(unplaced, key=lambda c: (sum(w.get((c, p), 0.0) for p in col), -comps.index(c)))
            parent = max(col, key=lambda q: (w.get((nxt, q), 0.0), -comps.index(q)))
            rel = self._relation(parent, nxt)
            if rel is not None:
                c = col[parent] + rel[0]
            else:
                mates = [m for m in self._bus_mates(nxt) if m in col and m != parent]
                if mates:
                    counts: Dict[int, int] = {}
                    for m in mates:
                        counts[col[m]] = counts.get(col[m], 0) + 1
                    c = max(sorted(counts), key=lambda k: counts[k])
                elif col[parent] > 0:
                    c = col[parent] + 1
                elif col[parent] < 0:
                    c = col[parent] - 1
                else:
                    right = sum(1 for v in col.values() if v == col[parent] + 1)
                    left = sum(1 for v in col.values() if v == col[parent] - 1)
                    c = col[parent] + (1 if right <= left else -1)
            # Avoid placing a component in the same column as a peer it has a
            # dedicated 2-part link to (step outward from the hub instead).
            same_col_peers = [q for q in p2p[nxt] if col.get(q) == c]
            if same_col_peers:
                peer = max(same_col_peers, key=lambda q: (w.get((nxt, q), 0.0), -comps.index(q)))
                rel_p = self._relation(peer, nxt)
                if rel_p is not None and rel_p[0] != 0:
                    c = col[peer] + rel_p[0]
                elif c < col[g.hub]:
                    c -= 1
                elif c > col[g.hub]:
                    c += 1
            col[nxt] = c
            rank[nxt] = len(rank)
        # Propagate directed port-side constraints (left_c -> right_c implies col[left_c] < col[right_c]).
        dir_edges: List[Tuple[str, str]] = []
        for i, a in enumerate(comps):
            for b in comps[i + 1:]:
                if w.get((a, b), 0.0) <= 0:
                    continue
                rel = self._relation(a, b)
                if rel is not None and rel[0] != 0:
                    dir_edges.append((a, b) if rel[0] > 0 else (b, a))
        for _ in range(len(comps)):
            changed = False
            for u, v in dir_edges:
                if col[u] >= col[v]:
                    if col[v] <= col[g.hub] and u != g.hub:
                        col[u] = col[v] - 1
                        changed = True
                    elif col[u] >= col[g.hub] and v != g.hub:
                        col[v] = col[u] + 1
                        changed = True
            if not changed:
                break
        self._columns = col
        # Initial positions: columns left to right, discovery order top to bottom.
        y_cursor: Dict[int, float] = {}
        for cid in sorted(comps, key=lambda c: rank[c]):
            comp = self.comps[cid]
            c = col[cid]
            y = y_cursor.get(c, 0.0)
            g.pos[cid] = (c * 400.0, snap(y))
            y_cursor[c] = y + comp.height + self.cfg.spacing.min_v_gap + 40.0
        self._assign_coordinates(g)

    def _assign_coordinates(self, g: _Group) -> None:
        """Column x positions and pin-aligning y positions for a layered layout.

        Within each column the vertical order follows the barycentre of the
        connected pins; y values minimise the squared misalignment of
        connected pins subject to the ordering and spacing (pool adjacent
        violators / isotonic regression).  The hub column is the anchor."""
        col = self._columns
        if not col or set(col) != set(g.comps):
            return
        cols = sorted(set(col.values()))
        members = {c: [cid for cid in g.comps if col[cid] == c] for c in cols}
        fps = {cid: self._footprint(cid) for cid in g.comps}
        links = self._pin_links(g.comps)
        hub_col = col[g.hub]

        def ideal_top(cid: str, outward_only: bool = False) -> Optional[float]:
            num = den = 0.0
            my_dist = abs(col[cid] - hub_col)
            for (a, pa, b, pb, wt) in links.get(cid, ()):
                if col[b] == col[cid]:
                    continue
                b_dist = abs(col[b] - hub_col)
                if outward_only and b_dist >= my_dist:
                    continue
                pin_a = self.comps[a].pins[pa]
                pin_b = self.comps[b].pins[pb]
                if pin_a.side not in (Side.LEFT, Side.RIGHT) or pin_b.side not in (Side.LEFT, Side.RIGHT):
                    continue
                factor = 1.0 if b_dist <= my_dist else 0.25
                ya = pin_a.position.y - self.comps[a].y
                yb = g.pos[b][1] + pin_b.position.y - self.comps[b].y
                num += wt * factor * (yb - ya)
                den += wt * factor
            return num / den if den > 0 else None

        order = sorted(cols, key=lambda c: (abs(c - hub_col), c))
        for sweep in range(6):
            for c in order:
                ids = members[c]
                ideals = {cid: ideal_top(cid, outward_only=(sweep == 0)) for cid in ids}
                if sweep == 0:
                    # Barycentric order from hub outward (components without links keep their place).
                    ids.sort(key=lambda cid: (
                        (ideals[cid] if ideals[cid] is not None else g.pos[cid][1]) + fps[cid][1]
                        + (fps[cid][3] - fps[cid][1]) / 2.0,
                        g.comps.index(cid),
                    ))
                if c == hub_col and sweep == 0 and len(ids) == 1:
                    continue
                # Priority method preserving the barycentric order `ids`: the most
                # strongly connected part takes its exact pin-aligned position, and
                # earlier/later parts in `ids` stay strictly above/below it.
                strength = {cid: sum(l[4] for l in links.get(cid, ()) if col[l[2]] != c) for cid in ids}
                placed_idx: Dict[int, Tuple[float, float]] = {}
                prio = sorted(range(len(ids)), key=lambda i: (ids[i] != g.hub, -strength[ids[i]], g.comps.index(ids[i])))
                gap = self.cfg.spacing.min_v_gap
                heights = [fps[cid][3] - fps[cid][1] for cid in ids]
                for i in prio:
                    cid = ids[i]
                    f = fps[cid]
                    h = heights[i]
                    want = ideals[cid] if (ideals[cid] is not None and cid != g.hub) else g.pos[cid][1]
                    top = snap(want) + f[1]
                    lo = max(
                        (
                            snap_up(placed_idx[j][1] + gap + sum(heights[k] + gap for k in range(j + 1, i)))
                            for j in placed_idx if j < i
                        ),
                        default=-float("inf"),
                    )
                    hi = min(
                        (
                            snap_down(placed_idx[j][0] - gap - h - sum(heights[k] + gap for k in range(i + 1, j)))
                            for j in placed_idx if j > i
                        ),
                        default=float("inf"),
                    )
                    top = max(lo, min(hi, top))
                    placed_idx[i] = (top, top + h)
                    g.pos[cid] = (g.pos[cid][0], snap(top - f[1]))
        # Column x positions: widths from footprints, corridors sized by the
        # number of wires that have to jog between neighbouring columns.
        x = 0.0
        prev = None
        for c in cols:
            ids = members[c]
            width = max(fps[cid][2] - fps[cid][0] for cid in ids)
            if prev is not None:
                x += self._corridor(members[prev], ids, g)
            for cid in ids:
                f = fps[cid]
                facing = self._facing(cid, col)
                if facing > 0:
                    left = x + width - (f[2] - f[0])
                elif facing < 0:
                    left = x
                else:
                    left = x + (width - (f[2] - f[0])) / 2.0
                g.pos[cid] = (snap(left - f[0]), g.pos[cid][1])
            x += width
            prev = c

    def _pin_links(self, comps: List[str]) -> Dict[str, List[Tuple[str, str, str, str, float]]]:
        """Pin-to-pin links between placeable parts: (comp, pin, peer, peer_pin, weight)."""
        members = set(comps)
        links: Dict[str, List[Tuple[str, str, str, str, float]]] = {c: [] for c in comps}
        for net in self.sch.nets:
            info = self.sem.nets[net.name]
            terms = [(c, p) for c, p in self._terminals(net) if c in members]
            owners = {c for c, _ in terms}
            if len(owners) < 2:
                continue
            wt = (0.1 if info.is_rail else 1.0) / (len(owners) - 1)
            for a, pa in terms:
                for b, pb in terms:
                    if a != b:
                        links[a].append((a, pa, b, pb, wt))
        return links

    def _facing(self, cid: str, col: Dict[str, int]) -> int:
        """+1 when a part's connections lead to columns on its right, -1 left."""
        score = 0
        for (a, pa, b, pb, wt) in self._pin_links([cid] + [c for c in col if c != cid]).get(cid, ()):
            if col.get(b, col[cid]) > col[cid]:
                score += 1
            elif col.get(b, col[cid]) < col[cid]:
                score -= 1
        return (score > 0) - (score < 0)

    def _corridor(self, left_ids: List[str], right_ids: List[str], g: _Group) -> float:
        sp = self.cfg.spacing
        jogs = 0
        right_set = set(right_ids)
        left_set = set(left_ids)
        for net in self.sch.nets:
            terms = self._terminals(net)
            ls = [(c, p) for c, p in terms if self._owner(c) in left_set]
            rs = [(c, p) for c, p in terms if self._owner(c) in right_set]
            if not ls or not rs:
                continue
            if len(terms) == 2:
                (c1, p1), (c2, p2) = ls[0], rs[0]
                y1 = self._terminal_abs(c1, p1, g.pos)[1]
                y2 = self._terminal_abs(c2, p2, g.pos)[1]
                if y1 == y2:
                    continue
            jogs += 1
        return max(sp.min_h_gap, sp.track_gap * (jogs + 3))


class StressSeedMixin:
    def _ext(self, cid: str) -> float:
        """Mean extent of a part's footprint (used for ideal distances)."""
        fx1, fy1, fx2, fy2 = self._footprint(cid)
        return ((fx2 - fx1) + (fy2 - fy1)) / 2.0

    def _embed(self, g: _Group) -> None:
        self._hub = g.hub
        comps = g.comps
        if len(comps) == 1:
            g.pos[comps[0]] = (0.0, 0.0)
            return
        w = self._weights(comps)
        idx = {c: i for i, c in enumerate(comps)}
        n = len(comps)
        inf = float("inf")
        dist = [[inf] * n for _ in range(n)]
        for i in range(n):
            dist[i][i] = 0.0
        for (a, b), v in w.items():
            length = (self._ext(a) + self._ext(b)) / 2.0 + 110.0 + 170.0 * math.exp(-2.0 * v)
            i, j = idx[a], idx[b]
            dist[i][j] = min(dist[i][j], length)
        for k in range(n):
            dk = dist[k]
            for i in range(n):
                dik = dist[i][k]
                if dik == inf:
                    continue
                di = dist[i]
                for j in range(n):
                    nd = dik + dk[j]
                    if nd < di[j]:
                        di[j] = nd

        centers = self._initial_centers(g, w)
        xs = [centers[c][0] for c in comps]
        ys = [centers[c][1] for c in comps]
        constraints = self._direction_constraints(comps, centers)
        for _ in range(self.cfg.stress_iterations):
            for i in range(n):
                sw = sx = sy = 0.0
                for j in range(n):
                    if i == j or dist[i][j] == inf:
                        continue
                    d = dist[i][j]
                    wij = 1.0 / (d * d)
                    dx, dy = xs[i] - xs[j], ys[i] - ys[j]
                    norm = math.hypot(dx, dy) or 1e-3
                    sw += wij
                    sx += wij * (xs[j] + d * dx / norm)
                    sy += wij * (ys[j] + d * dy / norm)
                if sw > 0:
                    xs[i], ys[i] = sx / sw, sy / sw
            for _ in range(3):
                for a, b, axis, gap in constraints:
                    i, j = idx[a], idx[b]
                    if axis == "x":
                        viol = xs[i] + gap - xs[j]
                        if viol > 0:
                            xs[i] -= viol / 2.0
                            xs[j] += viol / 2.0
                    else:
                        viol = ys[i] + gap - ys[j]
                        if viol > 0:
                            ys[i] -= viol / 2.0
                            ys[j] += viol / 2.0
        for c in comps:
            comp = self.comps[c]
            cx, cy = xs[idx[c]], ys[idx[c]]
            g.pos[c] = (snap(cx - comp.width / 2.0), snap(cy - comp.height / 2.0))

    def _direction_constraints(self, comps: List[str], centers: Optional[Dict[str, Pt]] = None) -> List[Tuple[str, str, str, float]]:
        """(a, b, axis, gap): center_b - center_a >= gap along axis."""
        members = set(comps)
        out: List[Tuple[str, str, str, float]] = []
        seen: Set[Tuple[str, str, str]] = set()
        for net in self.sch.nets:
            info = self.sem.nets[net.name]
            if info.is_rail:
                continue
            terms = [(c, p) for c, p in self._terminals(net) if c in members]
            for c1, p1 in terms:
                pin = self.comps[c1].pins[p1]
                if not pin.side_specified:
                    continue
                for c2, _ in terms:
                    if c2 == c1:
                        continue
                    a1, a2 = self.comps[c1], self.comps[c2]
                    if pin.side in (Side.LEFT, Side.RIGHT):
                        gap = (a1.width + a2.width) / 2.0 + self.cfg.spacing.min_h_gap
                        key = (c1, c2, pin.side.value)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append((c1, c2, "x", gap) if pin.side == Side.RIGHT else (c2, c1, "x", gap))
                    else:
                        gap = (a1.height + a2.height) / 2.0 + self.cfg.spacing.min_v_gap
                        key = (c1, c2, pin.side.value)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append((c1, c2, "y", gap) if pin.side == Side.BOTTOM else (c2, c1, "y", gap))
        if self._hub and self._hub in members:
            hub = self._hub
            bus_slaves = [c for c in comps if c != hub and hub in self._bus_mates(c) and self._relation(hub, c) is None]
            if len(bus_slaves) >= 2:
                a1 = self.comps[hub]
                hx = centers[hub][0] if centers and hub in centers else 0.0
                for c in bus_slaves:
                    a2 = self.comps[c]
                    gap = (a1.width + a2.width) / 2.0 + self.cfg.spacing.min_h_gap
                    cx = centers[c][0] if centers and c in centers else 1.0
                    out.append((hub, c, "x", gap) if cx >= hx else (c, hub, "x", gap))
        return out

    def _initial_centers(self, g: _Group, w: Dict[Tuple[str, str], float]) -> Dict[str, Pt]:
        """Deterministic BFS seed: neighbours go to the side their pins face."""
        comps = g.comps
        centers: Dict[str, Pt] = {g.hub: (0.0, 0.0)}
        side_count: Dict[Tuple[str, str], int] = {}
        queue = [g.hub]
        while queue:
            parent = queue.pop(0)
            px, py = centers[parent]
            nbrs = sorted(
                (c for c in comps if c not in centers and w.get((parent, c), 0.0) > 0),
                key=lambda c: (-w[(parent, c)], comps.index(c)),
            )
            for child in nbrs:
                direction = self._relation(parent, child)
                if direction is None:
                    # Parts sharing a bus with an already placed sibling go to the
                    # same side, so the shared wires do not wrap around the parent.
                    mates = [m for m in self._bus_mates(child) if m in centers and m != parent]
                    votes = [1 if centers[m][0] > px else -1 for m in mates if centers[m][0] != px]
                    if votes:
                        direction = (1, 0) if sum(votes) >= 0 else (-1, 0)
                if direction is None:
                    left = side_count.get((parent, "L"), 0)
                    right = side_count.get((parent, "R"), 0)
                    direction = (1, 0) if right <= left else (-1, 0)
                key = (parent, "L" if direction[0] < 0 else "R" if direction[0] > 0 else "V")
                k = side_count.get(key, 0)
                side_count[key] = k + 1
                dist = (self._ext(parent) + self._ext(child)) / 2.0 + 160.0
                stack_off = (k - 0.0) * (self._ext(child) + 60.0) * (1 if k % 2 == 0 else -1) * 0.5
                if direction[1] == 0:
                    centers[child] = (px + direction[0] * dist, py + stack_off)
                else:
                    centers[child] = (px + stack_off, py + direction[1] * dist)
                queue.append(child)
        for c in comps:  # weakly connected leftovers
            if c not in centers:
                centers[c] = (0.0, 400.0 + 200.0 * comps.index(c))
        return centers


def _nearest_free(top: float, height: float, placed: List[Tuple[float, float]], gap: float) -> float:
    """Closest grid-aligned top to ``top`` whose [top, top+height] interval keeps
    ``gap`` clearance from every interval in ``placed``."""
    def free(t: float) -> bool:
        return all(t + height + gap <= a or t >= b + gap for a, b in placed)

    if free(top):
        return top
    cands = []
    for a, b in placed:
        for t in (snap_up(b + gap), snap_down(a - gap - height)):
            if free(t):
                cands.append(t)
    if not cands:
        t = max(b for _, b in placed) + gap
        return snap_up(t)
    # Prefer going below: weaker parts read top-to-bottom after stronger ones.
    return min(cands, key=lambda t: (abs(t - top) * (1.0 if t >= top else 1.5), -t))


def snap_up(v: float) -> float:
    return math.ceil(v / GRID_SIZE - 1e-9) * GRID_SIZE


def snap_down(v: float) -> float:
    return math.floor(v / GRID_SIZE + 1e-9) * GRID_SIZE
