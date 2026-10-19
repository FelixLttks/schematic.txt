"""Orthogonal grid router.

Every net becomes a rectilinear Steiner tree on the 20 px grid, found by A*
(Hart, Nilsson & Raphael 1968) over states *(node, heading)* so that bends
can be priced.  Multi-terminal nets grow one terminal at a time; the new
terminal is connected to the nearest valid node of the existing tree.

Hard rules (a drawing that violates one is ambiguous, so the search never
produces it):

* no wire inside a component body, on a foreign terminal or on the grid node
  directly in front of a foreign pin;
* a wire leaves / enters a pin straight along the pin's direction;
* two nets never share a grid edge;
* two nets may share a grid node only as a straight, perpendicular crossing
  -- never at a bend, branch or end of either;
* a net never touches itself except at a deliberate T-junction (no loops,
  no four-way joins).

Costs come from ``config.Weights`` (the same numbers price placements and
score the result): length, bends, crossings, plus small penalties for
running directly beside another net and through text.

Order dependence of sequential routing is reduced by rip-up and reroute
(each net is removed and routed again against all others, kept only if
cheaper; cf. PathFinder, McMurchie & Ebeling 1995) and by conflict-driven
recovery when a net cannot be routed at all.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from schematic.layout.config import Weights
from schematic.layout.netlist import Netlist

DX = (1, 0, -1, 0)
DY = (0, 1, 0, -1)


@dataclass
class RTerminal:
    comp: str
    pin: str
    x: int
    y: int
    d: int                  # outward direction index


@dataclass
class RNet:
    index: int
    name: str
    factor: float
    rail: bool
    terminals: List[RTerminal]
    bundle: int = -1
    edges: Set[Tuple[int, int]] = field(default_factory=set)
    routed: bool = False
    failed: int = 0
    failed_terms: List[RTerminal] = field(default_factory=list)


class Router:
    def __init__(self, width: int, height: int, ox: int, oy: int, weights: Weights):
        self.W, self.H = width, height
        self.ox, self.oy = ox, oy           # grid coordinate of node 0
        self.w = weights
        size = width * height
        self.blocked = bytearray(size)
        self.text = bytearray(size)
        self.term = [-1] * size             # net owning a terminal node (-2: unconnected pin)
        self.reserve = [-1] * size          # node in front of a pin: only its net may use it
        self.front = [-1] * size            # second node in front of a pin (soft: keep pins free)
        self.eh = [-1] * size               # edge node -> node + 1
        self.ev = [-1] * size               # edge node -> node + W
        self.nets: List[RNet] = []
        self.bundle_of: List[int] = []      # net index -> bundle (-1: none)

    # ------------------------------------------------------------- setup
    def node(self, x: int, y: int) -> int:
        return (y - self.oy) * self.W + (x - self.ox)

    def xy(self, n: int) -> Tuple[int, int]:
        return n % self.W + self.ox, n // self.W + self.oy

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x - self.ox < self.W and 0 <= y - self.oy < self.H

    def block_rect(self, x0: int, y0: int, x1: int, y1: int) -> None:
        for y in range(max(y0, self.oy), min(y1, self.oy + self.H - 1) + 1):
            for x in range(max(x0, self.ox), min(x1, self.ox + self.W - 1) + 1):
                self.blocked[self.node(x, y)] = 1

    def text_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        import math
        for y in range(math.ceil(y0), math.floor(y1) + 1):
            for x in range(math.ceil(x0), math.floor(x1) + 1):
                if self.inside(x, y):
                    self.text[self.node(x, y)] = 1

    def add_pin(self, x: int, y: int, d: int, net: int) -> None:
        n = self.node(x, y)
        self.blocked[n] = 0
        self.term[n] = net
        if net >= 0:
            ex, ey = x + DX[d], y + DY[d]
            if self.inside(ex, ey):
                e = self.node(ex, ey)
                self.reserve[e] = net if self.reserve[e] in (-1, net) else -3
            fx, fy = x + 2 * DX[d], y + 2 * DY[d]
            if self.inside(fx, fy):
                f = self.node(fx, fy)
                self.front[f] = net if self.front[f] in (-1, net) else -3

    # ---------------------------------------------------------- edges
    def _edge(self, u: int, d: int) -> int:
        """Index into eh / ev of the edge leaving ``u`` in direction ``d``
        (negative: vertical, encoded as -1 - index)."""
        if d == 0:
            return u
        if d == 2:
            return u - 1
        if d == 1:
            return -1 - u
        return -1 - (u - self.W)

    def _edge_owner(self, e: int) -> int:
        return self.eh[e] if e >= 0 else self.ev[-1 - e]

    def _set_edge(self, e: int, net: int) -> None:
        if e >= 0:
            self.eh[e] = net
        else:
            self.ev[-1 - e] = net

    def _nets_at(self, n: int) -> Dict[int, int]:
        """net -> bitmask of used edge directions at node n."""
        out: Dict[int, int] = {}
        W = self.W
        x = n % W
        if x + 1 < W and self.eh[n] >= 0:
            out[self.eh[n]] = out.get(self.eh[n], 0) | 1
        if x > 0 and self.eh[n - 1] >= 0:
            out[self.eh[n - 1]] = out.get(self.eh[n - 1], 0) | 4
        if n + W < len(self.ev) and self.ev[n] >= 0:
            out[self.ev[n]] = out.get(self.ev[n], 0) | 2
        if n - W >= 0 and self.ev[n - W] >= 0:
            out[self.ev[n - W]] = out.get(self.ev[n - W], 0) | 8
        return out

    # ------------------------------------------------------------ search
    def _search(self, net: RNet, src: RTerminal, targets: Dict[int, int], window: Optional[Tuple[int, int, int, int]],
                soft: bool = False) -> Optional[List[int]]:
        """A* from ``src`` (leaving along its pin direction) to any target
        node.  ``targets`` maps node -> bitmask of allowed arrival headings.
        With ``soft`` foreign edges and nodes are allowed at a high price
        (used to find which nets block a connection)."""
        W, H = self.W, self.H
        w = self.w
        f = net.factor
        step = w.length * f
        bend = w.bend * f
        cross = w.crossing
        near_w = w.near
        text_w = w.text
        k = net.index
        bundle_of = self.bundle_of
        my_bundle = net.bundle
        stray = w.stray if my_bundle >= 0 and any(
            m.edges for m in self.nets if m.bundle == my_bundle and m is not net) else 0.0
        blocked, term, reserve, text = self.blocked, self.term, self.reserve, self.text
        front = self.front
        front_w = w.pin_front
        eh, ev = self.eh, self.ev
        if window is None:
            wx0, wy0, wx1, wy1 = 0, 0, W - 1, H - 1
        else:
            wx0, wy0, wx1, wy1 = window
        tx = [t % W for t in targets]
        ty = [t // W for t in targets]
        tx0, tx1, ty0, ty1 = min(tx), max(tx), min(ty), max(ty)
        single = len(targets) == 1
        if single:
            (t_only,) = tuple(targets)
            t_x, t_y = t_only % W, t_only // W

        def h(n: int, d: int) -> float:
            x, y = n % W, n // W
            dx = 0 if tx0 <= x <= tx1 else (tx0 - x if x < tx0 else x - tx1)
            dy = 0 if ty0 <= y <= ty1 else (ty0 - y if y < ty0 else y - ty1)
            est = (dx + dy) * step
            if single:
                ddx, ddy = t_x - x, t_y - y
                if ddx or ddy:
                    ahead = ddx * DX[d] + ddy * DY[d]
                    side = ddx * DY[d] - ddy * DX[d]
                    if ahead <= 0:
                        est += 2 * bend
                    elif side != 0:
                        est += bend
            return est

        s = self.node(src.x, src.y)
        start_d = src.d
        INF = float("inf")
        best: Dict[int, float] = {}
        parent: Dict[int, int] = {}
        key0 = s * 4 + start_d
        best[key0] = 0.0
        heap = [(h(s, start_d), 0.0, key0)]
        soft_pen = cross * 6
        while heap:
            fcost, g, key = heapq.heappop(heap)
            if g > best.get(key, INF):
                continue
            u, d = divmod(key, 4)
            if u in targets and u != s and (targets[u] >> d) & 1:
                path = [u]
                kk = key
                while kk != key0:
                    kk = parent[kk]
                    path.append(kk // 4)
                path.reverse()
                return path
            ux, uy = u % W, u // W
            here = None
            if u != s:
                # At a node shared with another net only a straight crossing is allowed.
                occupied = False
                if ux + 1 < W and eh[u] not in (-1, k):
                    occupied = True
                elif ux > 0 and eh[u - 1] not in (-1, k):
                    occupied = True
                elif uy + 1 < H and ev[u] not in (-1, k):
                    occupied = True
                elif uy > 0 and ev[u - W] not in (-1, k):
                    occupied = True
                here = occupied
            for nd in ((d,) if (u == s or here) else (d, (d + 1) & 3, (d + 3) & 3)):
                nx, ny = ux + DX[nd], uy + DY[nd]
                if nx < wx0 or nx > wx1 or ny < wy0 or ny > wy1:
                    continue
                v = u + DX[nd] + DY[nd] * W
                # edge
                if nd == 0:
                    eo = eh[u]
                elif nd == 2:
                    eo = eh[v]
                elif nd == 1:
                    eo = ev[u]
                else:
                    eo = ev[v]
                c = step
                if eo != -1:
                    if eo == k or not soft:
                        continue
                    c += soft_pen
                if blocked[v]:
                    continue
                tv = term[v]
                if tv != -1 and tv != k:
                    continue
                if tv == k and v not in targets:
                    continue
                rv = reserve[v]
                if rv != -1 and rv != k:
                    if not (soft and rv >= 0):
                        continue
                    c += soft_pen
                if nd != d:
                    c += bend
                fv = front[v]
                if fv != -1 and fv != k:
                    c += front_w
                if text[v]:
                    c += text_w
                # foreign occupancy of v
                horiz = nd in (0, 2)
                o_h = (eh[v] if nx + 1 < W else -1, eh[v - 1] if nx > 0 else -1)
                o_v = (ev[v] if ny + 1 < H else -1, ev[v - W] if ny > 0 else -1)
                own_v = False
                foreign = False
                for o in o_h + o_v:
                    if o == k:
                        own_v = True
                    elif o != -1:
                        foreign = True
                if own_v and v not in targets:
                    continue
                if foreign:
                    if v in targets and not soft:
                        continue
                    # straight perpendicular pass required
                    if horiz:
                        ok = o_h[0] == -1 and o_h[1] == -1 and o_v[0] == o_v[1] and o_v[0] not in (-1, k)
                    else:
                        ok = o_v[0] == -1 and o_v[1] == -1 and o_h[0] == o_h[1] and o_h[0] not in (-1, k)
                    if not ok:
                        if not soft:
                            continue
                        c += soft_pen
                    else:
                        c += cross
                # running directly beside another net
                if horiz:
                    e1 = eh[u - W] if uy > 0 and nd == 0 else (eh[v - W] if uy > 0 else -1)
                    e2 = eh[u + W] if uy + 1 < H and nd == 0 else (eh[v + W] if uy + 1 < H else -1)
                else:
                    e1 = ev[u - 1] if ux > 0 and nd == 1 else (ev[v - 1] if ux > 0 else -1)
                    e2 = ev[u + 1] if ux + 1 < W and nd == 1 else (ev[v + 1] if ux + 1 < W else -1)
                beside_mate = False
                for e in (e1, e2):
                    if e != -1 and e != k:
                        if my_bundle >= 0 and bundle_of[e] == my_bundle:
                            beside_mate = True
                        else:
                            c += near_w
                if stray and not beside_mate:
                    c += stray
                ng = g + c
                nkey = v * 4 + nd
                if ng < best.get(nkey, INF):
                    best[nkey] = ng
                    parent[nkey] = key
                    heapq.heappush(heap, (ng + h(v, nd), ng, nkey))
        return None

    # ------------------------------------------------------- tree targets
    def _targets(self, net: RNet, connected: List[RTerminal]) -> Dict[int, int]:
        """Valid arrival nodes of the current tree with allowed headings."""
        out: Dict[int, int] = {}
        if not net.edges:
            t = connected[0]
            out[self.node(t.x, t.y)] = 1 << ((t.d + 2) & 3)
            return out
        term_nodes = {self.node(t.x, t.y) for t in net.terminals}
        deg: Dict[int, int] = {}
        for a, b in net.edges:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        for n, dg in deg.items():
            if n in term_nodes or dg >= 3:
                continue
            used = self._nets_at(n)
            if any(o != net.index for o in used):
                continue
            mask = used.get(net.index, 0)
            allowed = 0
            for d in range(4):
                # arriving with heading d enters through the edge on the opposite side
                back = (d + 2) & 3
                bit = (1, 2, 4, 8)[back]
                if not mask & bit:
                    allowed |= 1 << d
            if allowed:
                out[n] = allowed
        return out

    def _commit(self, net: RNet, path: List[int]) -> None:
        for a, b in zip(path, path[1:]):
            d = self._dir(a, b)
            self._set_edge(self._edge(a, d), net.index)
            net.edges.add((a, b) if a < b else (b, a))

    def _dir(self, a: int, b: int) -> int:
        diff = b - a
        if diff == 1:
            return 0
        if diff == -1:
            return 2
        return 1 if diff > 0 else 3

    def rip_up(self, net: RNet) -> None:
        for a, b in net.edges:
            self._set_edge(self._edge(a, self._dir(a, b)), -1)
        net.edges = set()
        net.routed = False

    def _window(self, pts: Sequence[Tuple[int, int]], margin: int) -> Tuple[int, int, int, int]:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (max(0, min(xs) - margin), max(0, min(ys) - margin),
                min(self.W - 1, max(xs) + margin), min(self.H - 1, max(ys) + margin))

    def route_net(self, net: RNet, recover: int = 1) -> bool:
        """Routes one net (Prim-style growth); returns False if any terminal
        could not be connected."""
        terms = list(net.terminals)
        if len(terms) < 2:
            net.routed = True
            return True
        # Start with the terminal closest to the centre of the net.
        cx = sum(t.x for t in terms) / len(terms)
        cy = sum(t.y for t in terms) / len(terms)
        terms.sort(key=lambda t: (abs(t.x - cx) + abs(t.y - cy), t.x, t.y))
        connected = [terms.pop(0)]
        net.failed_terms = []
        ok = True
        while terms:
            tree_pts = [self.xy(a) for e in net.edges for a in e] or [(c.x, c.y) for c in connected]
            terms.sort(key=lambda t: (min(abs(t.x - p[0]) + abs(t.y - p[1]) for p in tree_pts), t.x, t.y))
            t = terms.pop(0)
            targets = self._targets(net, connected)
            path = None
            if targets:
                pts = [(t.x - self.ox, t.y - self.oy)] + [(n % self.W, n // self.W) for n in targets]
                path = self._search(net, t, targets, self._window(pts, 8))
                if path is None:
                    path = self._search(net, t, targets, None)
                if path is None and recover > 0:
                    path = self._recover(net, t, targets, recover)
            if path is None:
                ok = False
                net.failed += 1
                net.failed_terms.append(t)
                continue
            self._commit(net, path)
            connected.append(t)
        net.routed = ok
        return ok

    def _recover(self, net: RNet, t: RTerminal, targets: Dict[int, int], depth: int) -> Optional[List[int]]:
        """Finds the cheapest path that ignores other nets, rips up exactly
        the nets in its way, commits it and re-routes the victims."""
        path = self._search(net, t, targets, None, soft=True)
        if path is None:
            return None
        victims: Dict[int, None] = {}
        for i, n in enumerate(path):
            for o in self._nets_at(n):
                if o != net.index:
                    victims[o] = None
            r = self.reserve[n]
            if r >= 0 and r != net.index:
                victims[r] = None
        saved = {v: set(self.nets[v].edges) for v in victims}
        for v in victims:
            self.rip_up(self.nets[v])
        path2 = self._search(net, t, targets, None)
        if path2 is None:
            for v, edges in saved.items():
                self._restore(self.nets[v], edges)
            return None
        self._commit(net, path2)
        for v in victims:
            self.route_net(self.nets[v], depth - 1)
        # The caller commits again; undo our commit to keep it single.
        for a, b in zip(path2, path2[1:]):
            self._set_edge(self._edge(a, self._dir(a, b)), -1)
            net.edges.discard((a, b) if a < b else (b, a))
        return path2

    def _restore(self, net: RNet, edges: Set[Tuple[int, int]]) -> None:
        for a, b in edges:
            self._set_edge(self._edge(a, self._dir(a, b)), net.index)
        net.edges = set(edges)
        net.routed = True

    # ------------------------------------------------------------- costs
    def net_cost(self, net: RNet) -> float:
        """Cost of a routed net with the shared weights (length, bends,
        junctions, crossings it takes part in, unconnected terminals)."""
        w = self.w
        deg: Dict[int, int] = {}
        axes: Dict[int, int] = {}
        for a, b in net.edges:
            horiz = abs(b - a) == 1
            for n in (a, b):
                deg[n] = deg.get(n, 0) + 1
                axes[n] = axes.get(n, 0) | (1 if horiz else 2)
        bends = sum(1 for n, dg in deg.items() if dg == 2 and axes[n] == 3)
        junctions = sum(1 for dg in deg.values() if dg >= 3)
        crossings = 0
        for n in deg:
            if any(o != net.index for o in self._nets_at(n)):
                crossings += 1
        cost = (w.length * len(net.edges) + w.bend * bends) * net.factor
        cost += w.junction * junctions + w.crossing * crossings
        near, apart = self._company(net)
        cost += w.near * near + w.stray * apart
        if not net.routed:
            cost += 1000.0
        return cost

    def _company(self, net: RNet) -> Tuple[int, int]:
        """(steps beside an unrelated wire, steps of a bundle member away
        from all routed mates)."""
        W, H = self.W, self.H
        k = net.index
        mates = net.bundle >= 0 and any(m.edges for m in self.nets if m.bundle == net.bundle and m is not net)
        near = apart = 0
        for a, b in net.edges:
            x, y = a % W, a // W
            if b - a == 1:
                sides = (self.eh[a - W] if y > 0 else -1, self.eh[a + W] if y + 1 < H else -1)
            else:
                sides = (self.ev[a - 1] if x > 0 else -1, self.ev[a + 1] if x + 1 < W else -1)
            beside_mate = False
            for e in sides:
                if e != -1 and e != k:
                    if net.bundle >= 0 and self.bundle_of[e] == net.bundle:
                        beside_mate = True
                    else:
                        near += 1
            if mates and not beside_mate:
                apart += 1
        return near, apart

    def route_all(self, passes: int) -> None:
        self.bundle_of = [-1] * (max((n.index for n in self.nets), default=-1) + 1)
        for n in self.nets:
            self.bundle_of[n.index] = n.bundle
        own = {n.index: self._order_key(n) for n in self.nets}
        lead: Dict[int, tuple] = {}
        for n in self.nets:
            if n.bundle >= 0:
                lead[n.bundle] = min(lead.get(n.bundle, own[n.index]), own[n.index])
        # members of a bundle are routed one after another (the first lays
        # the path, the others follow it as a ribbon)
        order = sorted(self.nets, key=lambda n: (lead.get(n.bundle, own[n.index]), own[n.index]))
        for net in order:
            self.route_net(net)
        for _ in range(passes):
            changed = False
            for net in order:
                old_edges = set(net.edges)
                old_routed = net.routed
                old_cost = self.net_cost(net)
                self.rip_up(net)
                self.route_net(net, recover=0)
                new_cost = self.net_cost(net)
                if new_cost < old_cost - 1e-6:
                    changed = True
                else:
                    self.rip_up(net)
                    self._restore(net, old_edges)
                    net.routed = old_routed
            if not changed:
                break
        # Repair: pins still open get the conflict-driven recovery (rip up
        # exactly the nets in the way, route, re-route the victims) until
        # nothing improves.
        for depth in (1, 2, 2):
            open_nets = [n for n in order if not n.routed]
            if not open_nets:
                break
            for net in open_nets:
                self.rip_up(net)
                self.route_net(net, recover=depth)
            for net in order:
                if not net.routed:
                    self.rip_up(net)
                    self.route_net(net, recover=depth)

    def _order_key(self, net: RNet):
        xs = [t.x for t in net.terminals]
        ys = [t.y for t in net.terminals]
        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
        straight = len(net.terminals) == 2 and (xs[0] == xs[1] or ys[0] == ys[1])
        return (net.rail, len(net.terminals) > 2, not straight, hpwl, net.index)
