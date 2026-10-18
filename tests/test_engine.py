"""Engine-level behaviour: candidate selection, feedback, configuration."""

import unittest

from schematic import parse_schematic, layout_schematic
from schematic.layout.config import LayoutConfig, config_for_effort

INVERTED = """
A: Left Part
  pins: P1, P2, P3, P4
B: Right Part
  pins: Q1, Q2, Q3, Q4
A.P1 -> B.Q4 : N1
A.P2 -> B.Q3 : N2
A.P3 -> B.Q2 : N3
A.P4 -> B.Q1 : N4
"""


# Declared pin orders: the bundle cannot be untangled by re-ordering pins.
INVERTED_FIXED = """
A: Left Part
  right: P1, P2, P3, P4
B: Right Part
  left: Q1, Q2, Q3, Q4
A.P1 -> B.Q4 : N1
A.P2 -> B.Q3 : N2
A.P3 -> B.Q2 : N3
A.P4 -> B.Q1 : N4
"""


class TestEngine(unittest.TestCase):
    def test_spacing_feedback_restores_connectivity(self):
        # Corridors far too tight for the router: the engine must notice the
        # unconnected pins and re-place with wider spacing.
        cfg = LayoutConfig()
        cfg.spacing.min_h_gap = 40.0
        cfg.spacing.track_gap = 0.0
        cfg.candidates = (("layered", False),)
        sch = parse_schematic(INVERTED_FIXED)
        result = layout_schematic(sch, cfg)
        self.assertGreater(result.attempts, 1)
        self.assertEqual(result.report.violation_count, 0, result.report.violations)

    def test_candidates_are_reported(self):
        sch = parse_schematic(INVERTED)
        result = layout_schematic(sch)
        labels = [c[0] for c in result.candidates]
        self.assertEqual(labels[:3], ["layered", "layered+search", "stress+search"])
        for extra in labels[3:]:
            self.assertIn("+orient:", extra)
        self.assertIn(result.strategy, labels)
        best = max(c[1] for c in result.candidates if c[2] == 0)
        self.assertGreaterEqual(result.report.readability_score, best - 1e-6)

    def test_effort_levels(self):
        self.assertEqual(len(config_for_effort("fast").candidates), 1)
        self.assertGreater(len(config_for_effort("thorough").candidates), 3)
        with self.assertRaises(ValueError):
            config_for_effort("ludicrous")
        for effort in ("fast", "balanced"):
            sch = parse_schematic(INVERTED)
            result = layout_schematic(sch, config_for_effort(effort))
            self.assertEqual(result.report.violation_count, 0, effort)

    def test_empty_and_single_component(self):
        sch = parse_schematic("")
        result = layout_schematic(sch)
        self.assertEqual(sch.wires, [])
        self.assertEqual(result.report.violation_count, 0)
        sch = parse_schematic("Lonely: Part\n  pins: A, B\n")
        result = layout_schematic(sch)
        self.assertEqual(result.report.violation_count, 0)
        self.assertEqual(len(sch.components), 1)


if __name__ == "__main__":
    unittest.main()
