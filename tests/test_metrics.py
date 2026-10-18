"""Tests for the layout quality metrics on hand-built geometry."""

import unittest

from schematic.models import Component, Net, NetConnection, Pin, Point, Schematic, Side, WireSegment
from schematic.layout.metrics import evaluate


def _terminal(sch: Schematic, cid: str, x: float, y: float, side: Side = Side.LEFT) -> None:
    """A one-pin part whose pin terminal sits at (x, y), body pushed outwards."""
    dx = {Side.LEFT: 0.0, Side.RIGHT: -40.0, Side.TOP: -20.0, Side.BOTTOM: -20.0}[side]
    dy = {Side.LEFT: -20.0, Side.RIGHT: -20.0, Side.TOP: 0.0, Side.BOTTOM: -40.0}[side]
    comp = Component(id=cid)
    comp.width, comp.height = 40.0, 40.0
    off = {Side.LEFT: (20.0, 0.0), Side.RIGHT: (-20.0, 0.0), Side.TOP: (0.0, 20.0), Side.BOTTOM: (0.0, -20.0)}[side]
    comp.x, comp.y = x + off[0] + dx, y + off[1] + dy
    comp.pins["P"] = Pin(name="P", side=side, id="P", position=Point(x, y), lead_start=Point(x, y))
    sch.components[cid] = comp


def _net(sch: Schematic, name: str, *cids: str) -> None:
    sch.nets.append(Net(name=name, connections=[NetConnection(c, "P", "P") for c in cids]))


def _wire(sch: Schematic, name: str, *pts) -> None:
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        sch.wires.append(WireSegment(Point(x1, y1), Point(x2, y2), name))


class TestMetrics(unittest.TestCase):
    def test_clean_crossing(self):
        sch = Schematic()
        _terminal(sch, "A1", 0, 100, Side.RIGHT)
        _terminal(sch, "A2", 200, 100, Side.LEFT)
        _terminal(sch, "B1", 100, 0, Side.BOTTOM)
        _terminal(sch, "B2", 100, 200, Side.TOP)
        _net(sch, "A", "A1", "A2")
        _net(sch, "B", "B1", "B2")
        _wire(sch, "A", (0, 100), (200, 100))
        _wire(sch, "B", (100, 0), (100, 200))
        rep = evaluate(sch)
        self.assertEqual(rep.crossings, 1)
        self.assertEqual(rep.bends, 0)
        self.assertEqual(rep.violation_count, 0, rep.violations)
        self.assertEqual(rep.connections, 2)

    def test_t_touch_is_a_violation(self):
        sch = Schematic()
        _terminal(sch, "A1", 0, 100, Side.RIGHT)
        _terminal(sch, "A2", 200, 100, Side.LEFT)
        _terminal(sch, "B1", 100, 0, Side.BOTTOM)
        _net(sch, "A", "A1", "A2")
        _net(sch, "B", "B1")
        _wire(sch, "A", (0, 100), (200, 100))
        _wire(sch, "B", (100, 0), (100, 100))   # ends on A's wire: looks connected
        rep = evaluate(sch)
        self.assertEqual(rep.violations["t_touches"], 1)
        self.assertLess(rep.readability_score, 60.0)

    def test_collinear_overlap_is_a_violation(self):
        sch = Schematic()
        _terminal(sch, "A1", 0, 100, Side.RIGHT)
        _terminal(sch, "A2", 200, 100, Side.LEFT)
        _terminal(sch, "B1", 0, 120, Side.RIGHT)
        _terminal(sch, "B2", 200, 120, Side.LEFT)
        _net(sch, "A", "A1", "A2")
        _net(sch, "B", "B1", "B2")
        _wire(sch, "A", (0, 100), (200, 100))
        _wire(sch, "B", (0, 120), (40, 120), (40, 100), (160, 100), (160, 120), (200, 120))
        rep = evaluate(sch)
        self.assertGreater(rep.violations["collinear_overlap"], 0)

    def test_unrouted_pin(self):
        sch = Schematic()
        _terminal(sch, "A1", 0, 100, Side.RIGHT)
        _terminal(sch, "A2", 200, 100, Side.LEFT)
        _net(sch, "A", "A1", "A2")
        _wire(sch, "A", (0, 100), (120, 100))
        rep = evaluate(sch)
        self.assertEqual(rep.violations["unrouted_pins"], 1)
        self.assertEqual(rep.violations["disconnected_nets"], 1)

    def test_bends_and_small_jog(self):
        sch = Schematic()
        _terminal(sch, "A1", 0, 100, Side.RIGHT)
        _terminal(sch, "A2", 200, 120, Side.LEFT)
        _net(sch, "A", "A1", "A2")
        _wire(sch, "A", (0, 100), (100, 100), (100, 120), (200, 120))
        rep = evaluate(sch)
        self.assertEqual(rep.bends, 2)
        self.assertEqual(rep.small_jogs, 1)
        self.assertEqual(rep.violation_count, 0, rep.violations)

    def test_bundle_parallelism(self):
        # Two nets joining the same two parts form a bundle; drawn as adjacent
        # straight wires they are perfectly coherent.
        sch2 = Schematic()
        left = Component(id="L")
        left.x, left.y, left.width, left.height = -60.0, 60.0, 40.0, 100.0
        right = Component(id="R")
        right.x, right.y, right.width, right.height = 320.0, 60.0, 40.0, 100.0
        for i, name in enumerate(("BUS_A", "BUS_B")):
            y = 100 + 20 * i
            left.pins[f"P{i}"] = Pin(name=f"P{i}", side=Side.RIGHT, id=f"P{i}", position=Point(0, y))
            right.pins[f"P{i}"] = Pin(name=f"P{i}", side=Side.LEFT, id=f"P{i}", position=Point(300, y))
            sch2.nets.append(Net(name=name, connections=[NetConnection("L", f"P{i}", f"P{i}"),
                                                          NetConnection("R", f"P{i}", f"P{i}")]))
            _wire(sch2, name, (0, y), (300, y))
        sch2.components = {"L": left, "R": right}
        rep2 = evaluate(sch2)
        self.assertEqual(rep2.bundles, 1)
        self.assertEqual(rep2.parallelism_score, 1.0)
        self.assertEqual(rep2.bundled_nets, 2)
        self.assertEqual(rep2.readability_score, 100.0)


if __name__ == "__main__":
    unittest.main()
