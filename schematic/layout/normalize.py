"""Input normalisation before layout.

Two connection lines can mention the same pin under different net names, e.g.
``ESP32.3V3 -> R1.1`` (auto-named "3V3") and ``Fuse.OUT -> ESP32.3V3``
(auto-named "Fuse_OUT").  Electrically that is a single node, and drawing it
as two independently routed nets would show two wires fighting over one pin.
Such nets are merged; a user-given name wins over a generated one.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from schematic.models import Net, Schematic


def merge_shared_pin_nets(schematic: Schematic) -> Dict[str, str]:
    """Merges nets sharing a pin in place; returns ``{old_name: new_name}``."""
    nets = schematic.nets
    parent = list(range(len(nets)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: Dict[Tuple[str, str], int] = {}
    for i, net in enumerate(nets):
        for conn in net.connections:
            key = (conn.component_id, conn.pin_name.upper())
            if key in owner:
                a, b = find(owner[key]), find(i)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[key] = i

    groups: Dict[int, List[int]] = {}
    for i in range(len(nets)):
        groups.setdefault(find(i), []).append(i)
    if all(len(g) == 1 for g in groups.values()):
        return {}

    renamed: Dict[str, str] = {}
    merged: List[Net] = []
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
