"""End-to-end layout: correctness on every input, structural expectations,
independence from names, determinism."""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from schematic import generate_schematic_svg, layout_schematic, parse_schematic, render_schematic_to_svg
from schematic.layout.config import config_for_effort

ROOT = Path(__file__).resolve().parent.parent
INPUTS = sorted(list((ROOT / "examples").glob("*.txt")) + list((ROOT / "tests" / "layout_cases").glob("*.txt")))


def _layout(text, effort="fast"):
    sch = parse_schematic(text)
    result = layout_schematic(sch, config_for_effort(effort))
    return sch, result


class TestEveryInput(unittest.TestCase):
    """Hard constraints, checked by the independent metrics module."""

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for path in INPUTS:
            sch, res = _layout(path.read_text(encoding="utf-8"))
            cls.results[path.name] = (sch, res)

    def test_no_violations(self):
        for name, (_, res) in self.results.items():
            self.assertEqual(res.report.violation_count, 0, f"{name}: {res.report.violations}")

    def test_everything_on_the_grid(self):
        for name, (sch, _) in self.results.items():
            for w in sch.wires:
                for v in (w.start.x, w.start.y, w.end.x, w.end.y):
                    self.assertEqual(v % 20.0, 0.0, name)
                self.assertTrue(w.is_horizontal or w.is_vertical, name)
            for c in sch.components.values():
                self.assertEqual(c.x % 20.0, 0.0, name)
                self.assertEqual(c.y % 20.0, 0.0, name)

    def test_labels_clear_of_components(self):
        for name, (sch, _) in self.results.items():
            for lab in sch.labels:
                for c in sch.components.values():
                    overlap = (lab.x < c.x + c.width and c.x < lab.x + lab.width
                               and lab.y - 10 < c.y + c.height and c.y < lab.y)
                    self.assertFalse(overlap, f"{name}: label {lab.text} on {c.id}")

    def test_svg_renders(self):
        for name, (sch, _) in self.results.items():
            svg = render_schematic_to_svg(sch)
            self.assertTrue(svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), name)


class TestStructure(unittest.TestCase):
    def test_linear_chain_is_one_straight_line(self):
        _, res = _layout((ROOT / "tests/layout_cases/01_linear_chain.txt").read_text(), "balanced")
        self.assertEqual(res.report.bends, 0)
        self.assertEqual(res.report.crossings, 0)

    def test_parallel_bundle_is_a_straight_ribbon(self):
        _, res = _layout((ROOT / "tests/layout_cases/04_parallel_bundle.txt").read_text())
        self.assertEqual(res.report.straight_connections, res.report.two_terminal_nets)
        self.assertEqual(res.report.crossings, 0)

    def test_isolated_parts_do_not_disturb(self):
        sch, res = _layout((ROOT / "tests/layout_cases/09_isolated.txt").read_text())
        self.assertEqual(res.report.violation_count, 0)
        wired = [c for c in sch.components.values() if c.id in ("MCU", "LED", "Rled")]
        loose = [c for c in sch.components.values() if c.id in ("Spare", "Hole", "Logo")]
        self.assertGreater(min(c.y for c in loose), max(c.y + c.height for c in wired))

    def test_empty_and_single_part(self):
        sch, res = _layout("")
        self.assertEqual(res.report.violation_count, 0)
        sch, res = _layout("Lonely: Part\n  pins: A, B\n")
        self.assertEqual(res.report.violation_count, 0)


def _rename(text: str) -> str:
    """Replaces every part id, pin name and net name by an unrelated name of
    the same length (text widths stay equal), keeping types untouched."""
    out = []
    mapping = {}

    def alias(word: str) -> str:
        # injective, same length, letters only (never a known kind word)
        if word not in mapping:
            n = sum(1 for k in mapping if len(k) == len(word))
            code = ""
            for _ in range(len(word)):
                code = "QXZJVKWYBFHMNPRSTUCD"[n % 20] + code
                n //= 20
            assert n == 0, "too many names for this length"
            mapping[word] = code
        return mapping[word]

    for line in text.splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            out.append(line)
            continue
        m = re.match(r"^(\s*)(pins|left|right)(\s*:\s*)(.*)$", line)
        if m:
            def pin(token: str) -> str:
                mm = re.match(r"^([^()]+)\((.*)\)$", token)
                return f"{alias(mm.group(1))}({alias(mm.group(2))})" if mm else alias(token)
            pins = [pin(p.strip()) for p in m.group(4).split(",")]
            out.append(m.group(1) + m.group(2) + m.group(3) + ", ".join(pins))
            continue
        m = re.match(r"^(\w+)(\s*:\s*)(.*)$", line)
        if m and "->" not in line and "<-" not in line:
            out.append(alias(m.group(1)) + m.group(2) + m.group(3))
            continue
        if "->" in line or "<-" in line:
            body, _, net = line.partition(" : ")

            def ref(mm):
                return alias(mm.group(1)) + "." + alias(mm.group(2))
            body = re.sub(r"(\w+)\.([\w+\-]+)", ref, body)
            out.append(body + (" : " + alias(net.strip()) if net else ""))
            continue
        out.append(line)
    return "\n".join(out)


class TestNamesHaveNoInfluence(unittest.TestCase):
    """Names are only labels: renaming parts, pins and nets (same lengths)
    must give exactly the same geometry."""

    def test_renaming_keeps_geometry(self):
        for case in ("tests/layout_cases/03_dense_network.txt", "tests/layout_cases/10_long_distance.txt"):
            text = (ROOT / case).read_text()
            renamed = _rename(text)
            self.assertNotEqual(text, renamed)
            a, _ = _layout(text)
            b, _ = _layout(renamed)
            geo_a = sorted((c.x, c.y, c.width, c.height) for c in a.components.values())
            geo_b = sorted((c.x, c.y, c.width, c.height) for c in b.components.values())
            self.assertEqual(geo_a, geo_b, case)
            wires_a = sorted((w.start.x, w.start.y, w.end.x, w.end.y) for w in a.wires)
            wires_b = sorted((w.start.x, w.start.y, w.end.x, w.end.y) for w in b.wires)
            self.assertEqual(wires_a, wires_b, case)


class TestDeterminism(unittest.TestCase):
    def test_same_svg_across_processes(self):
        code = ("import sys; sys.path.insert(0, %r); from schematic import generate_schematic_svg;"
                "from schematic.layout.config import config_for_effort;"
                "print(generate_schematic_svg(open(%r).read(), config=config_for_effort('fast')))"
                % (str(ROOT), str(ROOT / "tests/layout_cases/13_functional_regions.txt")))
        outs = []
        for seed in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            outs.append(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                                       text=True, check=True).stdout)
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
