"""Connectivity-driven component placement.

Pipeline (per connected sub-circuit):

1. **Classification** -- pull-up / filter / decoupling passives become
   *satellites*: they are not placed on their own but attached next to the pin
   they serve, vertically oriented with the supply side away from the signal.
2. **Global embedding** -- stress majorization (Gansner, Koren & North) on the
   weighted connectivity graph gives every component a position that reflects
   the circuit topology; declared pin sides act as direction constraints
   (a part wired to the hub's left-hand pins must lie left of the hub).
3. **Port configuration** -- free pins face the components they connect to
   and are sorted by the position of their peers (barycentric ordering), so
   bundles leave a component without crossing.  Two-pin parts are initially
   oriented along their connection; ambiguous axes are routed as candidates.
4. **Legalization** -- overlaps are removed with corridor clearances while
   keeping the relative order of components (mental-map preservation).
5. **Local search** -- deterministic steepest-descent over align / shift /
   swap moves on the *estimated routing* objective (bends, crossings, wire
   length, routes through bodies, area): placement anticipates routing.

Unconnected sub-circuits are packed next to each other; isolated parts go
into a row below the drawing.

Module layout: this file holds the placer core (classification, port
configuration, satellites, legalization, packing); the initial placements
live in ``seeds.py`` and the local search in ``search.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from schematic.models import Component, Pin, Schematic, Side, GRID_SIZE
from schematic.layout.config import DEFAULT_CONFIG, LayoutConfig
from schematic.layout.estimate import Rect
from schematic.layout.pin_allocator import (
    OPPOSITE,
    OUTWARD,
    apply_order,
    default_sides,
    orient_switch,
    orient_two_pin,
    resolve_pin,
    rotate_component_90,
    size_and_place_pins,
    snap,
    switch_common,
)
from schematic.layout.search import LocalSearchMixin
from schematic.layout.seeds import LayeredSeedMixin, StressSeedMixin
from schematic.layout.semantics import NetClass, Semantics, analyze

Pt = Tuple[float, float]


@dataclass
class Satellite:
    comp: str
    anchor: str            # macro component the satellite hangs off
    anchor_pin: str        # pin id on the anchor
    near_pin: str          # satellite pin wired towards the anchor pin
    far_pin: str           # satellite pin wired to the supply / ground
    above: bool            # True: hangs above the wire (pull-up), False: below
    slot: int = 0          # position along the wire when several share a pin
    dist: float = 60.0     # distance of the part's wire tap from the anchor pin
    rel: Pt = (0.0, 0.0)   # top-left offset relative to the anchor's top-left
    conventional_above: bool = True                       # supply up, ground down
    anchors: List[Tuple[str, str]] = field(default_factory=list)  # candidate anchor pins


@dataclass
class _Group:
    comps: List[str]
    hub: str
    pos: Dict[str, Pt] = field(default_factory=dict)  # top-left


class Placer(LayeredSeedMixin, StressSeedMixin, LocalSearchMixin):
    def __init__(self, schematic: Schematic, semantics: Optional[Semantics] = None,
                 config: Optional[LayoutConfig] = None,
                 orientation_overrides: Optional[Dict[str, bool]] = None,
                 keep_positions: bool = False):
        self.sch = schematic
        self.cfg = config or DEFAULT_CONFIG
        self.sem = semantics or analyze(schematic)
        self.comps = schematic.components
        self.orientation_overrides = orientation_overrides or {}
        self.keep_positions = keep_positions
        self._forced_orientations: Dict[str, bool] = {}
        self.sats: Dict[str, Satellite] = {}
        self.macro: List[str] = []
        self.isolated: List[str] = []
        self.pos: Dict[str, Pt] = {}
        self.conn_pin: Dict[int, str] = {}  # id(NetConnection) -> pin id
        self._fp_cache: Dict[str, Rect] = {}
        self._hub: Optional[str] = None     # hub of the group being configured
        self._columns: Dict[str, int] = {}  # layered seed: column of every part

    # ================================================================ public
    def place(self, strategy: Optional[str] = None, search: bool = True) -> None:
        """Places all components.

        ``strategy`` selects the initial placement: ``"layered"`` (port-aware
        columns), ``"stress"`` (stress majorization) or ``None`` to build both
        and keep the one with the better estimated routing cost.  ``search``
        enables the local search on the estimated routing objective."""
        sch = self.sch
        if not self.comps:
            sch.width, sch.height = 600.0, 400.0
            return
        if not self.keep_positions:
            for comp in self.comps.values():
                comp.x, comp.y = 0.0, 0.0
                default_sides(comp)
                size_and_place_pins(comp)
        self._resolve_connections()
        self._classify()
        self._apply_orientation_overrides()
        groups = self._groups()
        if self.keep_positions:
            # Local refinement of an already placed drawing: keep the layout,
            # apply the requested axis, re-slot satellites and legalise.
            for g in groups:
                g.pos = {cid: (self.comps[cid].x, self.comps[cid].y) for cid in g.comps}
                self._configure(g)
                self._legalize(g)
            self._apply_kept(groups)
        else:
            for g in groups:
                self._initial_layout(g, strategy)
                if search:
                    self._search(g)
            self._pack(groups)
            self._place_isolated()
            self._apply()
        self._finalize_bounds()

    # ========================================================= connectivity
    def _resolve_connections(self) -> None:
        for net in self.sch.nets:
            for conn in net.connections:
                comp = self.comps.get(conn.component_id)
                pin = resolve_pin(comp, conn.pin_id, conn.pin_name) if comp else None
                if pin is not None:
                    self.conn_pin[id(conn)] = pin.id
                    conn.pin_id = pin.id

    def _terminals(self, net) -> List[Tuple[str, str]]:
        seen: Dict[Tuple[str, str], None] = {}
        for conn in net.connections:
            pid = self.conn_pin.get(id(conn))
            if pid is not None:
                seen.setdefault((conn.component_id, pid), None)
        return list(seen)

    def _owner(self, cid: str) -> str:
        sat = self.sats.get(cid)
        return sat.anchor if sat else cid

    # ======================================================= classification
    def _classify(self) -> None:
        roles = self.sem.component_roles
        hubs = self.sem.hubs
        connected: Set[str] = set()
        for net in self.sch.nets:
            terms = self._terminals(net)
            if len({c for c, _ in terms}) >= 2 or len(terms) >= 2:
                connected.update(c for c, _ in terms)

        for cid in self.comps:
            if cid not in connected:
                self.isolated.append(cid)

        candidates = [c for c in self.comps if roles.get(c) in ("shunt", "decoupling") and c in connected]
        for cid in candidates:
            sat = self._make_satellite(cid, candidates, hubs)
            if sat is not None:
                self.sats[cid] = sat
        self.macro = [c for c in self.comps if c in connected and c not in self.sats]
        if not self.macro and self.sats:
            # Nothing to hang the satellites on: place them as ordinary parts.
            self.macro = sorted(self.sats, key=list(self.comps).index)
            self.sats.clear()

        self._assign_slots()
        self._count_pair_nets()

    def _count_pair_nets(self) -> None:
        """Number of nets joining each pair of placeable components; corridors
        between strongly connected parts must leave room for their jogs."""
        self.pair_nets: Dict[Tuple[str, str], int] = {}
        for net in self.sch.nets:
            owners = sorted({self._owner(c) for c, _ in self._terminals(net)})
            for i, a in enumerate(owners):
                for b in owners[i + 1:]:
                    self.pair_nets[(a, b)] = self.pair_nets.get((a, b), 0) + 1

    def _choose_satellite_anchors(self, g: "_Group") -> None:
        """Picks anchor pin and side for every satellite of a group.

        A vertical part hanging off a wire walls in the neighbouring pins on
        that side of the anchor, so the choice minimises walled-in connected
        pins first, then deviations from the convention (supply up, ground
        down), then prefers anchoring at the hub."""
        members = set(g.comps)
        connected = {(c, p) for net in self.sch.nets for c, p in self._terminals(net)
                     if len(self._terminals(net)) >= 2}
        changed = False
        for cid in sorted(self.sats, key=list(self.comps).index):
            sat = self.sats[cid]
            options = []
            body = self.comps[cid]
            span = 40.0 + max(body.width, body.height)
            far_info = self._pin_net_info(cid, sat.far_pin)
            far_net_name = far_info.name if far_info is not None else None
            for rank, (a, pid) in enumerate(sat.anchors):
                if a not in members:
                    continue
                apin = self.comps[a].pins[pid]
                for above in (sat.conventional_above, not sat.conventional_above):
                    lo, hi = (apin.position.y - span, apin.position.y - 1) if above else \
                        (apin.position.y + 1, apin.position.y + span)
                    walled = 0.0
                    seen_rails = set()
                    for q in self.comps[a].pins.values():
                        if q.id == pid or q.side != apin.side or (a, q.id) not in connected:
                            continue
                        if not (lo <= q.position.y <= hi):
                            continue
                        qinfo = self._pin_net_info(a, q.id)
                        if qinfo is not None and qinfo.name == far_net_name:
                            continue
                        if qinfo is not None and qinfo.is_rail:
                            if qinfo.name not in seen_rails:
                                seen_rails.add(qinfo.name)
                                walled += 0.25
                        else:
                            walled += 1.0
                    score = (10.0 * walled + 12.0 * (above != sat.conventional_above)
                             + (0.0 if a in self.sem.hubs else 1.0)
                             + (6.0 if self.sem.component_roles.get(a) == "series" else 0.0)
                             + (4.0 if apin.side in (Side.TOP, Side.BOTTOM) else 0.0) + 0.01 * rank)
                    options.append((score, a, pid, above))
            if not options:
                continue
            _, a, pid, above = min(options, key=lambda o: o[0])
            if (a, pid, above) != (sat.anchor, sat.anchor_pin, sat.above):
                sat.anchor, sat.anchor_pin, sat.above = a, pid, above
                changed = True
        if changed:
            self._count_pair_nets()

    def _assign_slots(self) -> None:
        """Distinct slots along the wires for satellites on one side of an anchor."""
        groups: Dict[Tuple[str, str], List[Satellite]] = {}
        for sat in self.sats.values():
            side = self.comps[sat.anchor].pins[sat.anchor_pin].side.value
            groups.setdefault((sat.anchor, side), []).append(sat)
        for lst in groups.values():
            def order(s: Satellite):
                pin = self.comps[s.anchor].pins[s.anchor_pin]
                return (pin.order, not s.above, list(self.comps).index(s.comp))
            lst.sort(key=order)
            # Cumulative distances: each part needs its body plus its upright
            # name / value labels before the next one can hang off the wire.
            dist = self.cfg.spacing.satellite_offset
            for i, s in enumerate(lst):
                s.slot = i
                s.dist = dist
                dist += 40.0 + _ceil_grid(label_width(self.comps[s.comp])) + 20.0

    def _make_satellite(self, cid: str, candidates: List[str], hubs: List[str]) -> Optional[Satellite]:
        comp = self.comps[cid]
        if len(comp.pins) != 2:
            return None
        role = self.sem.component_roles[cid]
        pin_net: Dict[str, object] = {}
        for net in self.sch.nets:
            for t_cid, pid in self._terminals(net):
                if t_cid == cid:
                    pin_net[pid] = net
        if len(pin_net) != 2:
            return None
        (p1, n1), (p2, n2) = sorted(pin_net.items())
        i1, i2 = self.sem.nets[n1.name], self.sem.nets[n2.name]
        if role == "shunt":
            if comp.kind == "diode" and {i1.cls, i2.cls} == {NetClass.POWER, NetClass.GROUND}:
                p_pwr, p_gnd, n_pwr, n_gnd = (p1, p2, n1, n2) if i1.cls == NetClass.POWER else (p2, p1, n2, n1)
                near, far, near_net = p_pwr, p_gnd, n_pwr
                above = False
            else:
                near, far, near_net, far_info = (p1, p2, n1, i2) if not i1.is_power_like else (p2, p1, n2, i1)
                above = far_info.cls == NetClass.POWER
        else:  # decoupling: hang below the supply pin it decouples
            if i1.cls == NetClass.POWER:
                near, far, near_net = p1, p2, n1
            else:
                near, far, near_net = p2, p1, n2
            above = False
        anchors = [
            (t_cid, pid) for t_cid, pid in self._terminals(near_net)
            if t_cid != cid and t_cid not in candidates
        ]
        if not anchors:
            return None
        hub_anchor = [a for a in anchors if a[0] in hubs]
        anchor = (hub_anchor or anchors)[0]
        return Satellite(comp=cid, anchor=anchor[0], anchor_pin=anchor[1], near_pin=near, far_pin=far,
                         above=above, conventional_above=above, anchors=anchors)

    def _groups(self) -> List[_Group]:
        adj: Dict[str, Set[str]] = {c: set() for c in self.macro}
        for net in self.sch.nets:
            owners = sorted({self._owner(c) for c, _ in self._terminals(net)} & set(self.macro))
            for a in owners:
                for b in owners:
                    if a != b:
                        adj[a].add(b)
        seen: Set[str] = set()
        groups: List[_Group] = []
        for c in self.macro:
            if c in seen:
                continue
            comp_list: List[str] = []
            stack = [c]
            seen.add(c)
            while stack:
                n = stack.pop()
                comp_list.append(n)
                for m in sorted(adj[n]):
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
            order = list(self.comps)
            comp_list.sort(key=order.index)
            groups.append(_Group(comps=comp_list, hub=self._group_hub(comp_list)))
        groups.sort(key=lambda g: (-len(g.comps), list(self.comps).index(g.comps[0])))
        return groups

    def _weights(self, comps: List[str]) -> Dict[Tuple[str, str], float]:
        """Symmetric connection strength between components of a group."""
        members = set(comps)
        w: Dict[Tuple[str, str], float] = {}
        for net in self.sch.nets:
            info = self.sem.nets[net.name]
            owners = sorted({self._owner(c) for c, _ in self._terminals(net)} & members)
            k = len(owners)
            if k < 2:
                continue
            factor = 0.15 if info.is_rail else 1.0
            base = factor / (k - 1)
            for i, a in enumerate(owners):
                for b in owners[i + 1:]:
                    w[(a, b)] = w.get((a, b), 0.0) + base
                    w[(b, a)] = w.get((b, a), 0.0) + base
        return w

    def _group_hub(self, comps: List[str]) -> str:
        w = self._weights(comps)
        score = {c: 0.0 for c in comps}
        for (a, _), v in w.items():
            score[a] += v
        for c in comps:
            score[c] += 0.05 * len(self.comps[c].pins)
        return max(comps, key=lambda c: (score[c], -list(self.comps).index(c)))

    # ================================================== initial placement
    def _initial_layout(self, g: _Group, strategy: Optional[str] = None) -> None:
        """Builds the initial placement of a group.

        * layered: port-aware columns grown from the hub (Sugiyama-style),
          barycentric ordering and pin-aligning coordinates;
        * stress: stress-majorization embedding of the connectivity graph.

        Without an explicit strategy both are configured and legalised, then
        scored with the estimated routing objective; the layered seed wins
        ties because it is the more regular drawing."""
        builders = {"layered": (self._embed_layered,), "stress": (self._embed,)}.get(
            strategy or "", (self._embed_layered, self._embed))
        candidates: List[Tuple[float, int, Dict[str, Pt], Dict[str, Tuple[Side, int]]]] = []
        for rank, builder in enumerate(builders):
            builder(g)
            for _ in range(2):
                self._configure(g)
                self._legalize(g)
                if builder == self._embed_layered:
                    self._assign_coordinates(g)
            score = self._estimator(g).total()
            snapshot = {pid_key: (p.side, p.order) for pid_key, p in self._pin_items(g)}
            candidates.append((score, rank, dict(g.pos), snapshot))
        best = min(candidates, key=lambda c: (c[0], c[1]))
        self._fp_cache.clear()
        g.pos = dict(best[2])
        for key, p in self._pin_items(g):
            p.side, p.order = best[3][key]
        for cid in g.comps:
            comp = self.comps[cid]
            comp.x, comp.y = g.pos[cid]
            size_and_place_pins(comp)
        self._assign_slots()
        for sat in self.sats.values():
            if sat.anchor in g.comps:
                self._configure_satellite(sat)

    def _pin_items(self, g: _Group):
        members = list(g.comps) + [s for s, sat in self.sats.items() if sat.anchor in g.comps]
        for cid in members:
            for pid, p in self.comps[cid].pins.items():
                yield (cid, pid), p

    # ====================================================== global embedding

    def _bus_mates(self, cid: str) -> List[str]:
        """Components sharing a multi-drop (>= 3 parts) signal net with ``cid``."""
        out: List[str] = []
        for net in self.sch.nets:
            if self.sem.nets[net.name].is_power_like:
                continue
            owners = sorted({self._owner(c) for c, _ in self._terminals(net)})
            if cid in owners and len(owners) >= 3:
                out.extend(o for o in owners if o != cid and o not in out)
        return out

    def _relation(self, a: str, b: str) -> Optional[Tuple[int, int]]:
        """Direction from a to b implied by declared pin sides, if any."""
        votes: Dict[Tuple[int, int], int] = {}
        for net in self.sch.nets:
            info = self.sem.nets[net.name]
            if info.is_rail:
                continue
            terms = self._terminals(net)
            owners = {self._owner(c) for c, _ in terms}
            if not info.is_power_like and len(owners) >= 3 and self._hub in owners and a != self._hub and b != self._hub:
                continue
            ta = [p for c, p in terms if c == a]
            tb = [p for c, p in terms if c == b]
            if not ta or not tb:
                continue
            for pid in ta:
                pin = self.comps[a].pins[pid]
                if pin.side_specified:
                    d = {Side.LEFT: (-1, 0), Side.RIGHT: (1, 0), Side.TOP: (0, -1), Side.BOTTOM: (0, 1)}[pin.side]
                    votes[d] = votes.get(d, 0) + 1
            for pid in tb:
                pin = self.comps[b].pins[pid]
                if pin.side_specified:
                    d = {Side.LEFT: (1, 0), Side.RIGHT: (-1, 0), Side.TOP: (0, 1), Side.BOTTOM: (0, -1)}[pin.side]
                    votes[d] = votes.get(d, 0) + 1
        if not votes:
            return None
        return max(sorted(votes), key=lambda d: votes[d])

    # =================================================== port configuration
    def _peer_points(self, cid: str, pid: str, positions: Dict[str, Pt]) -> List[Pt]:
        """Positions of everything a pin is wired to (pin terminals when known)."""
        pts: List[Pt] = []
        for net in self.sch.nets:
            terms = self._terminals(net)
            if (cid, pid) not in terms:
                continue
            info = self.sem.nets[net.name]
            cand: List[Pt] = []
            # A multi-drop signal net (bus) is drawn as taps off the source's
            # line, so a device's pin faces the source, not its fellow devices.
            source = None
            if not info.is_power_like and len(terms) >= 3 and self._hub is not None and cid != self._hub:
                source = [(c, p) for c, p in terms if c == self._hub]
            for c, p in (source or terms):
                if c == cid:
                    continue
                owner = self._owner(c)
                if owner == cid and c in self.sats:
                    continue  # own satellite: follows this pin, not a direction hint
                if owner not in positions:
                    continue
                cand.append(self._terminal_abs(c, p, positions))
            if info.is_rail and len(cand) > 2:
                me = self._terminal_abs(cid, pid, positions)
                cand.sort(key=lambda q: abs(q[0] - me[0]) + abs(q[1] - me[1]))
                cand = cand[:2]
            elif info.is_power_like and not info.is_rail and len(cand) > 1:
                me = self._terminal_abs(cid, pid, positions)
                cand.sort(key=lambda q: abs(q[0] - me[0]) + abs(q[1] - me[1]))
                cand = cand[:1]
            pts.extend(cand)
        return pts

    def _terminal_abs(self, cid: str, pid: str, positions: Dict[str, Pt]) -> Pt:
        comp = self.comps[cid]
        if cid in self.sats:
            sat = self.sats[cid]
            ax, ay = positions.get(sat.anchor, (0.0, 0.0))
            x, y = ax + sat.rel[0], ay + sat.rel[1]
        else:
            x, y = positions.get(cid, (comp.x, comp.y))
        p = comp.pins[pid]
        return x + (p.position.x - comp.x), y + (p.position.y - comp.y)

    def _configure(self, g: _Group) -> None:
        """Assigns free pin sides / orders from geometry and resizes components."""
        self._fp_cache.clear()
        self._hub = g.hub
        positions = g.pos
        self._resolve_equivalent(g.comps, positions)
        for cid in g.comps:
            comp = self.comps[cid]
            cx = positions[cid][0] + comp.width / 2.0
            cy = positions[cid][1] + comp.height / 2.0
            if comp.is_standard and len(comp.pins) == 2:
                if cid in self._forced_orientations:
                    self._force_axis(comp, self._forced_orientations[cid])
                else:
                    self._orient_passive(comp, cx, cy, positions)
            elif comp.kind == "switch" and len(comp.pins) == 3:
                if comp.rotatable:
                    common = switch_common(comp)
                    pts = self._peer_points(cid, common.id, positions)
                    if pts:
                        mx = sum(p[0] for p in pts) / len(pts)
                        orient_switch(comp, Side.LEFT if mx < cx else Side.RIGHT)
                # A non-rotatable switch keeps its declared / default orientation.
            else:
                hub = self.comps[g.hub]
                hub_cx = positions[g.hub][0] + hub.width / 2.0
                stacked = self._stacked_rails(cid, g) if self.cfg.outer_rail_pins and cid != g.hub else set()
                rail_pins: List[Pin] = []
                for pin in comp.pins.values():
                    if pin.side_specified:
                        continue
                    info = self._pin_net_info(cid, pin.id)
                    if info is not None and info.name in stacked:
                        rail_pins.append(pin)
                        continue
                    pts = self._peer_points(cid, pin.id, positions)
                    if not pts:
                        continue
                    mx = sum(p[0] for p in pts) / len(pts)
                    if abs(mx - cx) <= comp.width / 2.0 and cid != g.hub and abs(cx - hub_cx) > 1.0:
                        # Peer directly above / below: use the outer side, so stacked
                        # parts connect with a U outside instead of looping round.
                        pin.side = Side.LEFT if cx < hub_cx else Side.RIGHT
                    elif abs(mx - cx) > 1.0:
                        pin.side = Side.LEFT if mx < cx else Side.RIGHT
                if rail_pins:
                    # Supply pins of parts stacked in a column go to the side away
                    # from their signals: the bus keeps the inner corridor and the
                    # rails get a clean trunk outside instead of a crossing comb.
                    sig = [p.side for p in comp.pins.values()
                           if p not in rail_pins and p.side in (Side.LEFT, Side.RIGHT)
                           and self._pin_net_info(cid, p.id) is not None]
                    if sig:
                        inner = Side.LEFT if sig.count(Side.LEFT) >= sig.count(Side.RIGHT) else Side.RIGHT
                    else:
                        inner = Side.LEFT if cx > hub_cx else Side.RIGHT
                    for pin in rail_pins:
                        pin.side = OPPOSITE[inner]

            def effective_y(q: Pt, cid=cid, cx=cx, cy=cy) -> float:
                qx, qy = q
                x_lo, x_hi = min(cx, qx), max(cx, qx)
                for other in g.comps:
                    if other == cid:
                        continue
                    oc = self.comps[other]
                    ox, oy = positions[other]
                    if ox > x_lo and ox + oc.width < x_hi and oy <= qy <= oy + oc.height:
                        y_top, y_bot = oy - 20.0, oy + oc.height + 20.0
                        yd = y_top if abs(cy - y_top) + abs(qy - y_top) <= abs(cy - y_bot) + abs(qy - y_bot) else y_bot
                        return yd + 0.05 * (qy - oy)
                return qy

            def key(pin: Pin, cid=cid) -> float:
                info = self._pin_net_info(cid, pin.id)
                if info is not None and info.is_rail:
                    return -1e6 if info.cls == NetClass.POWER else 1e6
                pts = self._peer_points(cid, pin.id, positions)
                if not pts:
                    return 5e5
                if pin.side in (Side.LEFT, Side.RIGHT):
                    return sum(effective_y(p) for p in pts) / len(pts)
                return sum(p[0] for p in pts) / len(pts)

            if not (comp.is_standard and len(comp.pins) <= 3):
                apply_order(comp, key)
            # Resize around the current centre.
            old_w, old_h = comp.width, comp.height
            comp.x, comp.y = positions[cid]
            size_and_place_pins(comp)
            if (comp.width, comp.height) != (old_w, old_h):
                nx = snap(positions[cid][0] + (old_w - comp.width) / 2.0)
                ny = snap(positions[cid][1] + (old_h - comp.height) / 2.0)
                positions[cid] = (nx, ny)
                comp.x, comp.y = nx, ny
                size_and_place_pins(comp)
        self._choose_satellite_anchors(g)
        self._assign_slots()
        for sat in self.sats.values():
            if sat.anchor in g.comps:
                self._configure_satellite(sat)

    def _stacked_rails(self, cid: str, g: _Group) -> Set[str]:
        """Rail nets this part shares with another part stacked in its column."""
        fa = self._abs_fp(cid, g.pos[cid])
        mine: Dict[str, None] = {}
        for net in self.sch.nets:
            info = self.sem.nets[net.name]
            if info.is_rail and any(c == cid for c, _ in self._terminals(net)):
                mine[net.name] = None
        out: Set[str] = set()
        if not mine:
            return out
        for other in g.comps:
            if other in (cid, g.hub):
                continue
            fb = self._abs_fp(other, g.pos[other])
            if min(fa[2], fb[2]) - max(fa[0], fb[0]) <= 0:
                continue
            for net in self.sch.nets:
                if net.name in mine and any(c == other for c, _ in self._terminals(net)):
                    out.add(net.name)
        return out

    def _pin_net_info(self, cid: str, pid: str):
        for net in self.sch.nets:
            if (cid, pid) in self._terminals(net):
                return self.sem.nets[net.name]
        return None

    def _orient_passive(self, comp: Component, cx: float, cy: float, positions: Dict[str, Pt]) -> None:
        if any(p.side_specified for p in comp.pins.values()):
            return
        if not comp.rotatable:
            return
        p1, p2 = list(comp.pins.values())
        pts1 = self._peer_points(comp.id, p1.id, positions)
        pts2 = self._peer_points(comp.id, p2.id, positions)
        c1 = (sum(p[0] for p in pts1) / len(pts1), sum(p[1] for p in pts1) / len(pts1)) if pts1 else None
        c2 = (sum(p[0] for p in pts2) / len(pts2), sum(p[1] for p in pts2) / len(pts2)) if pts2 else None
        if c1 is None and c2 is None:
            return
        if c1 is not None and c2 is not None:
            dx, dy = c2[0] - c1[0], c2[1] - c1[1]
            ux1, uy1 = c1[0] - cx, c1[1] - cy
            ux2, uy2 = c2[0] - cx, c2[1] - cy
            # If the two peers straddle the component horizontally (one to the left,
            # one to the right), signal flows through horizontally.
            if ux1 * ux2 < 0 and _can_be_vertical(comp) and abs(dy) > 2.0 * abs(dx):
                orient_two_pin(comp, p1, Side.TOP if dy > 0 else Side.BOTTOM)
                return
            if ux1 * ux2 < 0:
                orient_two_pin(comp, p1, Side.LEFT if dx >= 0 else Side.RIGHT)
                return
            # If the two peers straddle vertically (one above, one below):
            if uy1 * uy2 < 0 and _can_be_vertical(comp):
                orient_two_pin(comp, p1, Side.TOP if dy > 0 else Side.BOTTOM)
                return
        elif c1 is not None:
            dx, dy = cx - c1[0], cy - c1[1]
        else:
            dx, dy = c2[0] - cx, c2[1] - cy
        if abs(dy) > 1.5 * abs(dx) and _can_be_vertical(comp):
            orient_two_pin(comp, p1, Side.TOP if dy > 0 else Side.BOTTOM)
        else:
            orient_two_pin(comp, p1, Side.LEFT if dx >= 0 else Side.RIGHT)

    def _configure_satellite(self, sat: Satellite) -> None:
        self._fp_cache.clear()
        comp = self.comps[sat.comp]
        near, far = comp.pins[sat.near_pin], comp.pins[sat.far_pin]
        anchor = self.comps[sat.anchor]
        apin = anchor.pins[sat.anchor_pin]
        if not (near.side_specified or far.side_specified):
            if _can_be_vertical(comp) and comp.rotatable:
                orient_two_pin(comp, near, Side.BOTTOM if sat.above else Side.TOP)
            else:
                orient_two_pin(comp, near, Side.LEFT if apin.side != Side.LEFT else Side.RIGHT)
        comp.x, comp.y = 0.0, 0.0
        size_and_place_pins(comp)
        # Geometry relative to the anchor's top-left corner.
        ax, ay = apin.position.x - anchor.x, apin.position.y - anchor.y
        out = {Side.LEFT: (-1, 0), Side.RIGHT: (1, 0), Side.TOP: (0, -1), Side.BOTTOM: (0, 1)}[apin.side]
        dist = sat.dist
        if out[1] == 0:
            wire_x = ax + out[0] * dist
            npx, npy = near.position.x - comp.x, near.position.y - comp.y
            if near.side in (Side.TOP, Side.BOTTOM):
                siblings = [
                    s for s in self.sats.values()
                    if s.anchor == sat.anchor and s.anchor_pin == sat.anchor_pin and s.above == sat.above
                ]
                min_h = min(self.comps[s.comp].height for s in siblings)
                max_h = max(self.comps[s.comp].height for s in siblings)
                target_far_dist = max(40.0 + min_h, 20.0 + max_h)
                stub = target_far_dist - comp.height
                pin_y = ay - stub if sat.above else ay + stub
                sat.rel = (snap(wire_x - npx), snap(pin_y - npy))
            else:
                pin_y = ay - 60.0 if sat.above else ay + 60.0
                sat.rel = (snap(wire_x - npx), snap(pin_y - npy))
        else:
            wire_y = ay + out[1] * dist
            npx, npy = near.position.x - comp.x, near.position.y - comp.y
            sat.rel = (snap(ax + 60.0 - npx), snap(wire_y - npy))

    # ==================================================== orientation variants
    def orientation_variants(self) -> List[Tuple[str, bool]]:
        """Parts whose alternative axis is worth testing, with that axis.

        Expects a placed schematic.  For free 2-pin standard parts the axis is
        the symbol orientation (``True`` = vertical); for every other
        rotatable part ``True`` means 90° clockwise and ``False`` 90°
        counter-clockwise.  The selection is based on peer directions only;
        the engine places, routes and scores every returned variant like any
        other candidate, so this heuristic never has the final word.  The most
        promising parts come first.
        """
        if self.cfg.orientation_candidates <= 0 and self.cfg.rotation_candidates <= 0:
            return []
        self._resolve_connections()
        self._classify()
        positions = {cid: (c.x, c.y) for cid, c in self.comps.items()}
        for cid, sat in self.sats.items():
            ax, ay = positions.get(sat.anchor, (0.0, 0.0))
            sat.rel = (self.comps[cid].x - ax, self.comps[cid].y - ay)
        hubs: Dict[str, str] = {}
        for g in self._groups():
            for cid in g.comps:
                hubs[cid] = g.hub
        scored = []
        rotations = []
        for cid, comp in self.comps.items():
            if cid in self.sats or cid in self.isolated:
                continue
            if not comp.rotatable:
                continue
            if comp.image:
                if self.cfg.rotation_candidates > 0:
                    gain_cw, connected = self._rotation_gain(cid, positions, clockwise=True)
                    gain_ccw, _ = self._rotation_gain(cid, positions, clockwise=False)
                    clockwise = gain_cw >= gain_ccw
                    if connected >= 2:
                        rotations.append((0, -max(gain_cw, gain_ccw), list(self.comps).index(cid),
                                          cid, clockwise, hubs.get(cid, cid)))
                continue
            if comp.is_standard and len(comp.pins) != 2:
                continue
            if comp.is_standard:
                if self.cfg.orientation_candidates <= 0:
                    continue
                if any(p.side_specified for p in comp.pins.values()) or not _can_be_vertical(comp):
                    continue
                self._hub = hubs.get(cid)
                if not self._macro_flip_ambiguous(cid, positions):
                    continue
                p1, p2 = list(comp.pins.values())
                vertical = p1.side in (Side.TOP, Side.BOTTOM) and p2.side in (Side.TOP, Side.BOTTOM)
                c1 = self._peer_centroid(cid, p1.id, positions)
                c2 = self._peer_centroid(cid, p2.id, positions)
                both = False
                ratio = 99.0
                if c1 is not None and c2 is not None:
                    cx, cy = comp.x + comp.width / 2.0, comp.y + comp.height / 2.0
                    both = (c1[0] - cx) * (c2[0] - cx) < 0 and (c1[1] - cy) * (c2[1] - cy) < 0
                    dx, dy = abs(c2[0] - c1[0]), abs(c2[1] - c1[1])
                    ratio = max(dx, dy) / max(1.0, min(dx, dy))
                scored.append((0 if both else 1, ratio, list(self.comps).index(cid),
                               cid, not vertical, hubs.get(cid, cid)))
            elif self.cfg.rotation_candidates > 0:
                gain_cw, connected = self._rotation_gain(cid, positions, clockwise=True)
                gain_ccw, _ = self._rotation_gain(cid, positions, clockwise=False)
                clockwise = gain_cw >= gain_ccw
                gain = max(gain_cw, gain_ccw)
                if connected >= 2 and gain > 0.0:
                    rotations.append((1, -gain, list(self.comps).index(cid), cid, clockwise,
                                      hubs.get(cid, cid)))
        scored.sort(key=lambda e: e[:3])
        rotations.sort(key=lambda e: e[:3])

        def spread(entries, limit):
            selected = []
            seen_groups = set()
            for entry in entries:
                if entry[-1] not in seen_groups and len(selected) < limit:
                    selected.append(entry)
                    seen_groups.add(entry[-1])
            for entry in entries:
                if entry not in selected and len(selected) < limit:
                    selected.append(entry)
            return selected

        selected = spread(scored, self.cfg.orientation_candidates)
        variants = [(entry[3], entry[4]) for entry in selected]
        rotations = spread(rotations, self.cfg.rotation_candidates)
        variants.extend((entry[3], entry[4]) for entry in rotations)
        return variants

    def _rotation_gain(self, cid: str, positions: Dict[str, Pt], clockwise: bool) -> Tuple[float, int]:
        """How much a rotation would reduce pins facing away from their peers.

        Returns the improvement and the number of connected pins considered.
        A positive value means the rotated part would face its wiring better.
        """
        comp = self.comps[cid]
        cx, cy = comp.x + comp.width / 2.0, comp.y + comp.height / 2.0
        gain = 0.0
        connected = 0
        for pin in comp.pins.values():
            pts = self._peer_points(cid, pin.id, positions)
            if not pts:
                continue
            connected += 1
            mx = sum(p[0] for p in pts) / len(pts) - cx
            my = sum(p[1] for p in pts) / len(pts) - cy
            length = math.hypot(mx, my)
            if length < 1e-6:
                continue
            dx, dy = OUTWARD[pin.side]
            before = (dx * mx + dy * my) / length
            rdx, rdy = (-dy, dx) if clockwise else (dy, -dx)
            after = (rdx * mx + rdy * my) / length
            gain -= min(0.0, before)
            gain += min(0.0, after)
        return gain, connected

    def _macro_flip_ambiguous(self, cid: str, positions: Dict[str, Pt]) -> bool:
        """True when both axes are plausible from the two peer directions.

        Used by the engine to decide which 2-pin parts get an alternative
        orientation routed as a candidate; the real score, not this test,
        makes the final choice."""
        comp = self.comps[cid]
        p1, p2 = list(comp.pins.values())
        c1 = self._peer_centroid(cid, p1.id, positions)
        c2 = self._peer_centroid(cid, p2.id, positions)
        if c1 is None or c2 is None:
            return False
        px, py = positions[cid]
        cx, cy = px + comp.width / 2.0, py + comp.height / 2.0
        dx, dy = c2[0] - c1[0], c2[1] - c1[1]
        ux1, uy1 = c1[0] - cx, c1[1] - cy
        ux2, uy2 = c2[0] - cx, c2[1] - cy
        if ux1 * ux2 < 0 and uy1 * uy2 < 0:
            return True
        if abs(dx) > 0 and abs(dy) > 0:
            return max(abs(dx), abs(dy)) < 2.0 * min(abs(dx), abs(dy))
        return False

    def _peer_centroid(self, cid: str, pid: str, positions: Dict[str, Pt]) -> Optional[Pt]:
        pts = self._peer_points(cid, pid, positions)
        if not pts:
            return None
        return (sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts))

    def _apply_orientation_overrides(self) -> None:
        """Pins an already chosen axis/orientation for a routed candidate.

        Used by the engine's orientation candidates: the normal heuristic must
        not overwrite the axis that the candidate is meant to test.  Declared
        pin sides always win, and satellites keep their conventional axis.
        Standard 2-pin parts only change their symbol axis; every other part
        is rotated physically (90° clockwise for ``True``, counter-clockwise
        for ``False``) and its geometry recomputed.
        """
        self._forced_orientations.clear()
        for cid, alternative in self.orientation_overrides.items():
            comp = self.comps.get(cid)
            if comp is None or cid in self.sats or not comp.rotatable:
                continue
            if comp.image:
                rotate_component_90(comp, clockwise=alternative)
                size_and_place_pins(comp)
            elif comp.is_standard:
                if len(comp.pins) != 2:
                    continue
                if any(p.side_specified for p in comp.pins.values()):
                    continue
                self._forced_orientations[cid] = alternative
            else:
                rotate_component_90(comp, clockwise=alternative)
                size_and_place_pins(comp)

    def _force_axis(self, comp: Component, vertical: bool) -> None:
        p1, p2 = list(comp.pins.values())
        if vertical:
            new1 = Side.TOP if p1.side in (Side.LEFT, Side.TOP) else Side.BOTTOM
        else:
            new1 = Side.LEFT if p1.side in (Side.LEFT, Side.TOP) else Side.RIGHT
        p1.side, p2.side = new1, OPPOSITE[new1]
        p1.order = p2.order = 0

    def _resolve_equivalent(self, comps: List[str], positions: Dict[str, Pt]) -> None:
        members = set(comps) | {s for s, sat in self.sats.items() if sat.anchor in comps}
        for net in self.sch.nets:
            for conn in net.connections:
                if conn.component_id not in members:
                    continue
                comp = self.comps[conn.component_id]
                matching = comp.get_pins_by_name(conn.pin_name)
                if len(matching) <= 1:
                    continue
                peers: List[Pt] = []
                peer_ids = [p for p in conn.peer_component_ids if p != comp.id] or [
                    c.component_id for c in net.connections if c.component_id != comp.id
                ]
                for pid in peer_ids:
                    owner = self._owner(pid) if pid in self.comps else None
                    if owner in positions:
                        pc = self.comps[pid]
                        ox, oy = positions[owner]
                        peers.append((ox + pc.width / 2.0, oy + pc.height / 2.0))
                if not peers:
                    continue
                base = positions.get(comp.id, (comp.x, comp.y))
                best = min(
                    matching,
                    key=lambda p: (
                        min(abs(base[0] + p.position.x - comp.x - q[0]) + abs(base[1] + p.position.y - comp.y - q[1])
                            for q in peers),
                        p.id,
                    ),
                )
                conn.pin_id = best.id
                self.conn_pin[id(conn)] = best.id

    # ========================================================= legalization
    def _footprint(self, cid: str) -> Rect:
        """Body + pin leads + attached satellites, relative to the top-left."""
        cached = self._fp_cache.get(cid)
        if cached is not None:
            return cached
        fp = self._compute_footprint(cid)
        self._fp_cache[cid] = fp
        return fp

    def _compute_footprint(self, cid: str) -> Rect:
        comp = self.comps[cid]
        x1, y1, x2, y2 = 0.0, 0.0, comp.width, comp.height
        for p in comp.pins.values():
            px, py = p.position.x - comp.x, p.position.y - comp.y
            x1, y1, x2, y2 = min(x1, px), min(y1, py), max(x2, px), max(y2, py)
        for sat in self.sats.values():
            if sat.anchor == cid:
                s = self.comps[sat.comp]
                sx, sy = sat.rel
                x1, y1 = min(x1, sx - 20.0), min(y1, sy)
                x2 = max(x2, sx + s.width + 20.0 + label_width(s))
                y2 = max(y2, sy + s.height)
        return x1, y1, x2, y2

    def _abs_fp(self, cid: str, pos: Pt) -> Rect:
        f = self._footprint(cid)
        return pos[0] + f[0], pos[1] + f[1], pos[0] + f[2], pos[1] + f[3]

    def _gaps(self, a: str, b: str) -> Tuple[float, float]:
        """Required (horizontal, vertical) clearance between two footprints.

        Footprints include the pin terminals, so the horizontal gap is the
        terminal-to-terminal corridor width: two escape columns plus free
        tracks for every wire that may have to jog between the two parts."""
        sp = self.cfg.spacing
        key = (a, b) if a < b else (b, a)
        n = self.pair_nets.get(key, 0)
        hg = sp.min_h_gap + sp.track_gap * min(6, max(0, n - 2))
        return hg, sp.min_v_gap

    def _conflict(self, fa: Rect, fb: Rect, gaps: Tuple[float, float] = None, connected: bool = False) -> bool:
        """True when two footprints are too close.

        Parts that are wired to each other must either be stacked (their x
        ranges overlap, wires run beside them) or keep a full corridor
        between them: a diagonal arrangement with a narrow horizontal gap
        leaves no tracks for the jogs between their facing pins."""
        hg, vg = gaps if gaps is not None else (self.cfg.spacing.min_h_gap, self.cfg.spacing.min_v_gap)
        if fa[2] + hg <= fb[0] or fb[2] + hg <= fa[0]:
            return False
        if fa[3] + vg <= fb[1] or fb[3] + vg <= fa[1]:
            if not connected:
                return False
            return not (min(fa[2], fb[2]) - max(fa[0], fb[0]) > 0)
        return True

    def _legalize(self, g: _Group) -> None:
        comps = g.comps
        for _ in range(300):
            changed = False
            cx = sum(g.pos[c][0] for c in comps) / len(comps)
            cy = sum(g.pos[c][1] for c in comps) / len(comps)
            for i, a in enumerate(comps):
                for b in comps[i + 1:]:
                    fa, fb = self._abs_fp(a, g.pos[a]), self._abs_fp(b, g.pos[b])
                    hg, vg = self._gaps(a, b)
                    if not self._conflict(fa, fb, (hg, vg), self._connected(a, b)):
                        continue
                    # Move the component that is further from the group centre.
                    da = abs(fa[0] + fa[2] - 2 * cx) + abs(fa[1] + fa[3] - 2 * cy)
                    db = abs(fb[0] + fb[2] - 2 * cx) + abs(fb[1] + fb[3] - 2 * cy)
                    mover, fixed, fm, ff = (b, a, fb, fa) if db >= da else (a, b, fa, fb)
                    options = [
                        (ff[2] + hg - fm[0], 0.0),
                        (-(fm[2] + hg - ff[0]), 0.0),
                        (0.0, ff[3] + vg - fm[1]),
                        (0.0, -(fm[3] + vg - ff[1])),
                    ]
                    mcx, mcy = (fm[0] + fm[2]) / 2.0, (fm[1] + fm[3]) / 2.0
                    fcx, fcy = (ff[0] + ff[2]) / 2.0, (ff[1] + ff[3]) / 2.0

                    def cost(o: Pt) -> float:
                        # Prefer the cheapest push that moves away from the fixed part.
                        away = (o[0] * (mcx - fcx) + o[1] * (mcy - fcy)) >= 0
                        return abs(o[0]) + abs(o[1]) + (0.0 if away else 1e4)

                    dx, dy = min(options, key=lambda o: (cost(o), o))
                    px, py = g.pos[mover]
                    g.pos[mover] = (snap(px + _ceil_grid(dx)), snap(py + _ceil_grid(dy)))
                    changed = True
            if not changed:
                break

    def _legal(self, g: _Group, cid: str, pos: Pt, ignore: Tuple[str, ...] = ()) -> bool:
        fa = self._abs_fp(cid, pos)
        for other in g.comps:
            if other == cid or other in ignore:
                continue
            if self._conflict(fa, self._abs_fp(other, g.pos[other]), self._gaps(cid, other),
                              self._connected(cid, other)):
                return False
        return True

    def _connected(self, a: str, b: str) -> bool:
        return self.pair_nets.get((a, b) if a < b else (b, a), 0) > 0

    # ========================================================= local search

    # =============================================================== packing
    def _group_bbox(self, g: _Group) -> Rect:
        rects = [self._abs_fp(c, g.pos[c]) for c in g.comps]
        return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))

    def _pack(self, groups: List[_Group]) -> None:
        if not groups:
            return
        gap = self.cfg.spacing.group_gap
        boxes = [self._group_bbox(g) for g in groups]
        total = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        row_limit = max(boxes[0][2] - boxes[0][0], math.sqrt(total) * 1.6)
        x, y, row_h = 0.0, 0.0, 0.0
        for g, b in zip(groups, boxes):
            w, h = b[2] - b[0], b[3] - b[1]
            if x > 0 and x + w > row_limit:
                x, y, row_h = 0.0, y + row_h + gap, 0.0
            dx, dy = snap(x - b[0]), snap(y - b[1])
            for c in g.comps:
                px, py = g.pos[c]
                self.pos[c] = (px + dx, py + dy)
            x += w + gap
            row_h = max(row_h, h)

    def _place_isolated(self) -> None:
        if not self.isolated:
            return
        rects = [self._abs_fp(c, self.pos[c]) for c in self.pos]
        y0 = (max(r[3] for r in rects) + self.cfg.spacing.group_gap) if rects else 0.0
        x0 = min((r[0] for r in rects), default=0.0)
        x = x0
        for cid in self.isolated:
            comp = self.comps[cid]
            size_and_place_pins(comp)
            f = self._footprint(cid)
            self.pos[cid] = (snap(x - f[0]), snap(y0 - f[1]))
            x += (f[2] - f[0]) + self.cfg.spacing.isolated_gap

    # ============================================================== finalize
    def _apply_kept(self, groups: List[_Group]) -> None:
        """Writes configured positions back without re-packing or margins.

        Used by keep-position refinement, where the drawing must stay exactly
        where it is and only the requested local change may move parts.
        """
        for g in groups:
            for cid in g.comps:
                comp = self.comps[cid]
                comp.x, comp.y = g.pos[cid]
                size_and_place_pins(comp)
            for cid, sat in self.sats.items():
                if sat.anchor in g.comps:
                    ax, ay = g.pos[sat.anchor]
                    comp = self.comps[cid]
                    comp.x, comp.y = snap(ax + sat.rel[0]), snap(ay + sat.rel[1])
                    size_and_place_pins(comp)

    def _apply(self) -> None:
        for sat in self.sats.values():
            ax, ay = self.pos[sat.anchor]
            self.pos[sat.comp] = (ax + sat.rel[0], ay + sat.rel[1])
        fps = [self._abs_fp(c, self.pos[c]) if c not in self.sats else
               (self.pos[c][0] - 20.0, self.pos[c][1] - 20.0,
                self.pos[c][0] + self.comps[c].width + 20.0, self.pos[c][1] + self.comps[c].height + 20.0)
               for c in self.pos]
        min_x = min(f[0] for f in fps)
        min_y = min(f[1] for f in fps)
        margin = self.cfg.spacing.margin
        dx, dy = snap(margin - min_x), snap(margin - min_y)
        for cid, (x, y) in self.pos.items():
            comp = self.comps[cid]
            comp.x, comp.y = snap(x + dx), snap(y + dy)
            size_and_place_pins(comp)
        all_pos = {c: (self.comps[c].x, self.comps[c].y) for c in self.pos}
        self._resolve_equivalent(list(self.comps), all_pos)

    def _finalize_bounds(self) -> None:
        comps = list(self.comps.values())
        max_x = max(max(c.x + c.width, *(p.position.x for p in c.pins.values())) if c.pins else c.x + c.width
                    for c in comps)
        max_y = max(max(c.y + c.height, *(p.position.y for p in c.pins.values())) if c.pins else c.y + c.height
                    for c in comps)
        self.sch.width = max_x + self.cfg.spacing.margin
        self.sch.height = max_y + self.cfg.spacing.margin


def _ceil_grid(v: float) -> float:
    if v >= 0:
        return math.ceil(v / GRID_SIZE - 1e-9) * GRID_SIZE
    return -math.ceil(-v / GRID_SIZE - 1e-9) * GRID_SIZE


def label_width(comp: Component) -> float:
    """Width of the upright name / value text drawn beside a standard part."""
    return max(len(comp.id) * 6.5, len(comp.value) * 5.6) + 14.0


def _can_be_vertical(comp: Component) -> bool:
    return comp.kind in ("resistor", "capacitor", "inductor", "diode", "led", "fuse")


def place_schematic(schematic: Schematic, config: Optional[LayoutConfig] = None,
                    semantics: Optional[Semantics] = None) -> None:
    """Places all components (sizes, pin sides and coordinates)."""
    from schematic.layout.normalize import merge_shared_pin_nets

    if merge_shared_pin_nets(schematic):
        semantics = None
    Placer(schematic, semantics=semantics, config=config).place()
