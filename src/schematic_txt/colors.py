"""Net colours shared by the router (``net.color``) and the SVG renderer.

The *only* place where a net name is interpreted: ground nets are drawn black
(slate on the dark theme) and positive supply nets red.  Every other net gets
a stable colour from a palette, chosen from the net's name hash.

The same classification tells the layout which nets are supplies
(``supply_kind``): a supply with many terminals is drawn as a tag at every pin
instead of a wire (see ``layout.structure``).  Structure alone cannot tell a
supply from a bus -- both join many parts -- so this is the one decision
the layout takes from a name, as every schematic editor does with its power
symbols.
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
# Words that may follow the supply word without turning the name into a
# signal: ``5V_RAW``, ``VBAT_IN``.  Anything else after it (``PWR_EN``,
# ``VIN_SENSE``, ``3V3_EN``) names a signal *about* the supply.
_SUPPLY_QUALIFIERS = {"RAW", "IN", "OUT", "MAIN", "SW", "RAIL", "BUS", "AUX", "REG", "HV", "LV"}


def _is_positive(toks: list[str]) -> bool:
    """The name ends in a supply word or a voltage, optionally followed by
    qualifiers only: ``VCC``, ``DISP_VCC``, ``DCDC_IN_POS``, ``5V_RAW``."""
    rest = list(toks)
    while rest and rest[-1] in _SUPPLY_QUALIFIERS:
        rest.pop()
    return bool(rest) and (rest[-1] in _POSITIVE_TOKENS or bool(_VOLTAGE.match(rest[-1])))


def _category(name: str) -> str:
    toks = [t for t in _TOKEN.split(name.upper()) if t]
    if any(t in _GROUND_TOKENS or t.endswith("GND") or t.startswith("GND") for t in toks):
        return "ground"
    if _is_positive(toks):
        return "positive"
    return "signal"


def supply_kind(name: str) -> str | None:
    """``"ground"``, ``"positive"`` or ``None`` (a signal), judged by the net
    name.  Stricter than the colour: ``LIGHT_NEG`` (a switched low side) may
    be drawn like ground but is not ground."""
    cat = _category(name)
    if cat == "ground":
        toks = [t for t in _TOKEN.split(name.upper()) if t]
        if not any(t in _GROUND_TOKENS - {"NEG"} or t.endswith("GND") or t.startswith("GND") for t in toks):
            return None
    return None if cat == "signal" else cat


def net_color(name: str, dark_theme: bool = True, category: str | None = None) -> str:
    cat = category or _category(name)
    if cat == "ground":
        pair: tuple[str, str] = _GROUND
    elif cat in ("positive", "power"):
        pair = _POSITIVE
    else:
        pair = _PALETTE[zlib.crc32(name.encode("utf-8")) % len(_PALETTE)]
    return pair[0] if dark_theme else pair[1]
