"""End-to-end layout quality tests over all examples and layout test cases.

* Hard invariants (connectivity, overlaps, grid, ambiguity) must hold for
  every input -- correctness is never traded for appearance.
* Structural expectations encode what a good drawing of each case must show.
* Regression floors compare the readability metrics with the stored baseline
  ``tests/layout_quality_baseline.json`` (small tolerances).
* Geometry snapshots (exact fingerprints) catch any unintended change.

Regenerate the baseline after an intended layout change -- and after looking at
the drawings, e.g. with ``python3 tools/layout_report.py`` -- with::

    UPDATE_LAYOUT_BASELINE=1 python3 -m unittest tests.test_layout_quality
"""

import glob
import hashlib
import json
import os
import unittest
from pathlib import Path

from schematic import parse_schematic, layout_schematic, render_schematic_to_svg
from schematic.layout.metrics import pair_coherence

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "layout_quality_baseline.json"
CASE_FILES = sorted(glob.glob(str(ROOT / "examples" / "*.txt"))) + sorted(
    glob.glob(str(ROOT / "tests" / "layout_cases" / "*.txt"))
)


def _fingerprint(sch) -> str:
    """Stable hash of the drawn geometry (grid units)."""
    parts = []
    for c in sorted(sch.components.values(), key=lambda c: c.id):
        parts.append(f"C {c.id} {round(c.x / 20)} {round(c.y / 20)} {round(c.width / 20)} {round(c.height / 20)}")
        for p in sorted(c.pins.values(), key=lambda p: p.id):
            parts.append(f"P {c.id}.{p.id} {p.side.value} {round(p.position.x / 20)} {round(p.position.y / 20)}")
    for w in sorted(sch.wires, key=lambda w: (w.net_name, w.start.x, w.start.y, w.end.x, w.end.y)):
        parts.append(f"W {w.net_name} {round(w.start.x / 20)} {round(w.start.y / 20)} {round(w.end.x / 20)} {round(w.end.y / 20)}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


class LayoutQualityTest(unittest.TestCase):
    results = {}

    @classmethod
    def setUpClass(cls):
        for path in CASE_FILES:
            name = Path(path).stem
            sch = parse_schematic(Path(path).read_text(encoding="utf-8"))
            result = layout_schematic(sch)
            cls.results[name] = (sch, result.report)
        if os.environ.get("UPDATE_LAYOUT_BASELINE"):
            data = {
                name: {
                    "readability_score": rep.readability_score,
                    "crossings": rep.crossings,
                    "bends": rep.bends,
                    "fingerprint": _fingerprint(sch),
                }
                for name, (sch, rep) in sorted(cls.results.items())
            }
            BASELINE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ invariants
    def test_no_violations(self):
        for name, (_, rep) in self.results.items():
            with self.subTest(case=name):
                self.assertEqual(rep.violation_count, 0, {k: v for k, v in rep.violations.items() if v})

    def test_geometry_on_grid(self):
        for name, (sch, _) in self.results.items():
            with self.subTest(case=name):
                for c in sch.components.values():
                    for v in (c.x, c.y, c.width, c.height):
                        self.assertEqual(v % 20.0, 0.0, f"{c.id}: {v}")
                    for p in c.pins.values():
                        self.assertEqual(p.position.x % 20.0, 0.0)
                        self.assertEqual(p.position.y % 20.0, 0.0)
                for w in sch.wires:
                    for v in (w.start.x, w.start.y, w.end.x, w.end.y):
                        self.assertEqual(v % 20.0, 0.0, f"wire of {w.net_name}")
                    self.assertTrue(w.is_horizontal or w.is_vertical)

    def test_labels_do_not_cover_parts(self):
        for name, (sch, _) in self.results.items():
            with self.subTest(case=name):
                for lab in sch.labels:
                    box = (lab.x, lab.y - 10.0, lab.x + lab.width, lab.y)
                    for c in sch.components.values():
                        inside = box[0] < c.x + c.width and c.x < box[2] and box[1] < c.y + c.height and c.y < box[3]
                        self.assertFalse(inside, f"label {lab.text} covers {c.id}")

    def test_svg_renders(self):
        for name, (sch, _) in self.results.items():
            with self.subTest(case=name):
                for dark in (True, False):
                    svg = render_schematic_to_svg(sch, dark_theme=dark)
                    self.assertTrue(svg.startswith("<svg"))
                    self.assertIn('id="schematic-wires"', svg)

    # ---------------------------------------------- structural expectations
    def test_linear_chain_is_a_straight_line(self):
        _, rep = self.results["01_linear_chain"]
        self.assertEqual(rep.crossings, 0)
        self.assertEqual(rep.bends, 0)

    def test_parallel_bus_is_a_ribbon(self):
        _, rep = self.results["04_parallel_bundle"]
        self.assertEqual(rep.crossings, 0)
        self.assertEqual(rep.bends, 0)
        self.assertEqual(rep.parallelism_score, 1.0)

    def test_spi_bundle_has_no_internal_crossings(self):
        sch, rep = self.results["esp32_sd_spi"]
        self.assertEqual(rep.crossings, 0)
        for a, b in (("SPI_MOSI", "SPI_MISO"), ("SPI_MISO", "SPI_SCK"), ("SPI_SCK", "SPI_CS")):
            self.assertGreaterEqual(pair_coherence(sch, a, b), 0.6, (a, b))

    def test_differential_pairs_stay_together(self):
        sch, _ = self.results["06_differential_pairs"]
        self.assertGreaterEqual(pair_coherence(sch, "USB_D_P", "USB_D_N"), 0.8)
        # The CAN pair also branches to its termination resistor, whose two
        # drops necessarily leave the pair; the main run must still be shared.
        self.assertGreaterEqual(pair_coherence(sch, "CAN_H", "CAN_L"), 0.5)

    def test_related_wires_take_the_same_corridor(self):
        sch, _ = self.results["11_length_vs_readability"]
        self.assertGreaterEqual(pair_coherence(sch, "LINK_A", "LINK_B"), 0.8)

    def test_clusters_without_crossings(self):
        _, rep = self.results["08_clusters"]
        self.assertEqual(rep.crossings, 0)

    def test_isolated_parts_stay_out_of_the_way(self):
        sch, rep = self.results["09_isolated"]
        connected_bottom = max(sch.components[c].y + sch.components[c].height for c in ("MCU", "LED", "Rled"))
        for cid in ("Spare", "Hole", "Logo"):
            self.assertGreaterEqual(sch.components[cid].y, connected_bottom, cid)
        self.assertEqual(rep.component_overlaps, 0)

    def test_fan_out_trees_have_junctions(self):
        sch, _ = self.results["02_branching"]
        nets = {j.net_name for j in sch.junctions}
        self.assertIn("CLK_25M", nets)
        self.assertIn("SYS_RST_N", nets)

    # ----------------------------------------------------- regression floors
    def test_quality_does_not_regress(self):
        if not BASELINE.exists():
            self.skipTest("no baseline stored yet")
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for name, (_, rep) in self.results.items():
            ref = baseline.get(name)
            if ref is None:
                continue
            with self.subTest(case=name):
                self.assertGreaterEqual(rep.readability_score, ref["readability_score"] - 3.0)
                self.assertLessEqual(rep.crossings, ref["crossings"] + 2)
                self.assertLessEqual(rep.bends, int(ref["bends"] * 1.15) + 2)

    def test_geometry_snapshots(self):
        """Exact geometry fingerprints: any change must be reviewed and the
        baseline regenerated deliberately (see module docstring)."""
        if not BASELINE.exists():
            self.skipTest("no baseline stored yet")
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for name, (sch, _) in self.results.items():
            ref = baseline.get(name)
            if ref is None:
                continue
            with self.subTest(case=name):
                self.assertEqual(_fingerprint(sch), ref["fingerprint"],
                                 "layout changed: review it and regenerate the baseline")


if __name__ == "__main__":
    unittest.main()
