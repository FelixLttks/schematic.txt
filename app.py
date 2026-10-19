#!/usr/bin/env python3
"""Schematic.txt - Text-to-Schematic 2D Application.

Usage:
  # Start local web application
  python app.py

  # Or render a file directly to SVG via CLI
  python app.py input.txt -o schematic.svg

  # Print the layout quality report as well (or write it as JSON)
  python app.py input.txt -o schematic.svg --report
  python app.py input.txt --json-report quality.json --effort thorough
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path so 'schematic' and 'web' packages resolve smoothly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schematic import parse_schematic, layout_schematic, render_schematic_to_svg  # noqa: E402
from schematic.layout.config import EFFORT_LEVELS, config_for_effort  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="Schematic.txt: 2D Text-based Schematic Generation Tool"
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Optional input file to render directly to SVG. If omitted, starts the local web app.",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output SVG filename (used when input file is specified).",
    )
    parser.add_argument(
        "--light",
        action="store_true",
        help="Render with the light theme (default: dark).",
    )
    parser.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default="balanced",
        help="Layout effort: quality vs. running time (default: balanced).",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print the layout quality report.",
    )
    parser.add_argument(
        "--json-report",
        default=None,
        metavar="FILE",
        help="Write the layout quality report as JSON.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Web server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=5000,
        help="Web server port (default: 5000)",
    )

    args = parser.parse_args()

    # CLI mode if a file argument is provided
    if args.file:
        input_path = Path(args.file)
        if not input_path.is_file():
            print(f"Error: Input file '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)

        content = input_path.read_text(encoding="utf-8")
        schematic = parse_schematic(content)
        result = layout_schematic(schematic, config_for_effort(args.effort))
        svg_content = render_schematic_to_svg(schematic, dark_theme=not args.light)

        out_path = Path(args.output) if args.output else input_path.with_suffix(".svg")
        out_path.write_text(svg_content, encoding="utf-8")
        print(f"Schematic SVG successfully saved to: {out_path}")
        if args.report:
            print()
            print(result.report.format_table())
        if args.json_report:
            data = result.report.as_dict()
            data["placement_candidate"] = result.strategy
            data["candidates"] = [
                {"placement": name, "estimated_cost": est, "routed_cost": cost} for name, est, cost in result.candidates
            ]
            Path(args.json_report).write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"Quality report saved to: {args.json_report}")
        if result.report.violation_count:
            print(
                f"Warning: {result.report.violation_count} layout violation(s): "
                + ", ".join(f"{k}={v}" for k, v in result.report.violations.items() if v),
                file=sys.stderr,
            )
        return

    # Web mode (default)
    from web.server import run_server

    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
