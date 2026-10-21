#!/usr/bin/env python3
"""Before/after comparison report for the layout engine.

Renders every example and layout test case with the working-tree engine and
with a baseline engine from a git revision, scores both with the *current*
quality metrics and writes a Markdown report with side-by-side images and a
metrics table.

    uv run scripts/layout_report.py                      # baseline: HEAD
    uv run scripts/layout_report.py --baseline v0.1.0    # any git revision
    uv run scripts/layout_report.py --no-baseline        # current engine only

The baseline runs in a separate interpreter from a ``git archive`` extract,
so it never mixes with the working-tree modules.  Layout runs are heavy
(they fork worker processes); run one report at a time.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from schematic_txt import config_for_effort, layout_schematic, parse_schematic, render_svg
from schematic_txt.layout.metrics import evaluate
from schematic_txt.models import (
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

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ["examples/*.txt", "tests/cases/*.txt"]

# Runs inside the baseline checkout: lays out every case and dumps the
# geometry as plain JSON (read back by ``_schematic_from_dict``).
_BASELINE_RUNNER = r"""
import json, sys, time, traceback
sys.path.insert(0, sys.argv[1])
from schematic_txt import config_for_effort, layout_schematic, parse_schematic, render_svg

def dump(sch):
    return {
        "components": [{
            "id": c.id, "type_name": c.type_name, "kind": c.kind, "value": c.value,
            "x": c.x, "y": c.y, "width": c.width, "height": c.height,
            "pins": [{
                "id": p.id, "name": p.name, "label": p.label, "side": p.side.value,
                "position": [p.position.x, p.position.y],
                "lead_start": [p.lead_start.x, p.lead_start.y]} for p in c.pins.values()],
        } for c in sch.components.values()],
        "nets": [{"name": n.name, "auto_named": n.auto_named,
                  "connections": [[cn.component_id, cn.pin_name, cn.pin_id] for cn in n.connections]}
                 for n in sch.nets],
        "wires": [[w.start.x, w.start.y, w.end.x, w.end.y, w.net_name] for w in sch.wires],
        "junctions": [[j.point.x, j.point.y, j.net_name] for j in sch.junctions],
    }

cases = json.load(open(sys.argv[2]))
out = {}
for name, text in cases.items():
    try:
        t0 = time.perf_counter()
        sch = parse_schematic(text)
        layout_schematic(sch, config_for_effort(sys.argv[4]))
        elapsed = time.perf_counter() - t0
        out[name] = {"svg": render_svg(sch), "geometry": dump(sch), "seconds": elapsed}
    except Exception:
        out[name] = {"error": traceback.format_exc()}
json.dump(out, open(sys.argv[3], "w"))
"""


def _schematic_from_dict(data: dict[str, Any]) -> Schematic:
    sch = Schematic()
    for cd in data["components"]:
        comp = Component(id=cd["id"], kind=cd["kind"], value=cd["value"])
        comp.type_name = cd["type_name"]  # set after construction: no type detection
        comp.x, comp.y, comp.width, comp.height = cd["x"], cd["y"], cd["width"], cd["height"]
        for pd in cd["pins"]:
            pin = Pin(
                name=pd["name"],
                label=pd["label"],
                side=Side(pd["side"]),
                id=pd["id"],
                position=Point(*pd["position"]),
                lead_start=Point(*pd["lead_start"]),
            )
            comp.pins[pin.id] = pin
        sch.components[comp.id] = comp
    for nd in data["nets"]:
        net = Net(name=nd["name"], auto_named=nd["auto_named"])
        net.connections = [NetConnection(c, p, pid) for c, p, pid in nd["connections"]]
        sch.nets.append(net)
    sch.wires = [WireSegment(Point(x1, y1), Point(x2, y2), name) for x1, y1, x2, y2, name in data["wires"]]
    sch.junctions = [WireJunction(Point(x, y), net_name=name) for x, y, name in data["junctions"]]
    return sch


def _collect_cases(patterns: list[str]) -> dict[str, str]:
    cases: dict[str, str] = {}
    for pattern in patterns:
        for path in sorted(glob.glob(str(ROOT / pattern))):
            cases[Path(path).stem] = Path(path).read_text(encoding="utf-8")
    return cases


def _run_baseline(rev: str, cases: dict[str, str], effort: str) -> dict[str, dict]:
    tmp = Path(tempfile.mkdtemp(prefix="schematic-baseline-"))
    try:
        archive = tmp / "src.tar"
        subprocess.run(
            ["git", "-C", str(ROOT), "archive", "--format=tar", "-o", str(archive), rev, "src"],
            check=True,
        )
        src = tmp / "src"
        src.mkdir()
        with tarfile.open(archive) as tf:
            tf.extractall(src, filter="data")
        (tmp / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        (tmp / "runner.py").write_text(_BASELINE_RUNNER, encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(tmp / "runner.py"),
                str(src / "src"),
                str(tmp / "cases.json"),
                str(tmp / "out.json"),
                effort,
            ],
            check=True,
            cwd=str(tmp),
        )
        return json.loads((tmp / "out.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _to_png(svg_path: Path) -> Path | None:
    exe = shutil.which("magick")
    if not exe:
        return None
    png = svg_path.with_suffix(".png")
    subprocess.run(
        [exe, "-density", "96", str(svg_path), "-background", "white", "-flatten", str(png)],
        check=False,
        capture_output=True,
    )
    return png if png.exists() else None


_COLUMNS = [
    ("Score", "readability_score", "{:.1f}"),
    ("Cost", "cost", "{:.0f}"),
    ("Cross", "crossings", "{}"),
    ("Bends", "bends", "{}"),
    ("Straight", "straight_connections", "{}"),
    ("Length", "wire_length", "{:.0f}"),
    ("Viol.", "violation_count", "{}"),
]


def _cell(rep: dict | None, key: str, fmt: str) -> str:
    if rep is None:
        return "-"
    v = rep.get(key)
    if v is None:
        return "n/a"
    return fmt.format(v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", default="HEAD", help="git revision of the baseline engine (default: HEAD)")
    ap.add_argument("--no-baseline", action="store_true", help="only render the current engine")
    ap.add_argument("--out", default="build/layout-report", help="output directory")
    ap.add_argument("--cases", nargs="*", default=DEFAULT_CASES, help="glob patterns of input files")
    ap.add_argument("--no-png", action="store_true", help="skip PNG rasterisation")
    ap.add_argument("--effort", default="balanced", help="layout effort of both engines (default: balanced)")
    args = ap.parse_args()

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    cases = _collect_cases(args.cases)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 1

    baseline: dict[str, dict] = {}
    rev_label = args.baseline
    if not args.no_baseline:
        print(f"running baseline engine from '{args.baseline}' ...", file=sys.stderr)
        baseline = _run_baseline(args.baseline, cases, args.effort)
        sha = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", args.baseline], capture_output=True, text=True
        ).stdout.strip()
        if sha and sha != args.baseline:
            rev_label = f"{args.baseline} ({sha})"

    results: dict[str, dict] = {}
    for name, text in cases.items():
        entry: dict = {}
        t0 = time.perf_counter()
        sch = parse_schematic(text)
        layout_schematic(sch, config_for_effort(args.effort))
        entry["after_seconds"] = time.perf_counter() - t0
        after_rep = evaluate(sch)
        entry["after"] = after_rep.as_dict()
        svg_after = out / f"{name}.after.svg"
        svg_after.write_text(render_svg(sch), encoding="utf-8")
        if not args.no_png:
            _to_png(svg_after)

        b = baseline.get(name)
        if b and "geometry" in b:
            before_sch = _schematic_from_dict(b["geometry"])
            entry["before"] = evaluate(before_sch).as_dict()
            entry["before_seconds"] = b.get("seconds")
            svg_before = out / f"{name}.before.svg"
            svg_before.write_text(b["svg"], encoding="utf-8")
            if not args.no_png:
                _to_png(svg_before)
        elif b:
            entry["before_error"] = b.get("error")
        results[name] = entry

    (out / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    # ---- Markdown report ----------------------------------------------------
    lines = ["# Layout engine comparison", ""]
    if baseline:
        lines.append(
            f"Baseline engine: `{rev_label}` (first value) vs. current engine (second value). "
            "All drawings are scored with the current metrics (`src/schematic_txt/layout/metrics.py`)."
        )
    lines.append("")
    header = "| Case | " + " | ".join(c[0] for c in _COLUMNS) + " | Time (s) |"
    lines += [header, "|" + "---|" * (len(_COLUMNS) + 2)]
    for name, entry in results.items():
        cells = []
        for _, key, fmt in _COLUMNS:
            before = _cell(entry.get("before"), key, fmt)
            after = _cell(entry.get("after"), key, fmt)
            cells.append(f"{before} → {after}" if baseline else after)
        t_before = entry.get("before_seconds")
        t_after = entry["after_seconds"]
        timing = (
            f"{t_before:.2f} → {t_after:.2f}" if (baseline and t_before is not None) else f"{t_after:.2f}"
        )
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {timing} |")
    lines.append("")
    for name, entry in results.items():
        lines += [f"## {name}", ""]
        ext = "png" if not args.no_png and (out / f"{name}.after.png").exists() else "svg"
        if "before" in entry:
            lines.append(f"**Before** (score {entry['before']['readability_score']:.1f})")
            lines += ["", f"![before]({name}.before.{ext})", ""]
        lines.append(f"**After** (score {entry['after']['readability_score']:.1f})")
        lines += ["", f"![after]({name}.after.{ext})", ""]
    (out / "index.md").write_text("\n".join(lines), encoding="utf-8")

    # ---- Console summary ----------------------------------------------------
    table = [line for line in lines if line.startswith("| ") and not line.startswith("| Case")]
    rows = [["Case"] + [c[0] for c in _COLUMNS] + ["Time (s)"]]
    rows += [[c.strip() for c in line.strip("|").split("|")] for line in table]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)))
    print(f"\nreport written to {out / 'index.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
