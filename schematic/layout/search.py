"""Placement local search on the estimated routing objective.

Mixed into ``Placer``: deterministic steepest descent over align, shift,
relocate and swap moves.  Every candidate move is checked for legality
(clearances) and scored incrementally by ``Estimator.move_delta``; the best
improving move per component is applied.  Pins are re-configured and the
layout re-legalised between sweeps, because free pin sides follow geometry.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from schematic.models import Side
from schematic.layout.estimate import Estimator, Rect, Terminal
from schematic.layout.pin_allocator import snap

Pt = Tuple[float, float]


class LocalSearchMixin:
    def _estimator(self, g: _Group, home: Optional[Dict[str, Pt]] = None) -> Estimator:
        members = set(g.comps)
        sat_members = [s for s, sat in self.sats.items() if sat.anchor in members]
        nets: Dict[str, List[Terminal]] = {}
        factors: Dict[str, float] = {}
        for net in self.sch.nets:
            terms = [Terminal(c, p) for c, p in self._terminals(net) if c in members or c in sat_members]
            if len(terms) < 2:
                continue
            nets[net.name] = terms
            info = self.sem.nets[net.name]
            factors[net.name] = self.cfg.placement.rail_factor if info.is_rail else 1.0
        roots: Dict[str, int] = {}
        for name, terms in nets.items():
            if len(terms) <= 2 or self.sem.nets[name].is_power_like:
                continue
            on_hub = [i for i, t in enumerate(terms) if t.comp == g.hub]
            macro = [i for i, t in enumerate(terms) if t.comp in members]
            roots[name] = (on_hub or macro or [0])[0]
        pins: Dict[str, Dict[str, Tuple[float, float, Side]]] = {}
        rects: Dict[str, Rect] = {}
        follow: Dict[str, Tuple[str, float, float]] = {}
        for cid in list(g.comps) + sat_members:
            comp = self.comps[cid]
            pins[cid] = {pid: (p.position.x - comp.x, p.position.y - comp.y, p.side) for pid, p in comp.pins.items()}
            rects[cid] = (0.0, 0.0, comp.width, comp.height)
        for s in sat_members:
            sat = self.sats[s]
            follow[s] = (sat.anchor, sat.rel[0], sat.rel[1])
        return Estimator(nets, factors, pins, rects, dict(g.pos), self.cfg.placement, follow=follow,
                         anchor_pos=home or dict(g.pos), roots=roots)

    def _search(self, g: _Group) -> None:
        if len(g.comps) < 2:
            return
        home = dict(g.pos)
        for sweep in range(self.cfg.search_sweeps):
            est = self._estimator(g, home)
            improved = self._sweep(g, est)
            g.pos.update({c: est.pos[c] for c in g.comps})
            self._configure(g)
            self._legalize(g)
            if not improved:
                break

    def _sweep(self, g: _Group, est: Estimator) -> bool:
        improved_any = False
        order = sorted(g.comps, key=lambda c: (c == g.hub, g.comps.index(c)))
        for _ in range(self.cfg.search_passes):
            improved = False
            for cid in order:
                best_delta, best_moves = -1e-6, None
                for moves in self._candidates(g, est, cid):
                    if not all(self._legal(g, c, p, ignore=tuple(moves)) for c, p in moves.items()):
                        continue
                    delta, token = est.move_delta(moves, bound=best_delta)
                    est.revert(token)
                    if delta < best_delta:
                        best_delta, best_moves = delta, moves
                if best_moves is not None:
                    delta, token = est.move_delta(best_moves)
                    est.commit(token)
                    for c, p in best_moves.items():
                        g.pos[c] = p
                    improved = True
            improved_any |= improved
            if not improved:
                break
        return improved_any

    def _candidates(self, g: _Group, est: Estimator, cid: str) -> List[Dict[str, Pt]]:
        x, y = g.pos[cid]
        seen: Set[Pt] = {(x, y)}
        out: List[Dict[str, Pt]] = []

        def add(p: Pt) -> None:
            p = (snap(p[0]), snap(p[1]))
            if p not in seen:
                seen.add(p)
                out.append({cid: p})

        # Align moves: make one connection of this component straight.
        own = [cid] + [s for s, sat in self.sats.items() if sat.anchor == cid]
        for name, terms in est.nets.items():
            mine = [t for t in terms if t.comp in own]
            if not mine:
                continue
            others = [t for t in terms if t.comp not in own]
            for t in mine:
                tx, ty, tside = est.terminal_point(t)
                for o in others:
                    ox, oy, oside = est.terminal_point(o)
                    if tside in (Side.LEFT, Side.RIGHT) and oside in (Side.LEFT, Side.RIGHT):
                        add((x, y + (oy - ty)))
                    elif tside in (Side.TOP, Side.BOTTOM) and oside in (Side.TOP, Side.BOTTOM):
                        add((x + (ox - tx), y))
        for d in self.cfg.search_shifts:
            add((x + d, y))
            add((x - d, y))
            add((x, y + d))
            add((x, y - d))
        # Relocations: right next to (left / right / above / below) the parts it is
        # most strongly wired to.  These large moves let the search flip a part to
        # the other side of a hub or stack it under its bus mates.
        f = self._footprint(cid)
        partners = sorted(
            (o for o in g.comps if o != cid),
            key=lambda o: (-self.pair_nets.get((cid, o) if cid < o else (o, cid), 0), g.comps.index(o)),
        )[:self.cfg.search_partners]
        for o in partners:
            if not self.pair_nets.get((cid, o) if cid < o else (o, cid), 0):
                continue
            ofp = self._abs_fp(o, g.pos[o])
            hg, vg = self._gaps(cid, o)
            mid_y = (ofp[1] + ofp[3]) / 2.0 - (f[1] + f[3]) / 2.0
            add((ofp[2] + hg - f[0], mid_y))
            add((ofp[0] - hg - f[2], mid_y))
            for nx in (ofp[0] - f[0], ofp[2] - f[2]):
                add((nx, ofp[3] + vg - f[1]))
                add((nx, ofp[1] - vg - f[3]))
        # Swaps with nearby components of the group.
        comp = self.comps[cid]
        mx, my = x + comp.width / 2.0, y + comp.height / 2.0
        near = sorted(
            (o for o in g.comps if o != cid),
            key=lambda o: abs(g.pos[o][0] + self.comps[o].width / 2.0 - mx)
            + abs(g.pos[o][1] + self.comps[o].height / 2.0 - my),
        )[:3]
        for o in near:
            oc = self.comps[o]
            ox, oy = g.pos[o]
            ocx, ocy = ox + oc.width / 2.0, oy + oc.height / 2.0
            out.append({
                cid: (snap(ocx - comp.width / 2.0), snap(ocy - comp.height / 2.0)),
                o: (snap(mx - oc.width / 2.0), snap(my - oc.height / 2.0)),
            })
        return out
