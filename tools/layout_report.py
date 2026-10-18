#!/usr/bin/env python3
"""Before/after comparison report for the layout engine.

Renders every example and every layout test case with the current engine and
with a baseline engine checked out from a git revision, scores both with the
*current* quality metrics, and writes a Markdown report with side-by-side
images and a metrics table.

    python3 tools/layout_report.py                      # baseline: main
    python3 tools/layout_report.py --baseline 558353d   # any git revision
    python3 tools/layout_report.py --no-baseline        # current engine only

The baseline engine runs in a separate interpreter from a ``git archive``
extract, so it never mixes with the working-tree modules.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schematic import process_schematic, render_schematic_to_svg  # noqa: E402
from schematic.layout.metrics import evaluate  # noqa: E402
from schematic.serialize import schematic_from_dict, schematic_to_dict  # noqa: E402

DEFAULT_CASES = ["examples/*.txt", "tests/layout_cases/*.txt"]

_BASELINE_RUNNER = r'''
import json, sys, time, traceback
sys.path.insert(0, sys.argv[1])
from schematic import process_schematic, render_schematic_to_svg

def dump(sch):
    return {
        "width": sch.width, "height": sch.height,
        "components": [{
            "id": c.id, "type_name": c.type_name, "kind": c.kind, "value": c.value,
            "x": c.x, "y": c.y, "width": c.width, "height": c.height,
            "pins": [{
                "id": p.id, "name": p.name, "label": p.label, "side": p.side.value,
                "order": p.order, "side_specified": p.side_specified,
                "position": [p.position.x, p.position.y],
                "lead_start": [p.lead_start.x, p.lead_start.y]} for p in c.pins.values()],
        } for c in sch.components.values()],
        "nets": [{"name": n.name, "color": n.color,
                  "connections": [{"component_id": cn.component_id, "pin_name": cn.pin_name,
                                   "pin_id": cn.pin_id} for cn in n.connections]} for n in sch.nets],
        "wires": [[w.start.x, w.start.y, w.end.x, w.end.y, w.net_name, w.color] for w in sch.wires],
        "junctions": [[j.point.x, j.point.y, j.net_name] for j in sch.junctions],
    }

cases = json.load(open(sys.argv[2]))
out = {}
for name, text in cases.items():
    try:
        t0 = time.perf_counter()
        sch = process_schematic(text)
        elapsed = time.perf_counter() - t0
        out[name] = {"svg": render_schematic_to_svg(sch, dark_theme=False),
                     "geometry": dump(sch), "seconds": elapsed}
    except Exception:
        out[name] = {"error": traceback.format_exc()}
json.dump(out, open(sys.argv[3], "w"))
'''


def _collect_cases(patterns: List[str]) -> Dict[str, str]:
    cases: Dict[str, str] = {}
    for pattern in patterns:
        for path in sorted(glob.glob(str(ROOT / pattern))):
            cases[Path(path).stem] = Path(path).read_text(encoding="utf-8")
    return cases


def _run_baseline(rev: str, cases: Dict[str, str]) -> Dict[str, dict]:
    tmp = Path(tempfile.mkdtemp(prefix="schematic-baseline-"))
    try:
        archive = tmp / "src.tar"
        subprocess.run(
            ["git", "-C", str(ROOT), "archive", "--format=tar", "-o", str(archive), rev, "schematic"],
            check=True,
        )
        src = tmp / "src"
        src.mkdir()
        with tarfile.open(archive) as tf:
            tf.extractall(src, filter="data")
        (tmp / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        (tmp / "runner.py").write_text(_BASELINE_RUNNER, encoding="utf-8")
        subprocess.run(
            [sys.executable, str(tmp / "runner.py"), str(src), str(tmp / "cases.json"), str(tmp / "out.json")],
            check=True,
            cwd=str(tmp),
        )
        return json.loads((tmp / "out.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _to_png(svg_path: Path) -> Optional[Path]:
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
    ("Cross", "crossings", "{}"),
    ("Bends", "bends", "{}"),
    ("Jogs", "small_jogs", "{}"),
    ("Near", "near_crossings", "{}"),
    ("Tight", "tight_parallel_pairs", "{}"),
    ("Detour", "detour_ratio", "{:.2f}"),
    ("Parallel", "parallelism_score", "{:.2f}"),
    ("Viol.", "violation_count", "{}"),
]


def _cell(rep: Optional[dict], key: str, fmt: str) -> str:
    if rep is None:
        return "-"
    v = rep.get(key)
    if v is None:
        return "n/a"
    return fmt.format(v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", default="main", help="git revision of the baseline engine (default: main)")
    ap.add_argument("--no-baseline", action="store_true", help="only render the current engine")
    ap.add_argument("--out", default="build/layout-report", help="output directory")
    ap.add_argument("--cases", nargs="*", default=DEFAULT_CASES, help="glob patterns of input files")
    ap.add_argument("--no-png", action="store_true", help="skip PNG rasterisation")
    ap.add_argument("--geometry", action="store_true", help="also write the routed geometry as JSON per case")
    args = ap.parse_args()

    out = (ROOT / args.out) if not os.path.isabs(args.out) else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = _collect_cases(args.cases)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 1

    baseline: Dict[str, dict] = {}
    rev_label = args.baseline
    if not args.no_baseline:
        print(f"running baseline engine from '{args.baseline}' ...", file=sys.stderr)
        baseline = _run_baseline(args.baseline, cases)
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", args.baseline],
                             capture_output=True, text=True).stdout.strip()
        if sha and sha != args.baseline:
            rev_label = f"{args.baseline} ({sha})"

    results: Dict[str, dict] = {}
    for name, text in cases.items():
        entry: dict = {}
        t0 = time.perf_counter()
        sch = process_schematic(text)
        entry["after_seconds"] = time.perf_counter() - t0
        after_rep = evaluate(sch)
        entry["after"] = after_rep.as_dict()
        svg_after = out / f"{name}.after.svg"
        svg_after.write_text(render_schematic_to_svg(sch, dark_theme=False), encoding="utf-8")
        if args.geometry:
            (out / f"{name}.after.json").write_text(json.dumps(schematic_to_dict(sch)), encoding="utf-8")
        if not args.no_png:
            _to_png(svg_after)

        b = baseline.get(name)
        if b and "geometry" in b:
            before_sch = schematic_from_dict(b["geometry"])
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
        lines.append(f"Baseline engine: `{rev_label}` (first value) vs. current engine (second value). "
                     "All drawings are scored with the current metrics (`schematic/layout/metrics.py`).")
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
        timing = f"{t_before:.2f} → {t_after:.2f}" if (baseline and t_before is not None) else f"{t_after:.2f}"
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
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print(f"\nreport written to {out / 'index.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
