"""Command-line interface: ``schematic render``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from schematic_txt import __version__
from schematic_txt.errors import SchematicError
from schematic_txt.layout import EFFORT_LEVELS, config_for_effort, layout_schematic
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


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
