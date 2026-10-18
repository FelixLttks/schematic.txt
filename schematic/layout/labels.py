"""Net-name label placement (annotation stage).

Every user-named net gets one label, placed on a horizontal wire segment of
that net.  Candidates are scored greedily: a label must fit on its segment,
must not overlap component bodies, other labels or wires of other nets, and
prefers long segments close to the net's first pin (where a reader starts
following the wire).  Nets without any free spot simply stay unlabelled --
a missing label is better than one that hides a wire.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from schematic.models import NetLabel, Schematic

CHAR_W = 6.0        # approximate advance of the 9 px monospace label font
LABEL_H = 10.0
GAP = 3.0           # baseline offset above the wire

Box = Tuple[float, float, float, float]


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place_net_labels(schematic: Schematic, min_terminals: int = 2) -> List[NetLabel]:
    comps = [
        (c.x - 1.0, c.y - 1.0, c.x + c.width + 1.0, c.y + c.height + 1.0)
        for c in schematic.components.values()
    ]
    # Pin labels and leads also occupy space next to the components.
    for c in schematic.components.values():
        for p in c.pins.values():
            comps.append((min(p.position.x, p.lead_start.x) - 4.0, p.position.y - 6.0,
                          max(p.position.x, p.lead_start.x) + 4.0, p.position.y + 6.0))
    wires_by_net: Dict[str, List[Tuple[float, float, float, float]]] = {}
    for w in schematic.wires:
        x1, x2 = sorted((w.start.x, w.end.x))
        y1, y2 = sorted((w.start.y, w.end.y))
        wires_by_net.setdefault(w.net_name, []).append((x1, y1, x2, y2))
    first_pin: Dict[str, Tuple[float, float]] = {}
    for net in schematic.nets:
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            pin = comp.pins.get(conn.pin_id) if (comp and conn.pin_id) else (comp.get_pin(conn.pin_name) if comp else None)
            if pin is not None:
                first_pin[net.name] = (pin.position.x, pin.position.y)
                break

    placed: List[Box] = []
    labels: List[NetLabel] = []
    nets = [n for n in schematic.nets if not n.auto_named and n.name in wires_by_net]
    # Short names first: they fit in more places, long ones take what is left.
    nets.sort(key=lambda n: (len(wires_by_net[n.name]), n.name))
    for net in nets:
        text = net.name
        width = len(text) * CHAR_W
        best: Optional[Tuple[float, float, float]] = None
        origin = first_pin.get(net.name, (0.0, 0.0))
        for (x1, y1, x2, y2) in wires_by_net[net.name]:
            if y1 != y2 or x2 - x1 < width + 16.0:
                continue
            span = x2 - x1
            steps = max(1, int((span - width - 16.0) // 20.0) + 1)
            for i in range(steps):
                lx = x1 + 8.0 + i * 20.0
                box = (lx - 2.0, y1 - GAP - LABEL_H, lx + width + 2.0, y1 - 1.0)
                if any(_overlap(box, c) for c in comps) or any(_overlap(box, b) for b in placed):
                    continue
                if any(
                    _overlap(box, (wx1 - 1.0, wy1 - 1.0, wx2 + 1.0, wy2 + 1.0))
                    for segs in wires_by_net.values()
                    for (wx1, wy1, wx2, wy2) in segs
                    if (wx1, wy1, wx2, wy2) != (x1, y1, x2, y2)
                ):
                    continue
                dist = abs(lx - origin[0]) + abs(y1 - origin[1])
                score = dist - 2.0 * span
                if best is None or score < best[0]:
                    best = (score, lx, y1)
        if best is None:
            continue
        _, lx, y = best
        placed.append((lx - 2.0, y - GAP - LABEL_H, lx + width + 2.0, y - 1.0))
        labels.append(NetLabel(text=text, x=lx, y=y - GAP, width=width, net_name=net.name))
    schematic.labels = labels
    return labels
