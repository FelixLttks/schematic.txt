"""Schematic.txt - Text-to-Schematic 2D Engine."""

from typing import Optional

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
from schematic.layout.config import LayoutConfig
from schematic.layout.engine import LayoutResult, layout_schematic
from schematic.layout.metrics import QualityReport
from schematic.renderer.svg import render_schematic_to_svg


def process_schematic(text: str, config: Optional[LayoutConfig] = None) -> Schematic:
    """Parses text DSL / YAML, places all components and routes all nets."""
    schematic = parse_schematic(text)
    layout_schematic(schematic, config)
    return schematic


def generate_schematic_svg(text: str, dark_theme: bool = True, config: Optional[LayoutConfig] = None) -> str:
    """Converts a text schematic directly into a standalone SVG string."""
    schematic = process_schematic(text, config)
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
    "render_schematic_to_svg",
    "process_schematic",
    "generate_schematic_svg",
    "layout_schematic",
    "LayoutConfig",
    "LayoutResult",
    "QualityReport",
]
