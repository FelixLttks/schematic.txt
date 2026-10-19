"""Structural analysis of the netlist.

Placement, routing and metrics need to know the *shape* of the circuit: which
nets are high-fanout distribution nets, which parts are small two-terminal
symbols hanging between a distribution net and a signal, which nets travel
together between the same parts.

Everything here is derived from connectivity and component geometry only.
Net names, pin names and component names are never inspected (the only place
that reads a net name is ``styles.net_color``, which merely picks a colour).

Everything is deterministic: iteration always happens over sorted keys or
insertion-ordered containers, never over raw ``set`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from schematic.models import Net, Schematic

# A net touching at least this many "module" parts is a distribution net
# (rail): it is drawn with relaxed straightness / weight instead of as a
# point-to-point connection.
RAIL_MIN_MODULES = 4


@dataclass
class NetInfo:
    name: str
    components: Tuple[str, ...]          # distinct component ids, sorted
    terminal_count: int                  # distinct (component, pin) terminals
    is_rail: bool                        # distribution net: spans >= RAIL_MIN_MODULES modules
    bundle: Optional[str] = None         # routing bundle id (nets that travel together)

    @property
    def is_power_like(self) -> bool:
        """Structural 'distribution net' flag (kept for the callers' readability)."""
        return self.is_rail


@dataclass
class Bundle:
    id: str
    kind: str                            # "bus" | "parallel"
    nets: List[str] = field(default_factory=list)
    components: Tuple[str, ...] = ()


@dataclass
class Semantics:
    nets: Dict[str, NetInfo] = field(default_factory=dict)
    bundles: Dict[str, Bundle] = field(default_factory=dict)
    component_roles: Dict[str, str] = field(default_factory=dict)
    hubs: List[str] = field(default_factory=list)

    def info(self, net_name: str) -> Optional[NetInfo]:
        return self.nets.get(net_name)

    def related(self, a: str, b: str) -> bool:
        """True when two nets belong together visually (same bundle / shared rails)."""
        if a == b:
            return True
        ia, ib = self.nets.get(a), self.nets.get(b)
        if ia is None or ib is None:
            return False
        if ia.bundle and ia.bundle == ib.bundle:
            return True
        return ia.is_rail and ib.is_rail and len(set(ia.components) & set(ib.components)) >= 2

    def bundle_of(self, net_name: str) -> Optional[Bundle]:
        info = self.nets.get(net_name)
        if info and info.bundle:
            return self.bundles.get(info.bundle)
        return None


def net_terminals(net: Net) -> List[Tuple[str, str]]:
    seen: Dict[Tuple[str, str], None] = {}
    for c in net.connections:
        seen.setdefault((c.component_id, (c.pin_id or c.pin_name).upper()), None)
    return list(seen)


def _is_small_symbol(comp) -> bool:
    """Small fixed-size symbol (resistor-like, <= 3 pins) as opposed to a box part."""
    return bool(comp.is_standard and len(comp.pins) <= 3)


def analyze(schematic: Schematic) -> Semantics:
    """Derives distribution nets, bundles, component roles and hubs."""
    sem = Semantics()
    comps = schematic.components
    small = {cid for cid, c in comps.items() if _is_small_symbol(c)}

    for net in schematic.nets:
        terms = net_terminals(net)
        comp_ids = tuple(sorted({cid for cid, _ in terms}))
        modules = sum(1 for c in comp_ids if c not in small)
        sem.nets[net.name] = NetInfo(
            name=net.name,
            components=comp_ids,
            terminal_count=len(terms),
            is_rail=modules >= RAIL_MIN_MODULES,
        )

    # Component roles from topology and size only.
    #   module:     box part (anything that is not a small symbol)
    #   shunt:      two-terminal symbol between one distribution net and one other net
    #   decoupling: two-terminal symbol between two distribution nets
    #   series:     any other small symbol (lies in the path of a connection)
    for cid in sorted(comps):
        comp = comps[cid]
        nets_here = [n for n in schematic.nets if any(c.component_id == cid for c in n.connections)]
        if not nets_here:
            sem.component_roles[cid] = "isolated"
        elif cid not in small:
            sem.component_roles[cid] = "module"
        elif len(comp.pins) == 2 and len(nets_here) == 2:
            rails = sum(1 for n in nets_here if sem.nets[n.name].is_rail)
            sem.component_roles[cid] = {1: "shunt", 2: "decoupling"}.get(rails, "series")
        else:
            sem.component_roles[cid] = "series"

    # Routing bundles: non-rail nets that connect exactly the same set of
    # box parts travel together.
    def macro_set(info: NetInfo) -> Tuple[str, ...]:
        macro = tuple(c for c in info.components if sem.component_roles.get(c) == "module")
        return macro if len(macro) >= 2 else info.components

    groups: Dict[Tuple[str, ...], List[str]] = {}
    for name, info in sem.nets.items():
        if info.is_rail or len(info.components) < 2:
            continue
        groups.setdefault(macro_set(info), []).append(name)
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        bundle_id = "B:" + "+".join(key)
        kind = "bus" if any(sem.nets[m].terminal_count >= 3 for m in members) else "parallel"
        sem.bundles[bundle_id] = Bundle(id=bundle_id, kind=kind, nets=list(members), components=key)
        for m in members:
            sem.nets[m].bundle = bundle_id

    # Hubs: box parts with the highest signal connectivity.
    degree: Dict[str, float] = {}
    for info in sem.nets.values():
        if info.is_rail:
            continue
        for cid in info.components:
            degree[cid] = degree.get(cid, 0.0) + 1.0
    if degree:
        top = max(degree.values())
        sem.hubs = sorted(
            (c for c, d in degree.items() if d >= max(3.0, 0.6 * top) and sem.component_roles.get(c) == "module"),
            key=lambda c: (-degree[c], c),
        )
    return sem
