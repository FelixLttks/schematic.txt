"""Documented naming conventions for electrical roles.

The DSL leans on widely used electrical naming conventions: ``GND``/``VSS``/
``NEG`` for returns and ``VCC``/``VDD``/``3V3``/``5V``/``VIN``/``VBUS`` …
for supplies.  These conventions are applied uniformly to *every* schematic;
they are the only place where net or pin names may influence geometry, and
they are intentionally centralised here instead of being sniffed ad hoc in the
layout, routing or rendering code.

Nothing in this module knows about individual components or example files.
Role detection can later be overridden explicitly in the DSL (for example a
``[role=power]`` attribute on a net) without touching the consumers.
"""

from __future__ import annotations

GROUND_TOKENS = ("GND", "VSS", "NEG", "RETURN")
POWER_TOKENS = (
    "VCC",
    "VDD",
    "VIN",
    "VBUS",
    "VOUT",
    "3V3",
    "3.3V",
    "5V",
    "12V",
    "24V",
    "36V",
    "48V",
    "PWR",
    "POWER",
)


def is_ground(name: str) -> bool:
    """True for net/pin names that denote a return or ground path."""
    upper = name.upper()
    return any(token in upper for token in GROUND_TOKENS)


def is_power(name: str) -> bool:
    """True for net/pin names that denote a regulated or raw supply."""
    upper = name.upper()
    return any(token in upper for token in POWER_TOKENS)


def is_supply_like(name: str) -> bool:
    """True for any supply-side net/pin name."""
    return is_ground(name) or is_power(name)
