"""Netlist preparation: normalisation and structure.

* Nets that share a pin are one electrical node and are merged (a
  user-given name wins over a generated one).
* Every connection is resolved to a concrete pin id.
* Each net gets a weight from its structure only: nets with many terminals
  (``rail_min_terminals``) are *distribution nets*.  They are still drawn as
  wires, but their bends and length weigh less than those of signals.

Names (of nets, pins or parts) are never interpreted here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from schematic_txt.layout.config import Weights
from schematic_txt.models import Net, Schematic

Terminal = tuple[str, str]  # (component id, pin id)


@dataclass
class NetInfo:
    name: str
    index: int
    terminals: list[Terminal]
    rail: bool
    factor: float  # weight of this net's bends / length
    parts: tuple[str, ...] = ()
    bundle: int = -1  # nets joining exactly the same parts travel together


@dataclass
class Netlist:
    nets: list[NetInfo] = field(default_factory=list)
    by_name: dict[str, NetInfo] = field(default_factory=dict)
    pin_net: dict[Terminal, int] = field(default_factory=dict)  # terminal -> net index
    part_nets: dict[str, list[int]] = field(default_factory=dict)  # component -> net indices
    neighbors: dict[str, dict[str, float]] = field(default_factory=dict)  # connection strength
    bundles: list[list[int]] = field(default_factory=list)  # net indices per bundle

    def groups(self, parts: list[str]) -> list[list[str]]:
        """Connected sub-circuits (components joined by any net), in input order."""
        seen: dict[str, int] = {}
        out: list[list[str]] = []
        for start in parts:
            if start in seen or not self.part_nets.get(start):
                continue
            stack, comp = [start], []
            seen[start] = len(out)
            while stack:
                c = stack.pop()
                comp.append(c)
                for n in self.part_nets.get(c, []):
                    for d, _ in self.nets[n].terminals:
                        if d not in seen:
                            seen[d] = len(out)
                            stack.append(d)
            order = {c: i for i, c in enumerate(parts)}
            out.append(sorted(comp, key=order.__getitem__))
        return out


def merge_shared_pin_nets(schematic: Schematic) -> dict[str, str]:
    """Merges nets sharing a pin in place; returns ``{old_name: new_name}``."""
    nets = schematic.nets
    parent = list(range(len(nets)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[tuple[str, str], int] = {}
    for i, net in enumerate(nets):
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            pin = comp.get_pin(conn.pin_id or conn.pin_name) if comp else None
            key = (conn.component_id, pin.id if pin else conn.pin_name.upper())
            if key in owner:
                a, b = find(owner[key]), find(i)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[key] = i

    groups: dict[int, list[int]] = {}
    for i in range(len(nets)):
        groups.setdefault(find(i), []).append(i)
    if all(len(g) == 1 for g in groups.values()):
        return {}
    renamed: dict[str, str] = {}
    merged: list[Net] = []
    for root in sorted(groups):
        members = [nets[i] for i in groups[root]]
        if len(members) == 1:
            merged.append(members[0])
            continue
        named = [n for n in members if not n.auto_named]
        keep = (named or members)[0]
        new = Net(name=keep.name, color=keep.color, is_bus=keep.is_bus, auto_named=not named)
        for n in members:
            for conn in n.connections:
                new.add_connection(conn.component_id, conn.pin_name, conn.pin_id, conn.peer_component_ids)
            renamed[n.name] = keep.name
        merged.append(new)
    schematic.nets[:] = merged
    return renamed


def build_netlist(schematic: Schematic, weights: Weights) -> Netlist:
    nl = Netlist()
    for comp_id in schematic.components:
        nl.part_nets[comp_id] = []
        nl.neighbors[comp_id] = {}
    for net in schematic.nets:
        terms: dict[Terminal, None] = {}
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            if comp is None:
                continue
            pin = comp.get_pin(conn.pin_id) if conn.pin_id else None
            pin = pin or comp.get_pin(conn.pin_name)
            if pin is None:
                continue
            conn.pin_id = pin.id
            terms.setdefault((comp.id, pin.id), None)
        terminals = list(terms)
        if len(terminals) < 2:
            continue
        rail = len(terminals) >= weights.rail_min_terminals
        info = NetInfo(
            name=net.name,
            index=len(nl.nets),
            terminals=terminals,
            rail=rail,
            factor=weights.rail_factor if rail else 1.0,
            parts=tuple(dict.fromkeys(c for c, _ in terminals)),
        )
        nl.nets.append(info)
        nl.by_name[net.name] = info
        for t in terminals:
            nl.pin_net[t] = info.index
        for c in info.parts:
            if info.index not in nl.part_nets[c]:
                nl.part_nets[c].append(info.index)
        # Connection strength between parts: a k-terminal net contributes
        # 1 / (k - 1) per pair (clique model of VLSI placement).
        strength = 1.0 / (len(terminals) - 1)
        for a in info.parts:
            for b in info.parts:
                if a != b:
                    nl.neighbors[a][b] = nl.neighbors[a].get(b, 0.0) + strength
    # Bundles: signal (non-distribution) nets that join exactly the same set
    # of parts -- a parallel interface, a bus, a differential pair.
    by_parts: dict[tuple[str, ...], list[int]] = {}
    for info in nl.nets:
        if not info.rail and len(info.parts) >= 2:
            by_parts.setdefault(tuple(sorted(info.parts, key=list(schematic.components).index)), []).append(
                info.index
            )
    for members in by_parts.values():
        if len(members) >= 2:
            for n in members:
                nl.nets[n].bundle = len(nl.bundles)
            nl.bundles.append(members)
    return nl
