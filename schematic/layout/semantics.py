"""Semantic classification of nets and components.

Placement, routing, metrics and rendering all need to know what a net *means*
(ground, power rail, ordinary signal, member of a bus, half of a differential
pair) and what role a component plays (hub, passive pull-up, series element).
This module is the single place where those name- and topology-based
heuristics live, so the rest of the engine never re-derives them.

Everything here is deterministic: iteration always happens over sorted keys or
insertion-ordered containers, never over raw ``set`` objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from schematic.models import Net, Schematic


class NetClass(str, Enum):
    GROUND = "ground"
    POWER = "power"
    SIGNAL = "signal"


_TOKEN_SPLIT = re.compile(r"[\s_\-\.\/\(\)\[\]:]+")
_VOLTAGE_TOKEN = re.compile(r"^\+?\d+(\.\d+)?V\d*$|^\d+V\d+$|^V\d+(V\d+)?$")

GROUND_TOKENS = {
    "GND", "GROUND", "VSS", "AGND", "DGND", "PGND", "SGND", "CGND", "GNDA", "GNDD",
    "EARTH", "0V", "NEG", "VSSA", "VSSD",
}
POWER_TOKENS = {
    "VCC", "VDD", "VIN", "VBUS", "VBAT", "VBATT", "VSYS", "VOUT", "VIO", "VDDIO",
    "AVDD", "DVDD", "AVCC", "DVCC", "VCCIO", "VDDA", "VDDD", "VREF", "VMOT", "VM",
    "PWR", "POWER", "BAT", "BATT", "BATTERY", "SUPPLY", "RAIL", "V+", "POS", "VPP",
}
# Pin names used when a net was auto-named and carries no meaning of its own.
_AUTO_NAME = re.compile(r"^[A-Za-z0-9\-]+_[A-Za-z0-9\-\+]+$")

# Complementary suffix pairs marking the two halves of a differential pair.
_DIFF_SUFFIXES: Tuple[Tuple[str, str], ...] = (
    ("P", "N"), ("+", "-"), ("H", "L"), ("DP", "DM"), ("DP", "DN"),
    ("POS", "NEG"), ("PLUS", "MINUS"), ("A", "B"),
)


SIGNAL_QUALIFIER_TOKENS = {
    "SW", "SWITCHED", "DRAIN", "GATE", "PWM", "CTRL", "SENSE", "SHUNT", "SIG", "SIGNAL",
}


def tokens_of(name: str) -> List[str]:
    return [t for t in _TOKEN_SPLIT.split(name.upper()) if t]


def classify_name(name: str) -> Optional[NetClass]:
    """Classifies a net or pin name, or returns None when the name is neutral."""
    toks = tokens_of(name)
    upper = name.upper().strip()
    if any(t in SIGNAL_QUALIFIER_TOKENS for t in toks):
        return None
    if upper in ("V-", "-"):
        return NetClass.GROUND
    if any(t in GROUND_TOKENS or t.endswith("GND") or t.startswith("GND") for t in toks):
        return NetClass.GROUND
    if upper in ("V+", "+"):
        return NetClass.POWER
    for t in toks:
        if t in POWER_TOKENS or _VOLTAGE_TOKEN.match(t):
            return NetClass.POWER
    return None


@dataclass
class NetInfo:
    name: str
    cls: NetClass
    components: Tuple[str, ...]          # distinct component ids, sorted
    terminal_count: int                  # distinct (component, pin) terminals
    is_rail: bool                        # power/ground net spanning >= 3 components
    family: Optional[str] = None         # name family, e.g. "I2C" for I2C_SDA/I2C_SCL
    bundle: Optional[str] = None         # routing bundle id (nets that travel together)
    diff_partner: Optional[str] = None   # other half of a differential pair

    @property
    def is_power_like(self) -> bool:
        return self.cls in (NetClass.POWER, NetClass.GROUND)


@dataclass
class Bundle:
    id: str
    kind: str                            # "diff" | "bus" | "parallel"
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
        """True when two nets belong together visually (bundle, family or pair)."""
        if a == b:
            return True
        ia, ib = self.nets.get(a), self.nets.get(b)
        if ia is None or ib is None:
            return False
        if ia.bundle and ia.bundle == ib.bundle:
            return True
        if ia.family and ia.family == ib.family:
            return True
        if ia.is_rail and ib.is_rail and len(set(ia.components) & set(ib.components)) >= 2:
            return True
        return ia.diff_partner == b

    def bundle_of(self, net_name: str) -> Optional[Bundle]:
        info = self.nets.get(net_name)
        if info and info.bundle:
            return self.bundles.get(info.bundle)
        return None


def _split_family(name: str) -> Tuple[str, str]:
    """Splits a net name into (family prefix, member suffix)."""
    upper = name.upper()
    m = re.match(r"^(.*?)[_\-\.](\w+)$", upper)
    if m and m.group(1):
        return m.group(1), m.group(2)
    m = re.match(r"^(.*?[A-Z])(\d+)$", upper)
    if m:
        return m.group(1), m.group(2)
    return upper, ""


def diff_pair_base(name: str) -> Optional[Tuple[str, str]]:
    """Returns (base, polarity-suffix) when the name ends in a diff-pair suffix."""
    upper = name.upper()
    for pos, neg in _DIFF_SUFFIXES:
        for suffix in (pos, neg):
            for sep in ("_", "-", ""):
                tail = sep + suffix
                if upper.endswith(tail) and len(upper) > len(tail):
                    base = upper[: -len(tail)]
                    if sep == "" and suffix in ("A", "B", "P", "N", "H", "L") and not base[-1:].isalpha():
                        continue
                    return base, suffix
    return None


def _are_diff_pair(a: str, b: str) -> bool:
    ba, bb = diff_pair_base(a), diff_pair_base(b)
    if not ba or not bb or ba[0] != bb[0]:
        return False
    pair = {ba[1], bb[1]}
    return any(pair == {p, n} for p, n in _DIFF_SUFFIXES)


def net_terminals(net: Net) -> List[Tuple[str, str]]:
    seen: Dict[Tuple[str, str], None] = {}
    for c in net.connections:
        seen.setdefault((c.component_id, (c.pin_id or c.pin_name).upper()), None)
    return list(seen)


def _classify_net(net: Net, schematic: Schematic) -> NetClass:
    by_name = classify_name(net.name)
    if by_name is not None and not getattr(net, "auto_named", False):
        return by_name
    # Auto-named or neutral: vote over the pin names / labels on the net.
    votes: Dict[NetClass, int] = {}
    for c in net.connections:
        comp = schematic.components.get(c.component_id)
        pin = comp.get_pin(c.pin_id or c.pin_name) if comp else None
        names = [c.pin_name] + ([pin.label] if pin and pin.label else [])
        cls = None
        for nm in names:
            cls = classify_name(nm)
            if cls:
                break
        if cls:
            votes[cls] = votes.get(cls, 0) + 1
    if by_name is not None:
        return by_name
    if votes:
        best = max(sorted(votes, key=lambda k: k.value), key=lambda k: votes[k])
        if votes[best] * 2 > len(net.connections):
            return best
    return NetClass.SIGNAL


def analyze(schematic: Schematic) -> Semantics:
    """Derives net classes, bundles, families and component roles."""
    sem = Semantics()
    comps = schematic.components

    for net in schematic.nets:
        terms = net_terminals(net)
        comp_ids = tuple(sorted({cid for cid, _ in terms}))
        cls = _classify_net(net, schematic)
        sem.nets[net.name] = NetInfo(
            name=net.name,
            cls=cls,
            components=comp_ids,
            terminal_count=len(terms),
            is_rail=cls != NetClass.SIGNAL and len(comp_ids) >= 3,
        )

    # Component roles: passives are classified by the nets on their two pins.
    for cid in sorted(comps):
        comp = comps[cid]
        nets_here = [n for n in schematic.nets if any(c.component_id == cid for c in n.connections)]
        if not nets_here and not comp.pins:
            sem.component_roles[cid] = "isolated"
            continue
        if not nets_here:
            sem.component_roles[cid] = "isolated"
            continue
        if comp.is_standard and len(comp.pins) <= 3:
            # shunt:      one pin on a supply net, the other on a signal (pull-up, filter cap)
            # decoupling: between a supply and ground (bypass capacitor)
            # series:     in the path of a signal or of the power flow (fuse, series resistor)
            classes = [sem.nets[n.name] for n in nets_here]
            supply = [i for i in classes if i.is_power_like]
            signal = [i for i in classes if not i.is_power_like]
            if comp.kind in ("fuse", "inductor", "switch"):
                sem.component_roles[cid] = "series"
            elif len(comp.pins) == 2 and len(nets_here) == 2 and len(supply) == 1 and len(signal) == 1:
                sem.component_roles[cid] = "shunt"
            elif (
                comp.kind == "capacitor"
                and len(comp.pins) == 2 and len(nets_here) == 2 and len(supply) == 2
                and {i.cls for i in supply} == {NetClass.POWER, NetClass.GROUND}
            ):
                sem.component_roles[cid] = "decoupling"
            elif (
                comp.kind == "diode"
                and len(comp.pins) == 2 and len(nets_here) == 2 and len(supply) == 2
                and {i.cls for i in supply} == {NetClass.POWER, NetClass.GROUND}
            ):
                sem.component_roles[cid] = "shunt"
            else:
                sem.component_roles[cid] = "series"
        else:
            sem.component_roles[cid] = "module"

    # A power net is only a global distribution rail when it feeds >= 3 modules
    # (not when a 2-module power link merely passes through a fuse or bypass cap).
    total_modules = sum(1 for r in sem.component_roles.values() if r == "module")
    if total_modules >= 3:
        for info in sem.nets.values():
            if info.cls == NetClass.POWER and info.is_rail:
                modules = sum(1 for c in info.components if sem.component_roles.get(c) == "module")
                info.is_rail = modules >= 3

    # Name families (I2C_SDA + I2C_SCL -> "I2C"; DB0..DB7 -> "DB").
    families: Dict[str, List[str]] = {}
    for name, info in sem.nets.items():
        if info.is_rail:
            continue
        prefix, suffix = _split_family(name)
        if suffix:
            families.setdefault(prefix, []).append(name)
    for prefix in sorted(families):
        members = families[prefix]
        if len(members) >= 2:
            for m in members:
                sem.nets[m].family = prefix

    # Routing bundles: non-rail nets that connect exactly the same set of
    # non-passive components travel together (SPI lines, UART pairs, I2C bus).
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
        kind = "parallel"
        if len(members) == 2 and _are_diff_pair(members[0], members[1]):
            kind = "diff"
        elif any(sem.nets[m].terminal_count >= 3 for m in members):
            kind = "bus"
        sem.bundles[bundle_id] = Bundle(id=bundle_id, kind=kind, nets=list(members), components=key)
        for m in members:
            sem.nets[m].bundle = bundle_id

    # Differential pairs (also across bundles, e.g. CAN_H/CAN_L with a terminator).
    names = sorted(n for n, i in sem.nets.items() if not i.is_rail)
    for i, a in enumerate(names):
        if sem.nets[a].diff_partner:
            continue
        for b in names[i + 1:]:
            if sem.nets[b].diff_partner:
                continue
            if _are_diff_pair(a, b) and set(sem.nets[a].components) & set(sem.nets[b].components):
                sem.nets[a].diff_partner = b
                sem.nets[b].diff_partner = a
                break

    # Hubs: modules with the highest signal connectivity.
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
