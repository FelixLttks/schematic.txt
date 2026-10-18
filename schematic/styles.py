"""Net colours shared by the router (``net.color``) and the SVG renderer.

Colours encode the semantic class of a net so that related wires are
recognisable at a glance: ground is grey, supplies are red / orange, and each
bus family gets one hue.
"""

from __future__ import annotations

from typing import Optional, Tuple

# (dark theme, light theme)
_PALETTE = {
    "ground": ("#64748b", "#475569"),
    "battery": ("#f97316", "#ea580c"),
    "power": ("#ef4444", "#dc2626"),
    "analog": ("#ec4899", "#db2777"),
    "pulse": ("#06b6d4", "#0891b2"),
    "sda": ("#38bdf8", "#0284c7"),
    "scl": ("#10b981", "#059669"),
    "mosi": ("#818cf8", "#4f46e5"),
    "miso": ("#c084fc", "#9333ea"),
    "clock": ("#10b981", "#059669"),
    "select": ("#fbbf24", "#d97706"),
    "tx": ("#f59e0b", "#d97706"),
    "rx": ("#2dd4bf", "#0d9488"),
    "control": ("#a78bfa", "#7c3aed"),
    "signal": ("#38bdf8", "#0284c7"),
}


def _category(name: str) -> str:
    nu = name.upper()
    if any(k in nu for k in ("GND", "VSS", "NEG", "RETURN")):
        return "ground"
    if any(k in nu for k in ("BAT", "RAW", "HV", "36V", "48V")):
        return "battery"
    if any(k in nu for k in ("VCC", "VDD", "VIN", "VOUT", "PWR", "3V3", "5V", "12V", "24V", "VBUS")):
        return "power"
    if any(k in nu for k in ("ANALOG", "ADC", "SENSE", "DIFF")):
        return "analog"
    if any(k in nu for k in ("INT", "IRQ", "PWM", "PULSE")):
        return "pulse"
    if "SDA" in nu:
        return "sda"
    if "SCL" in nu:
        return "scl"
    if "MOSI" in nu:
        return "mosi"
    if "MISO" in nu:
        return "miso"
    if "SCK" in nu or "CLK" in nu:
        return "clock"
    if "CS" in nu or "SS" in nu:
        return "select"
    if "TX" in nu:
        return "tx"
    if "RX" in nu:
        return "rx"
    if any(k in nu for k in ("EN", "ENABLE", "RST", "RESET", "CTRL")):
        return "control"
    return "signal"


def net_color(name: str, dark_theme: bool = True, category: Optional[str] = None) -> str:
    pair: Tuple[str, str] = _PALETTE[category or _category(name)]
    return pair[0] if dark_theme else pair[1]
