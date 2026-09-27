"""Schematic.txt - Text-to-Schematic 2D Engine."""

from schematic.models import (
    Component,
    Net,
    NetConnection,
    Pin,
    Point,
    Rect,
    Schematic,
    Side,
    WireJunction,
    WireSegment,
)
from schematic.parser import ParseError, SchematicParser, parse_schematic
from schematic.layout.placer import place_schematic
from schematic.layout.router import route_schematic
from schematic.renderer.svg import render_schematic_to_svg


def process_schematic(text: str) -> Schematic:
    """Parses and jointly optimizes component placement and net routing."""
    schematic = parse_schematic(text)
    place_schematic(schematic)
    return schematic


def generate_schematic_svg(text: str, dark_theme: bool = True) -> str:
    """Converts a text schematic directly into a standalone SVG string."""
    schematic = process_schematic(text)
    return render_schematic_to_svg(schematic, dark_theme=dark_theme)


__all__ = [
    "Component",
    "Net",
    "NetConnection",
    "Pin",
    "Point",
    "Rect",
    "Schematic",
    "Side",
    "WireJunction",
    "WireSegment",
    "ParseError",
    "SchematicParser",
    "parse_schematic",
    "place_schematic",
    "route_schematic",
    "render_schematic_to_svg",
    "process_schematic",
    "generate_schematic_svg",
]
