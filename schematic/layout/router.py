"""Readability-driven orthogonal grid router.

Every net is routed on the 20 px grid with A* over states
``(node, heading, run-length bucket)``.  The cost model (``RoutingWeights``)
prices wire length, bends, short zig-zags, crossings, running beside unrelated
wires, hugging component outlines and passing in front of foreign pins, and
it rewards bundle members for following an already routed mate, which is what
turns related nets into parallel ribbons.

Hard rules are enforced by construction and can never be traded for cost:

* two nets never share a wire segment (no collinear overlap);
* a net may cross another net only by passing straight through a node where
  the other net also runs straight -- never bend, branch or end on a foreign
  wire (so there are no ambiguous T-touches or corner contacts);
* wires never enter component bodies, never touch foreign pin terminals and
  never use the escape node directly in front of another net's pin;
* every wire leaves and enters a pin perpendicular to the component edge.

Multi-pin nets grow a rectilinear Steiner tree: each further pin is joined to
the tree by a multi-source search from all valid tree nodes.  After the first
pass, rip-up-and-reroute passes re-route each net against all others and keep
the new route only when its cost drops, which removes most of the order
dependence of sequential routing (cf. PathFinder-style negotiation).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from schematic.models import GRID_SIZE, Point, Schematic, Side, WireJunction, WireSegment
from schematic.layout.config import DEFAULT_CONFIG, LayoutConfig
from schematic.layout.pin_allocator import resolve_pin
from schematic.layout.semantics import NetClass, Semantics, analyze
from schematic.styles import net_color

RIGHT, LEFT, DOWN, UP, NONE = 0, 1, 2, 3, 4
_OPP = {RIGHT: LEFT, LEFT: RIGHT, DOWN: UP, UP: DOWN}
_SIDE_DIR = {Side.RIGHT: RIGHT, Side.LEFT: LEFT, Side.BOTTOM: DOWN, Side.TOP: UP}
_HORIZONTAL = (True, True, False, False)
_DX = (1, -1, 0, 0)
_DY = (0, 0, 1, -1)

FREE = -1
UNCONNECTED_PIN = -2
SHARED = -3


@dataclass
class _Terminal:
    node: int
    out_dir: int          # direction pointing away from the component


@dataclass
class _NetState:
    index: int
    name: str
    terminals: List[_Terminal]
    edges: Set[Tuple[int, int]] = field(default_factory=set)   # (node, dir) with dir in RIGHT/DOWN
    adj: Dict[int, Set[int]] = field(default_factory=dict)     # node -> set of dirs
    cost: float = 0.0
    failed: int = 0
    related: Set[int] = field(default_factory=set)
    mates: Set[int] = field(default_factory=set)               # bundle mates (strong relation)
    is_rail: bool = False
    is_power: bool = False


class GridRouter:
    def __init__(self, schematic: Schematic, semantics: Optional[Semantics] = None,
                 config: Optional[LayoutConfig] = None):
        self.sch = schematic
        self.sem = semantics or analyze(schematic)
        self.cfg = config or DEFAULT_CONFIG
        self.w = self.cfg.routing
        self.nets: List[_NetState] = []
        self.failures: List[str] = []
        self._active: Set[int] = set()      # nets whose tree is being built right now

    # ================================================================= grid
    def _build_grid(self, pad: int = 8) -> None:
        comps = list(self.sch.components.values())
        xs: List[float] = []
        ys: List[float] = []
        for c in comps:
            xs += [c.x, c.x + c.width]
            ys += [c.y, c.y + c.height]
            for p in c.pins.values():
                xs.append(p.position.x)
                ys.append(p.position.y)
        g = GRID_SIZE
        self.x0 = int(round(min(xs) / g)) - pad
        self.y0 = int(round(min(ys) / g)) - pad
        x1 = int(round(max(xs) / g)) + pad
        y1 = int(round(max(ys) / g)) + pad
        self.W = x1 - self.x0 + 1
        self.H = y1 - self.y0 + 1
        size = self.W * self.H
        self.blocked = bytearray(size)
        self.term = [FREE] * size
        self.escape = [FREE] * size
        self.corridor = [FREE] * size
        self.occ_h = [FREE] * size
        self.occ_v = [FREE] * size
        self.occ_x = [FREE] * size
        self.edge_r = [FREE] * size
        self.edge_d = [FREE] * size
        self.near_body = bytearray(size)
        self.escape_shared: Dict[int, Set[int]] = {}
        self._term_comp: Dict[int, str] = {}

        W = self.W
        self.gx = [i % W + self.x0 for i in range(size)]
        self.gy = [i // W + self.y0 for i in range(size)]
        self.label_zone = bytearray(size)
        for c in comps:
            if not c.is_standard:
                continue
            # Upright name / value text: right of vertical symbols, above and
            # below horizontal ones.  Wires may cross it, but pay for it.
            vertical = any(p.side in (Side.TOP, Side.BOTTOM) for p in c.pins.values())
            if vertical:
                text_w = max(len(c.id) * 6.5, len(c.value) * 5.6) + 14.0
                zx1, zy1 = self._g(c.x + c.width), self._g(c.y)
                zx2, zy2 = self._g(c.x + c.width + text_w), self._g(c.y + c.height)
            else:
                zx1, zy1 = self._g(c.x), self._g(c.y - 20.0)
                zx2, zy2 = self._g(c.x + c.width), self._g(c.y + c.height + 20.0)
            for gy in range(zy1, zy2 + 1):
                for gx in range(zx1, zx2 + 1):
                    if 0 <= gx - self.x0 < W and 0 <= gy - self.y0 < self.H:
                        self.label_zone[self._n(gx, gy)] = 1
        for c in comps:
            gx1, gy1 = self._g(c.x), self._g(c.y)
            gx2, gy2 = self._g(c.x + c.width), self._g(c.y + c.height)
            for gy in range(gy1, gy2 + 1):
                for gx in range(gx1, gx2 + 1):
                    self.blocked[self._n(gx, gy)] = 1
            for gy in range(gy1 - 1, gy2 + 2):
                for gx in range(gx1 - 1, gx2 + 2):
                    if 0 <= gx - self.x0 < W and 0 <= gy - self.y0 < self.H:
                        self.near_body[self._n(gx, gy)] = 1

    def _g(self, v: float) -> int:
        return int(round(v / GRID_SIZE))

    def _n(self, gx: int, gy: int) -> int:
        return (gy - self.y0) * self.W + (gx - self.x0)

    def _xy(self, n: int) -> Tuple[int, int]:
        return n % self.W + self.x0, n // self.W + self.y0

    def _step(self, n: int, d: int) -> int:
        W = self.W
        if d == RIGHT:
            return n + 1 if (n % W) < W - 1 else -1
        if d == LEFT:
            return n - 1 if (n % W) > 0 else -1
        if d == DOWN:
            return n + W if n + W < W * self.H else -1
        return n - W if n - W >= 0 else -1

    # ============================================================ net setup
    def _setup_nets(self) -> None:
        index_of: Dict[str, int] = {}
        for net in self.sch.nets:
            terms: List[_Terminal] = []
            seen: Set[int] = set()
            for conn in net.connections:
                comp = self.sch.components.get(conn.component_id)
                if comp is None:
                    continue
                pin = resolve_pin(comp, conn.pin_id, conn.pin_name)
                if pin is None:
                    continue
                node = self._n(self._g(pin.position.x), self._g(pin.position.y))
                self._term_comp[node] = comp.id
                if node in seen:
                    continue
                seen.add(node)
                terms.append(_Terminal(node=node, out_dir=_SIDE_DIR[pin.side]))
            info = self.sem.nets.get(net.name)
            st = _NetState(index=len(self.nets), name=net.name, terminals=terms)
            if info is not None:
                st.is_rail = info.is_rail
                st.is_power = info.is_power_like
            index_of[net.name] = st.index
            self.nets.append(st)
        for st in self.nets:
            for other in self.nets:
                if other is st:
                    continue
                if self.sem.related(st.name, other.name):
                    st.related.add(other.index)
                    a, b = self.sem.nets.get(st.name), self.sem.nets.get(other.name)
                    if a and b and ((a.bundle and a.bundle == b.bundle) or a.diff_partner == other.name):
                        st.mates.add(other.index)

        # Terminals, escape nodes and pin corridors.
        for c in self.sch.components.values():
            for p in c.pins.values():
                node = self._n(self._g(p.position.x), self._g(p.position.y))
                self.term[node] = UNCONNECTED_PIN
        for st in self.nets:
            if len(st.terminals) < 2:
                continue
            for t in st.terminals:
                self.term[t.node] = st.index
        self.escape_dir = [NONE] * (self.W * self.H)
        for st in self.nets:
            if len(st.terminals) < 2:
                continue
            for t in st.terminals:
                n = t.node
                for k in range(1, 4):
                    n = self._step(n, t.out_dir)
                    if n < 0:
                        break
                    if k == 1:
                        cur = self.escape[n]
                        n2 = self._step(n, t.out_dir)
                        clear_ahead = (
                            n2 >= 0
                            and not self.blocked[n2]
                            and self.term[n2] in (FREE, st.index)
                        )
                        if cur in (FREE, st.index):
                            self.escape[n] = st.index
                            self.escape_dir[n] = t.out_dir if clear_ahead else SHARED
                        else:
                            self.escape[n] = SHARED
                            self.escape_dir[n] = SHARED
                            owners = self.escape_shared.setdefault(n, set())
                            owners.add(st.index)
                            if cur >= 0:
                                owners.add(cur)
                    else:
                        cur = self.corridor[n]
                        self.corridor[n] = st.index if cur in (FREE, st.index) else SHARED

    # ============================================================== routing
    def route(self) -> None:
        """Full routing: first pass, rip-up-and-reroute passes, output."""
        self.route_initial()
        self.improve(self.cfg.reroute_passes)
        self.emit()

    def route_initial(self) -> None:
        """Sequential first pass over all nets (with conflict recovery)."""
        self._order: List[_NetState] = []
        if not self.sch.components:
            return
        self._build_grid()
        self._setup_nets()
        self._order = self._net_order()
        for st in self._order:
            self._route_net(st)

    def improve(self, passes: int) -> None:
        """Rip-up-and-reroute passes; each stops early when nothing improves."""
        for _ in range(passes):
            if not self._order or not self._reroute_pass(self._order):
                break

    def emit(self) -> None:
        if not self.sch.components:
            self.sch.wires, self.sch.junctions = [], []
            return
        self._emit()

    def _net_order(self) -> List[_NetState]:
        routable = [st for st in self.nets if len(st.terminals) >= 2]

        def hpwl(st: _NetState) -> int:
            xs = [self._xy(t.node)[0] for t in st.terminals]
            ys = [self._xy(t.node)[1] for t in st.terminals]
            return (max(xs) - min(xs)) + (max(ys) - min(ys))

        def straight(st: _NetState) -> bool:
            if len(st.terminals) != 2:
                return False
            a, b = st.terminals
            (ax, ay), (bx, by) = self._xy(a.node), self._xy(b.node)
            if a.out_dir == RIGHT and b.out_dir == LEFT:
                return ay == by and bx > ax
            if a.out_dir == LEFT and b.out_dir == RIGHT:
                return ay == by and bx < ax
            if a.out_dir == DOWN and b.out_dir == UP:
                return ax == bx and by > ay
            if a.out_dir == UP and b.out_dir == DOWN:
                return ax == bx and by < ay
            return False

        bundle_rank: Dict[str, int] = {}
        for st in routable:
            info = self.sem.nets.get(st.name)
            if info and info.bundle and info.bundle not in bundle_rank:
                bundle_rank[info.bundle] = len(bundle_rank)

        def key(st: _NetState):
            info = self.sem.nets.get(st.name)
            if straight(st):
                tier = 0
            elif st.is_rail:
                tier = 4 if info and info.cls == NetClass.GROUND else 3
            elif st.is_power:
                tier = 2
            else:
                tier = 1
            first = min(self._xy(t.node)[1] for t in st.terminals)
            group = bundle_rank.get(info.bundle, 10_000) if info and info.bundle else 10_000
            return (tier, group if tier == 1 else 0, hpwl(st) if group == 10_000 else first, st.index)

        return sorted(routable, key=key)

    def _route_net(self, st: _NetState, depth: int = 0) -> None:
        self._active.add(st.index)
        try:
            self._grow_tree(st, depth)
        finally:
            self._active.discard(st.index)

    def _grow_tree(self, st: _NetState, depth: int) -> None:
        st.edges.clear()
        st.adj.clear()
        st.cost = 0.0
        st.failed = 0
        terms = st.terminals
        if len(terms) < 2:
            return

        def term_rank(t: _Terminal) -> Tuple[int, int, int]:
            cid = self._term_comp.get(t.node)
            if not cid or cid not in self.sch.components:
                return (2, 2, 0)
            comp = self.sch.components[cid]
            is_sat = cid in self.sem.component_roles and self.sem.component_roles[cid] in ("shunt", "decoupling")
            is_hub = cid in self.sem.hubs
            return (1 if is_sat else 0, 0 if is_hub else 1, -len(comp.pins))

        terms.sort(key=term_rank)
        st.adj[terms[0].node] = set()
        pending = list(terms[1:])
        while pending:
            tree = [self._xy(n) for n in st.adj]

            def dist(t: _Terminal) -> Tuple[int, int]:
                tx, ty = self._xy(t.node)
                return (min(abs(tx - x) + abs(ty - y) for x, y in tree), t.node)

            pending.sort(key=dist)
            target = pending.pop(0)
            first = terms[0] if not st.edges else None
            path, cost = self._search(st, target, first)
            if path is None:
                path, cost = self._search(st, target, first, relaxed=True)
            if path is None and depth < 2:
                path, cost = self._recover(st, target, first, depth)
            if path is None:
                st.failed += 1
                self.failures.append(st.name)
                continue
            st.cost += cost
            self._commit_path(st, path)
        if not st.failed and len(terms) >= 3:
            self._straighten_bridges(st)

    def _straighten_bridges(self, st: _NetState) -> None:
        """Slides bridge segments along rails (or unfolds staircases) when doing
        so merges an L-corner into a collinear straight-through trunk."""
        k = st.index
        free_k = (FREE, k)
        for _ in range(6):
            moved = False
            for d_seg in (RIGHT, DOWN):
                opp_seg = _OPP[d_seg]
                perp_dirs = (DOWN, UP) if d_seg == RIGHT else (RIGHT, LEFT)
                straight_seg = {d_seg, opp_seg}
                base_candidates = [
                    n for n, dirs in st.adj.items()
                    if d_seg in dirs and opp_seg not in dirs and self.term[n] != k
                ]
                for d_perp in perp_dirs:
                    dx, dy = _DX[d_perp], _DY[d_perp]
                    candidates = sorted(
                        base_candidates,
                        key=lambda n: -(self._xy(n)[0] * dx + self._xy(n)[1] * dy),
                    )
                    for u1 in candidates:
                        if u1 not in st.adj or d_seg not in st.adj[u1] or opp_seg in st.adj[u1]:
                            continue
                        chain = [u1]
                        curr = u1
                        ok = True
                        while True:
                            nxt = self._step(curr, d_seg)
                            if nxt < 0 or nxt not in st.adj:
                                ok = False
                                break
                            chain.append(nxt)
                            ndirs = st.adj[nxt]
                            if ndirs == straight_seg and self.term[nxt] == FREE:
                                curr = nxt
                                continue
                            if d_seg in ndirs or self.term[nxt] == k:
                                ok = False
                            break
                        if not ok or len(chain) < 2:
                            continue
                        u2 = chain[-1]
                        d1_orig, d2_orig = set(st.adj[u1]), set(st.adj[u2])
                        has_rail_a = d_perp in d1_orig
                        has_rail_b = d_perp in d2_orig
                        ext_a = (not has_rail_a) and d1_orig == {d_seg, _OPP[d_perp]}
                        ext_b = (not has_rail_b) and d2_orig == {opp_seg, _OPP[d_perp]}
                        if not (has_rail_a or has_rail_b) or not (has_rail_a or ext_a) or not (has_rail_b or ext_b):
                            continue
                        straight_perp = {d_perp, _OPP[d_perp]}
                        a, b = u1, u2
                        rail_a, rail_b = [u1], [u2]
                        for _step in range(1, 8):
                            na, nb = self._step(a, d_perp), self._step(b, d_perp)
                            if na < 0 or nb < 0:
                                break
                            ka = (a, d_perp) if d_perp in (RIGHT, DOWN) else (na, _OPP[d_perp])
                            kb = (b, d_perp) if d_perp in (RIGHT, DOWN) else (nb, _OPP[d_perp])
                            if has_rail_a:
                                if ka not in st.edges:
                                    break
                                if a != u1 and (st.adj.get(a) != straight_perp or self.term[a] != FREE):
                                    break
                            else:
                                eo_a = self.edge_r[ka[0]] if _HORIZONTAL[d_perp] else self.edge_d[ka[0]]
                                same_a = self.occ_h[na] if _HORIZONTAL[d_perp] else self.occ_v[na]
                                if (eo_a != FREE or self.blocked[na] or na in st.adj or self.term[na] != FREE
                                        or self.occ_x[na] not in free_k or same_a not in free_k
                                        or self.escape[na] not in free_k):
                                    break
                            if has_rail_b:
                                if kb not in st.edges:
                                    break
                                if b != u2 and (st.adj.get(b) != straight_perp or self.term[b] != FREE):
                                    break
                            else:
                                eo_b = self.edge_r[kb[0]] if _HORIZONTAL[d_perp] else self.edge_d[kb[0]]
                                same_b = self.occ_h[nb] if _HORIZONTAL[d_perp] else self.occ_v[nb]
                                if (eo_b != FREE or self.blocked[nb] or nb in st.adj or self.term[nb] != FREE
                                        or self.occ_x[nb] not in free_k or same_b not in free_k
                                        or self.escape[nb] not in free_k):
                                    break
                            a, b = na, nb
                            rail_a.append(a)
                            rail_b.append(b)
                            da, db = st.adj.get(a, set()), st.adj.get(b, set())
                            if opp_seg not in da and d_seg not in db:
                                continue
                            if self.term[a] != FREE or self.term[b] != FREE:
                                continue
                            if (self.occ_h[a] not in free_k or self.occ_v[a] not in free_k
                                    or self.occ_x[a] not in free_k or self.escape[a] not in free_k):
                                continue
                            if (self.occ_h[b] not in free_k or self.occ_v[b] not in free_k
                                    or self.occ_x[b] not in free_k or self.escape[b] not in free_k):
                                continue
                            # Build and validate the shifted bridge a -> b.
                            new_chain = [a]
                            cur_p = a
                            valid = True
                            for _ in range(len(chain) - 1):
                                nxt_p = self._step(cur_p, d_seg)
                                if nxt_p < 0:
                                    valid = False
                                    break
                                eo = self.edge_r[cur_p] if d_seg == RIGHT else self.edge_d[cur_p]
                                if eo != FREE:
                                    valid = False
                                    break
                                if nxt_p != b:
                                    if self.blocked[nxt_p] or nxt_p in st.adj or self.term[nxt_p] != FREE:
                                        valid = False
                                        break
                                    if self.occ_x[nxt_p] not in free_k:
                                        valid = False
                                        break
                                    same = self.occ_h[nxt_p] if d_seg == RIGHT else self.occ_v[nxt_p]
                                    if same not in free_k:
                                        valid = False
                                        break
                                    ev = self.escape[nxt_p]
                                    if ev not in free_k:
                                        ed = self.escape_dir[nxt_p]
                                        if not (ed >= 0 and _HORIZONTAL[d_seg] != _HORIZONTAL[ed]):
                                            valid = False
                                            break
                                new_chain.append(nxt_p)
                                cur_p = nxt_p
                            if not valid or cur_p != b:
                                continue
                            old_cost = self._evaluate(st)
                            snapshot = (set(st.edges), {n: set(d) for n, d in st.adj.items()}, st.cost, st.failed)
                            new_edges = set(st.edges)
                            for x, y in zip(chain, chain[1:]):
                                new_edges.discard((x, d_seg))
                            if has_rail_a and len(d1_orig) == 2:
                                for x, y in zip(rail_a, rail_a[1:]):
                                    ek = (x, d_perp) if d_perp in (RIGHT, DOWN) else (y, _OPP[d_perp])
                                    new_edges.discard(ek)
                            elif ext_a:
                                for x, y in zip(rail_a, rail_a[1:]):
                                    ek = (x, d_perp) if d_perp in (RIGHT, DOWN) else (y, _OPP[d_perp])
                                    new_edges.add(ek)
                            if has_rail_b and len(d2_orig) == 2:
                                for x, y in zip(rail_b, rail_b[1:]):
                                    ek = (x, d_perp) if d_perp in (RIGHT, DOWN) else (y, _OPP[d_perp])
                                    new_edges.discard(ek)
                            elif ext_b:
                                for x, y in zip(rail_b, rail_b[1:]):
                                    ek = (x, d_perp) if d_perp in (RIGHT, DOWN) else (y, _OPP[d_perp])
                                    new_edges.add(ek)
                            for x, y in zip(new_chain, new_chain[1:]):
                                new_edges.add((x, d_seg))
                            new_adj: Dict[int, Set[int]] = {}
                            for (en, ed) in new_edges:
                                em = self._step(en, ed)
                                new_adj.setdefault(en, set()).add(ed)
                                new_adj.setdefault(em, set()).add(_OPP[ed])
                            self._restore(st, new_edges, new_adj, st.cost, st.failed)
                            new_cost = self._evaluate(st)
                            if new_cost < old_cost - 1e-6:
                                st.cost = new_cost
                                moved = True
                                break
                            self._restore(st, *snapshot)
                        if moved:
                            break
                    if moved:
                        break
                if moved:
                    break
            if not moved:
                break

    def _recover(self, st: _NetState, target: _Terminal, first: Optional[_Terminal],
                 depth: int) -> Tuple[Optional[List[int]], float]:
        """Rips up the nets blocking the cheapest route, then re-routes them."""
        path, _ = self._search(st, target, first, relaxed=True, soft=True)
        if path is None:
            return None, 0.0
        # Nets that are themselves being built further up the call stack must
        # not be ripped up underneath their own routing loop.
        conflicts = self._conflicts(st, path)
        if conflicts & self._active:
            return None, 0.0
        victims = sorted(conflicts)
        if not victims:
            return None, 0.0
        for v in victims:
            self._rip_up(self.nets[v])
        path, cost = self._search(st, target, first, relaxed=True)
        if path is None:  # should not happen; restore the victims as they were
            for v in victims:
                self._route_net(self.nets[v], depth + 1)
            return None, 0.0
        self._commit_path(st, path)
        for v in victims:
            self._route_net(self.nets[v], depth + 1)
        # The committed path is part of the tree now; report an empty extension.
        return [path[-1]], cost

    def _conflicts(self, st: _NetState, path: List[int]) -> Set[int]:
        k = st.index
        out: Set[int] = set()
        for a, b in zip(path, path[1:]):
            d = self._dir(a, b)
            owner = self.edge_r[a] if d == RIGHT else self.edge_r[b] if d == LEFT else \
                self.edge_d[a] if d == DOWN else self.edge_d[b]
            if owner >= 0 and owner != k:
                out.add(owner)
        for i in range(1, len(path) - 1):
            n = path[i]
            d_in, d_out = self._dir(path[i - 1], n), self._dir(n, path[i + 1])
            occupants = {self.occ_h[n], self.occ_v[n], self.occ_x[n]} - {FREE, k}
            if d_in != d_out:
                out |= occupants
            else:
                if self.occ_x[n] not in (FREE, k):
                    out.add(self.occ_x[n])
                same = self.occ_h[n] if _HORIZONTAL[d_out] else self.occ_v[n]
                if same not in (FREE, k):
                    out.add(same)
        return {o for o in out if o >= 0}

    # ----------------------------------------------------------------- A*
    def _search(self, st: _NetState, target: _Terminal, first_source: Optional[_Terminal],
                relaxed: bool = False, soft: bool = False) -> Tuple[Optional[List[int]], float]:
        """A* from the net's tree (or ``first_source``) to ``target``.

        The search is first confined to the bounding box of tree and target
        plus a generous margin; only if that fails is the whole grid used."""
        path, cost = self._search_in(st, target, first_source, relaxed, soft, margin=14)
        if path is None:
            path, cost = self._search_in(st, target, first_source, relaxed, soft, margin=None)
        return path, cost

    def _search_in(self, st: _NetState, target: _Terminal, first_source: Optional[_Terminal],
                   relaxed: bool, soft: bool, margin: Optional[int]) -> Tuple[Optional[List[int]], float]:
        """A* core.  ``relaxed`` allows the escape node of a foreign pin at a
        high price; ``soft`` prices conflicts with other nets instead of
        forbidding them (only used to find which nets block a pin)."""
        w = self.w
        k = st.index
        W = self.W
        size = W * self.H
        tx, ty = self._xy(target.node)
        arrive_dir = _OPP[target.out_dir]
        target_node = target.node
        bend_w = w.bend * (w.rail_bend_factor if st.is_rail else 1.0)
        mates = st.mates
        related = st.related
        has_mates = any(self.nets[m].edges for m in mates)
        blocked, term, escape, corridor = self.blocked, self.term, self.escape, self.corridor
        occ_h, occ_v, occ_x = self.occ_h, self.occ_v, self.occ_x
        edge_r, edge_d = self.edge_r, self.edge_d
        near_body = self.near_body
        label_zone = self.label_zone
        own = st.adj
        x0, y0 = self.x0, self.y0
        conflict = 60.0
        free_k = (FREE, k)

        heap: List[Tuple[float, int, int, int, int, int]] = []
        INF = 1e18
        best = [INF] * (size * 15)
        parent: Dict[int, int] = {}
        counter = 0
        gxa, gya = self.gx, self.gy
        if margin is not None:
            src_nodes = [first_source.node] if first_source is not None else list(st.adj)
            xs = [gxa[n] for n in src_nodes] + [tx]
            ys = [gya[n] for n in src_nodes] + [ty]
            bx1, bx2 = min(xs) - margin, max(xs) + margin
            by1, by2 = min(ys) - margin, max(ys) + margin
        else:
            bx1 = by1 = -10 ** 9
            bx2 = by2 = 10 ** 9

        def h(n: int) -> int:
            return abs(gxa[n] - tx) + abs(gya[n] - ty)

        if first_source is not None:
            s = first_source.node
            best[(s * 5 + NONE) * 3] = 0.0
            heapq.heappush(heap, (h(s), h(s), counter, s, first_source.out_dir + 10, 0))
        else:
            for n, dirs in st.adj.items():
                if len(dirs) >= 3 or term[n] == k or not dirs:
                    continue
                if occ_h[n] not in free_k or occ_v[n] not in free_k or occ_x[n] not in free_k:
                    continue
                if escape[n] not in free_k:
                    continue
                is_corner = len(dirs) == 2 and dirs not in ({RIGHT, LEFT}, {UP, DOWN})
                if is_corner:
                    c0 = 0.0
                else:
                    c0 = w.junction + bend_w
                    for d0 in dirs:
                        m = self._step(n, d0)
                        md = st.adj.get(m)
                        if md and len(md) >= 2 and md not in ({RIGHT, LEFT}, {UP, DOWN}) and term[m] == FREE:
                            c0 += w.jog
                        elif md and m >= 0:
                            m2 = self._step(m, d0)
                            md2 = st.adj.get(m2)
                            if md2 and len(md2) >= 2 and md2 not in ({RIGHT, LEFT}, {UP, DOWN}) and term[m2] == FREE:
                                c0 += 0.5 * bend_w
                sid = (n * 5 + NONE) * 3 + 1
                if c0 < best[sid]:
                    best[sid] = c0
                    counter += 1
                    heapq.heappush(heap, (c0 + h(n), h(n), counter, n, NONE, 1))

        found = -1
        expansions = 0
        limit = 120_000 if not soft else 250_000
        heappop, heappush = heapq.heappop, heapq.heappush
        while heap:
            f, hu, _, u, d, b = heappop(heap)
            forced = -1
            if d >= 10:  # first step out of a pin terminal: direction is forced
                forced = d - 10
                d = NONE
            sid = (u * 5 + d) * 3 + b
            g = best[sid]
            if f - hu > g + 1e-9:
                continue
            if u == target_node and d == arrive_dir:
                found = sid
                break
            expansions += 1
            if expansions > limit:
                break
            src_dirs = own.get(u) if (d == NONE and forced < 0) else None
            u_col = u % W
            for d2 in (RIGHT, LEFT, DOWN, UP):
                if forced >= 0 and d2 != forced:
                    continue
                if d != NONE and d2 == _OPP[d]:
                    continue
                if src_dirs is not None and d2 in src_dirs:
                    continue
                if d2 == RIGHT:
                    if u_col == W - 1:
                        continue
                    v = u + 1
                    eo = edge_r[u]
                elif d2 == LEFT:
                    if u_col == 0:
                        continue
                    v = u - 1
                    eo = edge_r[v]
                elif d2 == DOWN:
                    v = u + W
                    if v >= size:
                        continue
                    eo = edge_d[u]
                else:
                    v = u - W
                    if v < 0:
                        continue
                    eo = edge_d[v]
                cost = w.step
                if eo != FREE:
                    if eo == k or not soft:
                        continue
                    cost += conflict
                nb = b
                if d != NONE and d2 != d:
                    # Bending at u requires u to be free of every other net.
                    if term[u] != FREE:
                        continue
                    eu = escape[u]
                    if eu != FREE and eu != k and not (eu == SHARED and k in self.escape_shared.get(u, ())):
                        if not relaxed:
                            continue
                        cost += 25.0
                    if occ_h[u] not in free_k or occ_v[u] not in free_k or occ_x[u] not in free_k:
                        if not soft:
                            continue
                        cost += conflict
                    cost += bend_w
                    if b == 1:
                        cost += w.jog
                    nb = 1
                elif d != NONE:
                    # Straight through u: only perpendicular straight traffic may share it.
                    bad = occ_x[u] not in free_k or (
                        occ_h[u] not in free_k if _HORIZONTAL[d2] else occ_v[u] not in free_k)
                    if bad:
                        if not soft:
                            continue
                        cost += conflict
                    nb = 2 if b >= 1 else 0
                else:
                    nb = 0 if forced >= 0 else 1
                if not (bx1 <= gxa[v] <= bx2 and by1 <= gya[v] <= by2):
                    continue
                # Entering v.
                if v == target_node:
                    if d2 != arrive_dir:
                        continue
                else:
                    if blocked[v] or v in own or term[v] != FREE:
                        continue
                    ev = escape[v]
                    if ev != FREE and ev != k:
                        if ev == SHARED and k in self.escape_shared.get(v, ()):
                            pass
                        elif not relaxed:
                            ed = self.escape_dir[v]
                            if ed >= 0 and _HORIZONTAL[d2] != _HORIZONTAL[ed]:
                                cost += 16.0
                            else:
                                continue
                        else:
                            cost += 25.0
                    if occ_x[v] not in free_k:
                        if not soft:
                            continue
                        cost += conflict
                    ov_h, ov_v = occ_h[v], occ_v[v]
                    if _HORIZONTAL[d2]:
                        same, cross = ov_h, ov_v
                    else:
                        same, cross = ov_v, ov_h
                    if same not in free_k:
                        if not soft:
                            continue
                        cost += conflict
                    if cross not in free_k:
                        cost += w.crossing_related if cross in related else w.crossing
                    cv = corridor[v]
                    if cv != FREE and cv != k:
                        cost += w.pin_corridor
                    if near_body[v]:
                        cost += w.hug
                    if label_zone[v]:
                        cost += w.label
                # Parallel neighbours (one grid step to either side).
                if _HORIZONTAL[d2]:
                    e = u if d2 == RIGHT else v
                    side1 = edge_r[e - W] if e >= W else FREE
                    side2 = edge_r[e + W] if e + W < size else FREE
                else:
                    e = u if d2 == DOWN else v
                    col = e % W
                    side1 = edge_d[e - 1] if col > 0 else FREE
                    side2 = edge_d[e + 1] if col < W - 1 else FREE
                beside_mate = False
                if side1 != FREE and side1 != k:
                    if side1 in mates:
                        beside_mate = True
                    elif side1 not in related:
                        cost += w.near_foreign
                if side2 != FREE and side2 != k:
                    if side2 in mates:
                        beside_mate = True
                    elif side2 not in related:
                        cost += w.near_foreign
                if has_mates and not beside_mate:
                    cost += w.bundle_stray

                ng = g + cost
                vid = (v * 5 + d2) * 3 + nb
                if ng < best[vid] - 1e-9:
                    best[vid] = ng
                    parent[vid] = sid
                    counter += 1
                    hv = abs(gxa[v] - tx) + abs(gya[v] - ty)
                    heappush(heap, (ng + hv, hv, counter, v, d2, nb))
        if found < 0:
            return None, 0.0
        path: List[int] = []
        sid = found
        while True:
            path.append(sid // 15)
            if sid not in parent:
                break
            sid = parent[sid]
        path.reverse()
        return path, best[found]

    # --------------------------------------------------------- occupancy
    def _dir(self, a: int, b: int) -> int:
        if b == a + 1:
            return RIGHT
        if b == a - 1:
            return LEFT
        if b == a + self.W:
            return DOWN
        return UP

    def _commit_path(self, st: _NetState, path: List[int]) -> None:
        touched: Set[int] = set()
        for a, b in zip(path, path[1:]):
            d = self._dir(a, b)
            if d in (RIGHT, DOWN):
                key = (a, d)
            else:
                key = (b, _OPP[d])
            if key in st.edges:
                continue
            st.edges.add(key)
            st.adj.setdefault(a, set()).add(d)
            st.adj.setdefault(b, set()).add(_OPP[d])
            if key[1] == RIGHT:
                self.edge_r[key[0]] = st.index
            else:
                self.edge_d[key[0]] = st.index
            touched.add(a)
            touched.add(b)
        for n in touched:
            self._update_node(st, n)

    def _update_node(self, st: _NetState, n: int) -> None:
        k = st.index
        if self.occ_h[n] == k:
            self.occ_h[n] = FREE
        if self.occ_v[n] == k:
            self.occ_v[n] = FREE
        if self.occ_x[n] == k:
            self.occ_x[n] = FREE
        dirs = st.adj.get(n)
        if not dirs:
            return
        if dirs == {RIGHT, LEFT}:
            self.occ_h[n] = k
        elif dirs == {UP, DOWN}:
            self.occ_v[n] = k
        else:
            self.occ_x[n] = k

    def _rip_up(self, st: _NetState) -> None:
        for (n, d) in st.edges:
            if d == RIGHT:
                self.edge_r[n] = FREE
            else:
                self.edge_d[n] = FREE
        nodes = list(st.adj)
        st.edges = set()
        st.adj = {}
        for n in nodes:
            self._update_node(st, n)

    def _restore(self, st: _NetState, edges: Set[Tuple[int, int]], adj: Dict[int, Set[int]], cost: float,
                 failed: int) -> None:
        self._rip_up(st)
        st.edges = set(edges)
        st.adj = {n: set(d) for n, d in adj.items()}
        for (n, d) in st.edges:
            if d == RIGHT:
                self.edge_r[n] = st.index
            else:
                self.edge_d[n] = st.index
        for n in st.adj:
            self._update_node(st, n)
        st.cost = cost
        st.failed = failed

    # ----------------------------------------------------- rip-up & reroute
    def _evaluate(self, st: _NetState) -> float:
        """Cost of a net's current tree against the current environment."""
        w = self.w
        k = st.index
        W = self.W
        cost = 0.0
        bend_w = w.bend * (w.rail_bend_factor if st.is_rail else 1.0)
        has_mates = any(self.nets[m2].edges for m2 in st.mates)
        for (n, d) in st.edges:
            cost += w.step
            if d == RIGHT:
                side1 = self.edge_r[n - W] if n - W >= 0 else FREE
                side2 = self.edge_r[n + W] if n + W < W * self.H else FREE
            else:
                side1 = self.edge_d[n - 1] if (n % W) > 0 else FREE
                side2 = self.edge_d[n + 1] if (n % W) < W - 1 else FREE
            beside_mate = False
            for o in (side1, side2):
                if o == FREE or o == k:
                    continue
                if o in st.mates:
                    beside_mate = True
                elif o not in st.related:
                    cost += w.near_foreign
            if not beside_mate and has_mates:
                cost += w.bundle_stray
        for n, dirs in st.adj.items():
            if len(dirs) == 2 and dirs not in ({RIGHT, LEFT}, {UP, DOWN}):
                cost += bend_w
            if len(dirs) >= 3:
                cost += w.junction
            if len(dirs) == 2 and dirs in ({RIGHT, LEFT}, {UP, DOWN}):
                foreign = self.occ_v[n] if dirs == {RIGHT, LEFT} else self.occ_h[n]
                if foreign not in (FREE, k):
                    cost += w.crossing_related if foreign in st.related else w.crossing
            if self.term[n] == FREE:
                ev = self.escape[n]
                if ev != FREE and ev != k and not (ev == SHARED and k in self.escape_shared.get(n, ())):
                    cost += 16.0
                cv = self.corridor[n]
                if cv != FREE and cv != k:
                    cost += w.pin_corridor
                if self.near_body[n]:
                    cost += w.hug
        cost += w.jog * self._count_jogs(st)
        return cost + 500.0 * st.failed

    def _count_jogs(self, st: _NetState) -> int:
        jogs = 0
        for n, dirs in st.adj.items():
            if len(dirs) < 2 or dirs in ({RIGHT, LEFT}, {UP, DOWN}):
                continue
            for d in dirs:
                m = self._step(n, d)
                md = st.adj.get(m, set())
                if len(md) >= 2 and md not in ({RIGHT, LEFT}, {UP, DOWN}) and n < m and self.term[m] == FREE:
                    jogs += 1
        return jogs

    def _lower_bound(self, st: _NetState) -> float:
        xs = [self.gx[t.node] for t in st.terminals]
        ys = [self.gy[t.node] for t in st.terminals]
        return ((max(xs) - min(xs)) + (max(ys) - min(ys))) * self.w.step

    def _reroute_pass(self, order: List[_NetState]) -> bool:
        """Re-routes every net with noticeable excess cost against all others;
        a new route is kept only if it is strictly cheaper.  Recovery (which
        would disturb other nets) is disabled here so a rejected attempt can
        be restored exactly."""
        improved = False
        scored = []
        for st in order:
            cost = self._evaluate(st)
            excess = cost - self._lower_bound(st)
            if st.failed or excess >= self.w.bend:
                scored.append((-excess / max(1, len(st.terminals) - 1), st.index, st, cost))
        scored.sort(key=lambda t: (t[0], t[1]))
        for _, _, st, _ in scored:
            old_cost = self._evaluate(st)
            snapshot = (set(st.edges), {n: set(d) for n, d in st.adj.items()}, st.cost, st.failed)
            self._rip_up(st)
            self._route_net(st, depth=2)
            new_cost = self._evaluate(st)
            if new_cost < old_cost - 1e-6:
                improved = True
            else:
                self._restore(st, *snapshot)
        return improved

    # ================================================================ output
    def _emit(self) -> None:
        g = GRID_SIZE
        wires: List[WireSegment] = []
        junctions: List[WireJunction] = []
        for st in self.nets:
            net = self.sch.nets[st.index]
            info = self.sem.nets.get(net.name)
            category = None
            if info is not None and info.cls == NetClass.GROUND:
                category = "ground"
            elif info is not None and info.cls == NetClass.POWER and not any(
                k in net.name.upper() for k in ("BAT", "RAW", "HV", "36V", "48V")
            ):
                category = "power"
            color = net_color(net.name, dark_theme=True, category=category)
            net.color = color
            if not st.adj:
                continue
            terminals = {t.node for t in st.terminals}
            breaks = {n for n, d in st.adj.items() if len(d) != 2 or d not in ({RIGHT, LEFT}, {UP, DOWN})}
            breaks |= terminals & set(st.adj)
            visited: Set[Tuple[int, int]] = set()
            for start in sorted(breaks):
                for d in sorted(st.adj[start]):
                    key = (start, d) if d in (RIGHT, DOWN) else (self._step(start, d), _OPP[d])
                    if key in visited:
                        continue
                    n = start
                    while True:
                        m = self._step(n, d)
                        k2 = (n, d) if d in (RIGHT, DOWN) else (m, _OPP[d])
                        visited.add(k2)
                        n = m
                        if n in breaks:
                            break
                    (ax, ay), (bx, by) = self._xy(start), self._xy(n)
                    wires.append(WireSegment(Point(ax * g, ay * g), Point(bx * g, by * g), net.name, color))
            for n, dirs in sorted(st.adj.items()):
                if len(dirs) >= 3 or (n in terminals and len(dirs) >= 2):
                    x, y = self._xy(n)
                    junctions.append(WireJunction(point=Point(x * g, y * g), radius=3.5, net_name=net.name))
        self.sch.wires = wires
        self.sch.junctions = junctions


SchematicRouter = GridRouter


def route_schematic(schematic: Schematic, config: Optional[LayoutConfig] = None,
                    semantics: Optional[Semantics] = None) -> GridRouter:
    """Routes all nets of a placed schematic (wires and junction dots)."""
    from schematic.layout.normalize import merge_shared_pin_nets

    if merge_shared_pin_nets(schematic):
        semantics = None
    router = GridRouter(schematic, semantics=semantics, config=config)
    router.route()
    return router
