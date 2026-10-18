"""Router hard rules on hand-placed components."""

import unittest

from schematic import parse_schematic
from schematic.models import Side
from schematic.layout.metrics import evaluate
from schematic.layout.pin_allocator import apply_order, size_and_place_pins
from schematic.layout.router import GridRouter
from schematic.layout.semantics import analyze


def _place(sch, positions, sides):
    for cid, (x, y) in positions.items():
        comp = sch.components[cid]
        for p in comp.pins.values():
            p.side = sides[cid]
        apply_order(comp, lambda p: 0.0)
        comp.x, comp.y = x, y
        size_and_place_pins(comp)


class TestRouterRules(unittest.TestCase):
    TEXT = """
    A: Left Part
      pins: P1, P2, P3, P4
    B: Right Part
      pins: Q1, Q2, Q3, Q4
    A.P1 -> B.Q4 : N1
    A.P2 -> B.Q3 : N2
    A.P3 -> B.Q2 : N3
    A.P4 -> B.Q1 : N4
    """

    def _route(self, gap: float):
        """Places A and B facing each other, ``gap`` px between their pin terminals."""
        sch = parse_schematic(self.TEXT)
        _place(sch, {"A": (0.0, 0.0), "B": (0.0, 0.0)}, {"A": Side.RIGHT, "B": Side.LEFT})
        a = sch.components["A"]
        _place(sch, {"B": (a.x + a.width + 40.0 + gap, 0.0)}, {"B": Side.LEFT})
        sem = analyze(sch)
        router = GridRouter(sch, semantics=sem)
        router.route()
        return sch, router, evaluate(sch, sem)

    def test_inverted_bundle_is_connected_without_violations(self):
        # Fully inverted pin orders force crossings; they must stay clean.
        sch, router, rep = self._route(gap=200.0)
        self.assertEqual(router.failures, [])
        self.assertEqual(rep.violation_count, 0, rep.violations)
        self.assertGreater(rep.crossings, 0)

    def test_impossible_corridor_is_reported_never_drawn_ambiguously(self):
        # 60 px between the terminals leaves only the two escape columns and a
        # fully inverted bundle boxes the middle wires in.  The router must not
        # fake a connection: every failure is reported, nothing is ambiguous.
        sch, router, rep = self._route(gap=60.0)
        for kind in ("collinear_overlap", "t_touches", "dual_nodes", "foreign_terminal_contact",
                     "wire_through_component"):
            self.assertEqual(rep.violations[kind], 0, kind)
        self.assertEqual(rep.violations["unrouted_pins"], len(router.failures))

    def test_wires_enter_pins_perpendicular_to_the_edge(self):
        sch, _, _ = self._route(gap=200.0)
        outward = {Side.LEFT: (-1, 0), Side.RIGHT: (1, 0), Side.TOP: (0, -1), Side.BOTTOM: (0, 1)}
        for comp in sch.components.values():
            for pin in comp.pins.values():
                touching = [w for w in sch.wires if pin.position in (w.start, w.end)]
                self.assertTrue(touching, f"{comp.id}.{pin.name} has no wire")
                for w in touching:
                    other = w.end if w.start == pin.position else w.start
                    dx, dy = other.x - pin.position.x, other.y - pin.position.y
                    ox, oy = outward[pin.side]
                    self.assertGreater(dx * ox + dy * oy, 0, f"{comp.id}.{pin.name} entered sideways")


if __name__ == "__main__":
    unittest.main()
