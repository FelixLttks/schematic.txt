"""Quality report of a finished drawing.

Computed from the emitted geometry only (wires, junctions, component
rectangles, pins), independently of the router's internal state, so it also
verifies the router.  Wires are rasterised to unit grid edges, which makes
every check exact.

The *cost* uses the same weights as placement and routing; ``readability``
maps cost per connection onto 0..100 for humans (100 = every connection a
straight wire, no crossing).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from schematic_txt.layout.config import Weights
from schematic_txt.layout.netlist import Netlist, build_netlist
from schematic_txt.models import Schematic

G = 20.0
Node = tuple[int, int]


@dataclass
class QualityReport:
    components: int = 0
    nets: int = 0
    connections: int = 0
    wire_length: float = 0.0  # px
    bends: int = 0
    crossings: int = 0
    junctions: int = 0
    net_tags: int = 0  # pins carrying a net tag instead of a wire
    straight_connections: int = 0  # two-terminal nets drawn as one straight wire
    two_terminal_nets: int = 0
    max_bends_single_net: int = 0
    bundles: int = 0
    bundle_cohesion: float = 1.0  # share of bundle wire running directly beside a mate
    near_unrelated: int = 0  # grid steps directly beside a wire of an unrelated net
    width: float = 0.0
    height: float = 0.0
    cost: float = 0.0
    readability_score: float = 0.0
    violations: dict[str, int] = field(default_factory=dict)

    @property
    def violation_count(self) -> int:
        return sum(self.violations.values())

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["violation_count"] = self.violation_count
        return d

    def format_table(self) -> str:
        rows = [
            ("Readability score", f"{self.readability_score:.1f} / 100"),
            ("Cost (weighted)", f"{self.cost:.1f}"),
            ("Components / nets / connections", f"{self.components} / {self.nets} / {self.connections}"),
            ("Drawing size", f"{self.width:.0f} x {self.height:.0f} px"),
            ("Total wire length", f"{self.wire_length:.0f} px"),
            ("Bends", f"{self.bends} (max {self.max_bends_single_net} in one net)"),
            ("Crossings", str(self.crossings)),
            ("Junction dots", str(self.junctions)),
            ("Net tags", str(self.net_tags)),
            ("Straight 2-pin connections", f"{self.straight_connections} / {self.two_terminal_nets}"),
            ("Bundles / cohesion", f"{self.bundles} / {self.bundle_cohesion:.2f}"),
            ("Steps beside unrelated wires", str(self.near_unrelated)),
            ("Violations", ", ".join(f"{k}={v}" for k, v in self.violations.items() if v) or "none"),
        ]
        width = max(len(k) for k, _ in rows)
        return "\n".join(f"{k.ljust(width)}  {v}" for k, v in rows)


def _raster(sch: Schematic) -> dict[str, set[tuple[Node, Node]]]:
    edges: dict[str, set[tuple[Node, Node]]] = {}
    for w in sch.wires:
        x1, y1 = round(w.start.x / G), round(w.start.y / G)
        x2, y2 = round(w.end.x / G), round(w.end.y / G)
        es = edges.setdefault(w.net_name, set())
        if y1 == y2:
            for x in range(min(x1, x2), max(x1, x2)):
                es.add(((x, y1), (x + 1, y1)))
        elif x1 == x2:
            for y in range(min(y1, y2), max(y1, y2)):
                es.add(((x1, y), (x1, y + 1)))
    return edges


def evaluate(sch: Schematic, nl: Netlist | None = None, weights: Weights | None = None) -> QualityReport:
    """Scores any placed and routed schematic (also one produced by another
    engine version, see ``scripts/layout_report.py``)."""
    weights = weights or Weights()
    tagged: dict[str, set[tuple[str, str]]] = {}
    for tag in sch.net_tags:
        tagged.setdefault(tag.net_name, set()).add((tag.component_id, tag.pin_id))
    if nl is None:
        nl = build_netlist(sch, weights, symbols=set(tagged))
    r = QualityReport(components=len(sch.components), nets=len(nl.nets) + len(tagged))
    r.connections = sum(len(n.terminals) - 1 for n in nl.nets) + sum(len(t) - 1 for t in tagged.values())
    r.net_tags = sum(len(t) for t in tagged.values())
    viol = {
        "unconnected_terminals": 0,
        "shared_edges": 0,
        "ambiguous_contacts": 0,
        "wire_through_component": 0,
        "foreign_terminal_contact": 0,
        "component_overlaps": 0,
        "non_orthogonal": 0,
        "wire_through_tag": 0,
    }
    for w in sch.wires:
        if not (w.is_horizontal or w.is_vertical):
            viol["non_orthogonal"] += 1
    edges = _raster(sch)
    # node usage per net with axis mask (1 horizontal, 2 vertical) and degree
    use: dict[Node, dict[str, list[int]]] = {}
    for net, es in edges.items():
        for a, b in es:
            axis = 1 if a[1] == b[1] else 2
            for n in (a, b):
                m = use.setdefault(n, {}).setdefault(net, [0, 0])
                m[0] |= axis
                m[1] += 1
    # shared edges
    owner: dict[tuple[Node, Node], str] = {}
    for net, es in edges.items():
        for e in es:
            if e in owner and owner[e] != net:
                viol["shared_edges"] += 1
            owner[e] = net
    terminals: dict[Node, str] = {}
    for info in nl.nets:
        for c, p in info.terminals:
            pin = sch.components[c].pins[p]
            terminals[(round(pin.position.x / G), round(pin.position.y / G))] = info.name
    all_pins: set[Node] = set()
    for comp in sch.components.values():
        for pin in comp.pins.values():
            all_pins.add((round(pin.position.x / G), round(pin.position.y / G)))
    # crossings, ambiguous contacts, bends, junctions
    for n, nets in use.items():
        if len(nets) >= 2:
            ok = (
                len(nets) == 2
                and all(m[1] == 2 and m[0] in (1, 2) for m in nets.values())
                and {m[0] for m in nets.values()} == {1, 2}
                and n not in all_pins
            )
            if ok:
                r.crossings += 1
            else:
                viol["ambiguous_contacts"] += 1
        for mask, deg in nets.values():
            if deg == 2 and mask == 3:
                r.bends += 1
            if deg >= 3:
                r.junctions += 1
        if n in all_pins:
            for net in nets:
                if terminals.get(n) != net:
                    viol["foreign_terminal_contact"] += 1
    per_net_bends: dict[str, int] = {}
    for nets in use.values():
        for net, (mask, deg) in nets.items():
            if deg == 2 and mask == 3:
                per_net_bends[net] = per_net_bends.get(net, 0) + 1
    r.max_bends_single_net = max(per_net_bends.values(), default=0)
    # bodies
    bodies = []
    for comp in sch.components.values():
        x0, y0 = round(comp.x / G), round(comp.y / G)
        x1, y1 = round((comp.x + comp.width) / G), round((comp.y + comp.height) / G)
        bodies.append((x0, y0, x1, y1))
        for n in use:
            if x0 < n[0] < x1 and y0 < n[1] < y1:
                viol["wire_through_component"] += 1
    for i, a in enumerate(bodies):
        for b in bodies[i + 1 :]:
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                viol["component_overlaps"] += 1
    for tag in sch.net_tags:
        x0, y0, x1, y1 = (v / G for v in tag.box)
        for n in use:
            if x0 <= n[0] <= x1 and y0 <= n[1] <= y1:
                viol["wire_through_tag"] += 1
    # connectivity per net
    for info in nl.nets:
        es = edges.get(info.name, set())
        parent: dict[Node, Node] = {}

        def find(x: Node) -> Node:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in es:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        pts = []
        for c, p in info.terminals:
            pin = sch.components[c].pins[p]
            pts.append((round(pin.position.x / G), round(pin.position.y / G)))
        roots = {find(p) for p in pts}
        viol["unconnected_terminals"] += len(roots) - 1 if len(pts) > 1 else 0
        if len(info.terminals) == 2:
            r.two_terminal_nets += 1
            if per_net_bends.get(info.name, 0) == 0 and len(roots) == 1:
                r.straight_connections += 1
    r.wire_length = sum(len(es) for es in edges.values()) * G
    # company: bundle mates side by side, unrelated wires apart
    owner_of: dict[tuple[Node, Node], str] = {}
    for net, es in edges.items():
        for e in es:
            owner_of[e] = net
    bundle_of = {info.name: info.bundle for info in nl.nets}
    r.bundles = len(nl.bundles)
    beside = total = 0
    apart_steps = 0
    for net, es in edges.items():
        bun = bundle_of.get(net, -1)
        for a, b in es:
            if a[1] == b[1]:
                sides = [((a[0], a[1] + d), (b[0], b[1] + d)) for d in (-1, 1)]
            else:
                sides = [((a[0] + d, a[1]), (b[0] + d, b[1])) for d in (-1, 1)]
            mate = False
            for e in sides:
                o = owner_of.get(e)
                if o is None or o == net:
                    continue
                if bun >= 0 and bundle_of.get(o, -1) == bun:
                    mate = True
                else:
                    r.near_unrelated += 1
            if bun >= 0:
                total += 1
                beside += mate
                apart_steps += not mate
    r.bundle_cohesion = round(beside / total, 2) if total else 1.0
    # weighted cost: same weights as the engine
    cost = 0.0
    for info in nl.nets:
        es = edges.get(info.name, set())
        cost += (weights.length * len(es) + weights.bend * per_net_bends.get(info.name, 0)) * info.factor
    cost += weights.crossing * r.crossings + weights.junction * r.junctions
    cost += weights.near * r.near_unrelated + weights.stray * apart_steps
    r.cost = cost
    per_conn = cost / max(1, r.connections)
    # One straight 5-step wire per connection costs 2 -> score ~90; one
    # crossing per connection -> ~45.
    r.readability_score = round(100.0 * math.exp(-per_conn / 20.0), 1)
    if sum(viol.values()):
        r.readability_score = round(r.readability_score / 2.0, 1)
    r.violations = viol
    r.width, r.height = sch.width, sch.height
    return r
