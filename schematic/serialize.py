"""Plain-dict (JSON) serialisation of placed and routed schematics.

Used by the comparison tooling to score output produced by other engine
versions with the current metrics code, and handy for debugging.
"""

from __future__ import annotations

from typing import Any, Dict

from schematic.models import (
    Component,
    Net,
    NetConnection,
    Pin,
    Point,
    Schematic,
    Side,
    WireJunction,
    WireSegment,
)


def schematic_to_dict(sch: Schematic) -> Dict[str, Any]:
    return {
        "width": sch.width,
        "height": sch.height,
        "components": [
            {
                "id": c.id,
                "type_name": c.type_name,
                "kind": c.kind,
                "value": c.value,
                "x": c.x,
                "y": c.y,
                "width": c.width,
                "height": c.height,
                "pins": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "label": p.label,
                        "side": p.side.value,
                        "order": p.order,
                        "side_specified": p.side_specified,
                        "position": [p.position.x, p.position.y],
                        "lead_start": [p.lead_start.x, p.lead_start.y],
                    }
                    for p in c.pins.values()
                ],
            }
            for c in sch.components.values()
        ],
        "nets": [
            {
                "name": n.name,
                "color": n.color,
                "auto_named": getattr(n, "auto_named", False),
                "connections": [
                    {"component_id": cn.component_id, "pin_name": cn.pin_name, "pin_id": cn.pin_id}
                    for cn in n.connections
                ],
            }
            for n in sch.nets
        ],
        "wires": [
            [w.start.x, w.start.y, w.end.x, w.end.y, w.net_name, w.color] for w in sch.wires
        ],
        "junctions": [[j.point.x, j.point.y, j.net_name] for j in sch.junctions],
    }


def schematic_from_dict(data: Dict[str, Any]) -> Schematic:
    sch = Schematic(width=data.get("width", 1200.0), height=data.get("height", 800.0))
    for cd in data["components"]:
        # kind is restored verbatim; type detection must not run again.
        comp = Component(id=cd["id"], kind=cd.get("kind", "") or "", value=cd.get("value", ""))
        comp.type_name = cd.get("type_name", "")
        comp.x, comp.y = cd["x"], cd["y"]
        comp.width, comp.height = cd["width"], cd["height"]
        for pd in cd["pins"]:
            pin = Pin(
                name=pd["name"],
                label=pd.get("label", ""),
                side=Side(pd.get("side", "left")),
                order=pd.get("order", 0),
                side_specified=pd.get("side_specified", False),
                id=pd["id"],
                position=Point(*pd["position"]),
                lead_start=Point(*pd["lead_start"]),
            )
            comp.pins[pin.id] = pin
        sch.components[comp.id] = comp
    for nd in data["nets"]:
        net = Net(name=nd["name"], color=nd.get("color"), auto_named=nd.get("auto_named", False))
        for cn in nd["connections"]:
            net.connections.append(
                NetConnection(component_id=cn["component_id"], pin_name=cn["pin_name"], pin_id=cn.get("pin_id"))
            )
        sch.nets.append(net)
    for x1, y1, x2, y2, name, color in data["wires"]:
        sch.wires.append(WireSegment(Point(x1, y1), Point(x2, y2), name, color))
    for x, y, name in data["junctions"]:
        sch.junctions.append(WireJunction(point=Point(x, y), net_name=name))
    return sch
