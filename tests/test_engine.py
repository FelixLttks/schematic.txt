"""End-to-end layout: hard constraints on every input, structural
expectations, independence from names, determinism."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from schematic_txt import config_for_effort, layout_schematic, parse_schematic, render_svg
from schematic_txt.colors import supply_kind

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "cases"
INPUTS = sorted([*(ROOT / "examples").glob("*.txt"), *CASES.glob("*.txt")])


def _layout(text, effort="fast"):
    sch = parse_schematic(text)
    result = layout_schematic(sch, config_for_effort(effort))
    return sch, result


@pytest.fixture(scope="module", params=INPUTS, ids=lambda p: p.stem)
def laid_out(request):
    return _layout(request.param.read_text(encoding="utf-8"))


@pytest.mark.slow
class TestEveryInput:
    """Hard constraints, checked by the independent metrics module."""

    def test_no_violations(self, laid_out):
        _, res = laid_out
        assert res.report.violation_count == 0, res.report.violations

    def test_everything_on_the_grid(self, laid_out):
        sch, _ = laid_out
        for w in sch.wires:
            assert all(v % 20.0 == 0.0 for v in (w.start.x, w.start.y, w.end.x, w.end.y))
            assert w.is_horizontal or w.is_vertical
        for c in sch.components.values():
            assert c.x % 20.0 == 0.0
            assert c.y % 20.0 == 0.0

    def test_labels_clear_of_components(self, laid_out):
        sch, _ = laid_out
        for lab in sch.labels:
            for c in sch.components.values():
                overlap = (
                    lab.x < c.x + c.width
                    and c.x < lab.x + lab.width
                    and lab.y - 10 < c.y + c.height
                    and c.y < lab.y
                )
                assert not overlap, f"label {lab.text} on {c.id}"

    def test_svg_renders(self, laid_out):
        sch, _ = laid_out
        svg = render_svg(sch)
        assert svg.startswith("<svg")
        assert svg.rstrip().endswith("</svg>")


def test_linear_chain_is_one_straight_line():
    _, res = _layout((CASES / "01_linear_chain.txt").read_text(), "balanced")
    assert res.report.bends == 0
    assert res.report.crossings == 0


def test_parallel_bundle_is_a_straight_ribbon():
    _, res = _layout((CASES / "04_parallel_bundle.txt").read_text())
    assert res.report.straight_connections == res.report.two_terminal_nets
    assert res.report.crossings == 0


def test_isolated_parts_do_not_disturb():
    sch, res = _layout((CASES / "09_isolated.txt").read_text())
    assert res.report.violation_count == 0
    wired = [c for c in sch.components.values() if c.id in ("MCU", "LED", "Rled")]
    loose = [c for c in sch.components.values() if c.id in ("Spare", "Hole", "Logo")]
    assert min(c.y for c in loose) > max(c.y + c.height for c in wired)


@pytest.mark.parametrize("text", ["", "Lonely: Part\n  pins: A, B\n"], ids=["empty", "single-part"])
def test_trivial_inputs(text):
    _, res = _layout(text)
    assert res.report.violation_count == 0


def _rename(text: str) -> str:
    """Replaces every part id, pin name and net name by an unrelated name of
    the same length (text widths stay equal), keeping types untouched and
    the names of supply and ground nets (the one thing the layout reads
    from a name: such a net may become tags instead of a wire)."""
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

    def pin(token: str) -> str:
        m = re.match(r"^([^()]+)\((.*)\)$", token)
        return f"{alias(m.group(1))}({alias(m.group(2))})" if m else alias(token)

    def ref(m: re.Match) -> str:
        return alias(m.group(1)) + "." + alias(m.group(2))

    for line in text.splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            out.append(line)
            continue
        m = re.match(r"^(\s*)(pins|left|right)(\s*:\s*)(.*)$", line)
        if m:
            pins = [pin(p.strip()) for p in m.group(4).split(",")]
            out.append(m.group(1) + m.group(2) + m.group(3) + ", ".join(pins))
            continue
        is_connection = "->" in line or "<-" in line
        m = re.match(r"^(\w+)(\s*:\s*)(.*)$", line)
        if m and not is_connection:
            out.append(alias(m.group(1)) + m.group(2) + m.group(3))
        elif is_connection:
            body, _, net = line.partition(" : ")
            body = re.sub(r"(\w+)\.([\w+\-]+)", ref, body)
            net = net.strip()
            out.append(body + (" : " + (net if supply_kind(net) else alias(net)) if net else ""))
        else:
            out.append(line)
    return "\n".join(out)


@pytest.mark.parametrize("case", ["03_dense_network.txt", "10_long_distance.txt"])
def test_names_have_no_influence(case):
    """Names are only labels: renaming parts, pins and signal nets (same
    lengths) must give exactly the same geometry."""
    text = (CASES / case).read_text()
    renamed = _rename(text)
    assert text != renamed
    a, _ = _layout(text)
    b, _ = _layout(renamed)

    def geometry(sch):
        parts = sorted((c.x, c.y, c.width, c.height) for c in sch.components.values())
        wires = sorted((w.start.x, w.start.y, w.end.x, w.end.y) for w in sch.wires)
        return parts, wires

    assert geometry(a) == geometry(b)


def test_same_svg_across_processes():
    code = (
        "from schematic_txt import render;"
        f"print(render(open({str(CASES / '13_functional_regions.txt')!r}).read(), effort='fast'))"
    )
    outs = []
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
        )
        outs.append(proc.stdout)
    assert outs[0] == outs[1]
