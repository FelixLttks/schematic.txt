"""Layout engine: netlist -> placement -> routing -> labels -> report.

See ``docs/layout-engine.md`` for the model and the algorithms.
"""

from schematic_txt.layout.config import EFFORT_LEVELS, LayoutConfig, Weights, config_for_effort
from schematic_txt.layout.engine import LayoutResult, layout_schematic
from schematic_txt.layout.metrics import QualityReport

__all__ = [
    "EFFORT_LEVELS",
    "LayoutConfig",
    "LayoutResult",
    "QualityReport",
    "Weights",
    "config_for_effort",
    "layout_schematic",
]
