"""Net colours shared by the router (``net.color``) and the SVG renderer.

The *only* place where a net name is interpreted: ground nets are drawn black
(slate on the dark theme) and positive supply nets red.  Every other net gets
a stable colour from a palette, chosen from the net's name hash -- colours
never influence placement or routing.
"""

from __future__ import annotations

import re
import zlib

# (dark theme, light theme)
_GROUND = ("#94a3b8", "#111827")
_POSITIVE = ("#ef4444", "#dc2626")
_PALETTE = (
    ("#38bdf8", "#0284c7"),
    ("#10b981", "#059669"),
    ("#f59e0b", "#d97706"),
    ("#818cf8", "#4f46e5"),
    ("#ec4899", "#db2777"),
    ("#2dd4bf", "#0d9488"),
    ("#c084fc", "#9333ea"),
    ("#f97316", "#ea580c"),
    ("#a3e635", "#65a30d"),
    ("#06b6d4", "#0891b2"),
)

_TOKEN = re.compile(r"[\s_\-\.\/\(\)\[\]:]+")
_GROUND_TOKENS = {"GND", "GROUND", "VSS", "AGND", "DGND", "PGND", "0V", "NEG"}
_POSITIVE_TOKENS = {
    "VCC",
    "VDD",
    "VIN",
    "VBUS",
    "VBAT",
    "VBATT",
    "VSYS",
    "VOUT",
    "BAT",
    "BATT",
    "POS",
    "V+",
    "+",
    "PWR",
}
_VOLTAGE = re.compile(r"^\+?\d+(\.\d+)?V\d*$|^\d+V\d+$")


def _category(name: str) -> str:
    toks = [t for t in _TOKEN.split(name.upper()) if t]
    if any(t in _GROUND_TOKENS or t.endswith("GND") or t.startswith("GND") for t in toks):
        return "ground"
    if any(t in _POSITIVE_TOKENS or _VOLTAGE.match(t) for t in toks):
        return "positive"
    return "signal"


def net_color(name: str, dark_theme: bool = True, category: str | None = None) -> str:
    cat = category or _category(name)
    if cat == "ground":
        pair: tuple[str, str] = _GROUND
    elif cat in ("positive", "power"):
        pair = _POSITIVE
    else:
        pair = _PALETTE[zlib.crc32(name.encode("utf-8")) % len(_PALETTE)]
    return pair[0] if dark_theme else pair[1]
