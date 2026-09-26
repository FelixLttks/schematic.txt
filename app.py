#!/usr/bin/env python3
"""Schematic.txt - Text-to-Schematic 2D Application.

Usage:
  # Start local web application
  python app.py

  # Or render a file directly to SVG via CLI
  python app.py input.txt -o schematic.svg
"""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path so 'schematic' and 'web' packages resolve smoothly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schematic import generate_schematic_svg
from web.server import run_server


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
        svg_content = generate_schematic_svg(content)

        out_path = Path(args.output) if args.output else input_path.with_suffix(".svg")
        out_path.write_text(svg_content, encoding="utf-8")
        print(f"Schematic SVG successfully saved to: {out_path}")
        return

    # Web mode (default)
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
