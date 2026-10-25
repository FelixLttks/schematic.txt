"""schematic.txt: describe circuits as plain text, get readable schematics.

Typical use::

    from schematic_txt import render

    svg = render("MCU.GND -> Sensor.GND\nMCU.3V3 -> Sensor.VCC")

The steps can also be run one by one (``parse_schematic`` ->
``layout_schematic`` -> ``render_svg``), e.g. to inspect the quality report
that ``layout_schematic`` returns.
"""

from __future__ import annotations

from pathlib import Path

from schematic_txt.errors import ImageError, ParseError, SchematicError
from schematic_txt.layout import (
    LayoutConfig,
    LayoutResult,
    QualityReport,
    config_for_effort,
    layout_schematic,
)
from schematic_txt.models import Schematic
from schematic_txt.parser import parse_schematic
from schematic_txt.svg import render_svg

__version__ = "0.3.0"


def render(
    text: str,
    *,
    theme: str = "light",
    effort: str = "balanced",
    base_dir: str | Path | None = None,
) -> str:
    """Parses, lays out and draws a schematic description in one go.

    ``effort`` trades layout quality for time (``fast``, ``balanced``,
    ``thorough``); ``base_dir`` is where relative part image paths are
    resolved (images are embedded into the SVG).
    """
    schematic = parse_schematic(text)
    layout_schematic(schematic, config_for_effort(effort))
    return render_svg(schematic, theme=theme, base_dir=base_dir)


__all__ = [
    "ImageError",
    "LayoutConfig",
    "LayoutResult",
    "ParseError",
    "QualityReport",
    "Schematic",
    "SchematicError",
    "__version__",
    "config_for_effort",
    "layout_schematic",
    "parse_schematic",
    "render",
    "render_svg",
]
