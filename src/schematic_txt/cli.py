"""Command-line interface: ``schematic render | md``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from schematic_txt import __version__
from schematic_txt.errors import SchematicError
from schematic_txt.layout import EFFORT_LEVELS, config_for_effort, layout_schematic
from schematic_txt.markdown import DEFAULT_OUT_DIR, markdown_files, process_file
from schematic_txt.parser import parse_schematic
from schematic_txt.svg import THEMES, render_svg


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.run(args)
    except KeyboardInterrupt:
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schematic",
        description="Describe circuits as plain text, get readable schematics as SVG.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(title="commands", required=True, metavar="COMMAND")

    render = commands.add_parser("render", help="render a schematic file to SVG")
    render.add_argument("input", type=Path, help="schematic description (.txt), or - for stdin")
    render.add_argument("-o", "--output", type=Path, help="output SVG (default: input with .svg, or stdout)")
    render.add_argument("--theme", choices=THEMES, default="light", help="color theme (default: light)")
    _add_effort(render)
    render.add_argument("--report", action="store_true", help="print the layout quality report")
    render.add_argument("--json-report", type=Path, metavar="FILE", help="write the quality report as JSON")
    render.set_defaults(run=_render)

    md = commands.add_parser(
        "md",
        help="render ```schematic blocks in Markdown files",
        description="Render every ```schematic block in the given Markdown files (or all *.md "
        "files below the given directories) to SVG and show the image in place of the code.",
    )
    md.add_argument("paths", nargs="*", type=Path, default=[Path(".")], help="files or directories")
    md.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"image directory, relative to each Markdown file (default: {DEFAULT_OUT_DIR})",
    )
    _add_effort(md)
    md.add_argument("--force", action="store_true", help="render every block, even if its image exists")
    md.add_argument("--check", action="store_true", help="change nothing; exit 1 if anything is out of date")
    md.add_argument("--watch", action="store_true", help="keep running and re-render on every change")
    md.set_defaults(run=_markdown)

    return parser


def _add_effort(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default="balanced",
        help="layout quality vs. time (default: balanced)",
    )


def _render(args: argparse.Namespace) -> int:
    from_stdin = str(args.input) == "-"
    try:
        text = sys.stdin.read() if from_stdin else args.input.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail(f"cannot read {args.input}: {exc.strerror}")
    base_dir = Path.cwd() if from_stdin else args.input.parent
    try:
        schematic = parse_schematic(text)
        result = layout_schematic(schematic, config_for_effort(args.effort))
        svg = render_svg(schematic, theme=args.theme, base_dir=base_dir)
    except SchematicError as exc:
        return _fail(f"{args.input}: {exc}")

    output = args.output or (None if from_stdin else args.input.with_suffix(".svg"))
    if output is None or str(output) == "-":
        sys.stdout.write(svg)
    else:
        output.write_text(svg, encoding="utf-8")
        print(f"wrote {output}", file=sys.stderr)

    report = result.report
    if args.report:
        print(report.format_table(), file=sys.stderr)
    if args.json_report:
        args.json_report.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    if report.violation_count:
        found = ", ".join(f"{k}={v}" for k, v in report.violations.items() if v)
        print(f"warning: the layout has {report.violation_count} violation(s): {found}", file=sys.stderr)
    return 0


def _markdown(args: argparse.Namespace) -> int:
    missing = [p for p in args.paths if not p.exists()]
    if missing:
        return _fail(f"no such file or directory: {missing[0]}")
    if args.watch:
        if args.check:
            return _fail("--watch and --check cannot be combined")
        return _watch(args)
    return _markdown_once(args)


def _markdown_once(args: argparse.Namespace) -> int:
    failed = outdated = False
    for path in markdown_files(args.paths):
        outcome = process_file(
            path, out_dir=args.out_dir, effort=args.effort, force=args.force, check=args.check
        )
        for error in outcome.errors:
            print(f"error: {error}", file=sys.stderr)
        failed |= bool(outcome.errors)
        if not outcome.changed:
            continue
        outdated = True
        verb = "out of date" if args.check else "updated"
        details = [f"{len(outcome.rendered)} rendered"] if outcome.rendered else []
        details += [f"{len(outcome.removed)} removed"] if outcome.removed else []
        print(f"{path}: {verb}" + (f" ({', '.join(details)})" if details else ""), file=sys.stderr)
    if args.check and outdated:
        print("run `schematic md` to update", file=sys.stderr)
    return 1 if failed or (args.check and outdated) else 0


def _watch(args: argparse.Namespace) -> int:
    print("watching for changes, Ctrl+C to stop", file=sys.stderr)
    seen: dict[Path, int] = {}
    while True:
        files = list(markdown_files(args.paths))
        stamps = {f: f.stat().st_mtime_ns for f in files if f.exists()}
        changed = [f for f, stamp in stamps.items() if seen.get(f) != stamp]
        if changed:
            _markdown_once(argparse.Namespace(**{**vars(args), "paths": changed}))
            # our own write to the file must not count as a change
            stamps.update({f: f.stat().st_mtime_ns for f in changed if f.exists()})
        seen = stamps
        time.sleep(0.5)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
