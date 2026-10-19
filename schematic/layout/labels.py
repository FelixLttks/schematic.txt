"""Net-name labels.

Every user-named net (not a generated ``Comp_Pin`` name) gets its name
written once, just above one of its horizontal wire segments: the longest
segment where the text fits without touching a component, a pin, another
label or a wire of another net.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from schematic.models import NetLabel, Schematic

CHAR_W = 5.6
TEXT_H = 10.0
Box = Tuple[float, float, float, float]


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place_net_labels(sch: Schematic) -> List[NetLabel]:
    obstacles: List[Box] = []
    for c in sch.components.values():
        obstacles.append((c.x - 2, c.y - 14, c.x + c.width + 2, c.y + c.height + 2))
        for p in c.pins.values():
            obstacles.append((p.position.x - 4, p.position.y - 4, p.position.x + 4, p.position.y + 4))
    foreign: Dict[str, List[Box]] = {}
    for w in sch.wires:
        box = (min(w.start.x, w.end.x) - 1, min(w.start.y, w.end.y) - 1,
               max(w.start.x, w.end.x) + 1, max(w.start.y, w.end.y) + 1)
        foreign.setdefault(w.net_name, []).append(box)
    labels: List[NetLabel] = []
    taken: List[Box] = []
    for net in sch.nets:
        if net.auto_named:
            continue
        text = net.name
        width = len(text) * CHAR_W
        segs = [w for w in sch.wires if w.net_name == net.name and w.is_horizontal]
        segs.sort(key=lambda w: (-abs(w.end.x - w.start.x), w.start.y, min(w.start.x, w.end.x)))
        placed = False
        for w in segs:
            x0, x1 = sorted((w.start.x, w.end.x))
            if x1 - x0 < width + 12:
                continue
            y = w.start.y
            # Try positions along the segment, centre first.
            span = x1 - x0 - width - 12
            steps = max(1, int(span // 20))
            offsets = sorted(range(steps + 1), key=lambda i: abs(i - steps / 2.0))
            for i in offsets:
                lx = x0 + 6 + span * (i / steps if steps else 0.5)
                box = (lx, y - TEXT_H - 3, lx + width, y - 2)
                if any(_overlap(box, o) for o in obstacles) or any(_overlap(box, t) for t in taken):
                    continue
                if any(_overlap(box, b) for name, bs in foreign.items() if name != net.name for b in bs):
                    continue
                labels.append(NetLabel(text=text, x=lx, y=y - 4, width=width, net_name=net.name))
                taken.append(box)
                placed = True
                break
            if placed:
                break
    sch.labels = labels
    return labels
