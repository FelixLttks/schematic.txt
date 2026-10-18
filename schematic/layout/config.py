"""Tunable parameters of the layout engine.

Every weight of the placement objective, the routing cost model and the
readability score lives here, with its unit.  Costs are expressed in "grid
steps": one unit equals the cost of 20 px of straight wire, so a weight of 6
for a bend means "a bend is worth 120 px of extra wire".

Priority order encoded by the defaults (see docs/layout-engine.md):
connectivity (hard) > readability > grouping > crossings > bends > spacing
> stability > wire length > compactness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from schematic.layout.metrics import ScoreWeights


@dataclass
class SpacingConfig:
    """Geometric clearances in pixels (multiples of the 20 px grid)."""

    margin: float = 80.0               # empty border around the drawing
    min_h_gap: float = 100.0           # minimum gap between side-by-side components
    min_v_gap: float = 60.0            # minimum gap between stacked components
    track_gap: float = 20.0            # extra corridor width per wire that must jog in it
    satellite_offset: float = 60.0     # distance of a pull-up / filter part from its pin
    isolated_gap: float = 60.0         # spacing of unconnected parts in their row
    group_gap: float = 160.0           # spacing between unconnected sub-circuits


@dataclass
class PlacementWeights:
    """Weights of the estimated-routing objective used by placement search."""

    bend: float = 5.0                  # per estimated bend of a connection
    length: float = 0.12               # per grid step of estimated wire length
    crossing: float = 9.0              # per estimated crossing of two connections
    obstacle: float = 4.0              # per body an estimated route cuts through (plus its detour)
    near_miss: float = 6.0             # facing pins offset by one grid step (tiny jog)
    misfacing: float = 12.0            # pin faces away from its peer (wrap-around)
    rail_factor: float = 0.25          # multiplier for connections of power rails
    area: float = 0.004                # per grid step^2 of the bounding box
    aspect: float = 1.0                # per unit of aspect ratio above `max_aspect`
    max_aspect: float = 4.0
    stability: float = 0.02            # per grid step moved away from the initial embedding


@dataclass
class RoutingWeights:
    """Cost model of the grid router (A*), per grid step unless noted."""

    step: float = 1.0
    bend: float = 10.0                 # per bend
    jog: float = 16.0                  # bend following another bend within one step
    crossing: float = 8.0              # crossing an unrelated net
    crossing_related: float = 20.0     # crossing a member of the same bundle / pair
    near_foreign: float = 1.5          # running directly beside an unrelated wire
    bundle_stray: float = 0.35         # bundle member not running beside a routed mate
    pin_corridor: float = 3.0          # passing just in front of another net's pin
    hug: float = 0.4                   # running directly along a component outline
    label: float = 1.5                 # running through the name / value text of a part
    junction: float = 1.0              # every new T-junction
    rail_bend_factor: float = 1.0      # power/ground rails value straightness just like signals


@dataclass
class LayoutConfig:
    spacing: SpacingConfig = field(default_factory=SpacingConfig)
    placement: PlacementWeights = field(default_factory=PlacementWeights)
    routing: RoutingWeights = field(default_factory=RoutingWeights)
    score: ScoreWeights = field(default_factory=ScoreWeights)

    # Placement candidates (seed strategy, run local search).  Each one is
    # placed, routed and scored with the real quality metrics; the best wins.
    candidates: Tuple[Tuple[str, bool], ...] = (("layered", False), ("layered", True), ("stress", True))
    stress_iterations: int = 60        # global embedding iterations
    search_sweeps: int = 4             # placement local-search sweeps (pins re-configured between)
    search_passes: int = 3             # improvement passes over all parts within a sweep
    search_shifts: Tuple[float, ...] = (20.0, 60.0, 140.0)  # translation moves (px)
    search_partners: int = 3           # relocation targets: most strongly wired partners
    orientation_candidates: int = 2    # extra routed candidates with a forced 2-pin part orientation
    orientation_reroute_margin: float = 2.0  # first-pass gap still worth a rip-up comparison
    orientation_accept_margin: float = 0.0   # minimum final score gain for a local orientation change
    reroute_passes: int = 2            # rip-up-and-reroute passes on the winning candidate
    spacing_retries: int = 2           # re-runs with wider corridors if pins stay unconnected
    net_labels: bool = True            # place net-name labels on wires
    outer_rail_pins: bool = True       # supply pins of stacked parts face away from the bus


DEFAULT_CONFIG = LayoutConfig()

EFFORT_LEVELS = ("fast", "balanced", "thorough")


def config_for_effort(effort: str = "balanced") -> LayoutConfig:
    """Preset trade-offs between layout quality and running time.

    * ``fast``: one placement candidate, shorter search, one rip-up pass --
      for live previews of large drawings.
    * ``balanced`` (default): three placement candidates, each routed and
      scored; the winner gets two rip-up-and-reroute passes.
    * ``thorough``: additional stress candidate without search, longer search
      and more rip-up passes.
    """
    cfg = LayoutConfig()
    if effort == "fast":
        cfg.candidates = (("layered", True),)
        cfg.search_sweeps = 2
        cfg.search_passes = 2
        cfg.search_shifts = (20.0, 60.0)
        cfg.search_partners = 2
        cfg.orientation_candidates = 1
        cfg.reroute_passes = 1
    elif effort == "thorough":
        cfg.candidates = (("layered", False), ("layered", True), ("stress", False), ("stress", True))
        cfg.search_sweeps = 6
        cfg.orientation_candidates = 4
        cfg.reroute_passes = 4
    elif effort != "balanced":
        raise ValueError(f"unknown effort level {effort!r}; expected one of {', '.join(EFFORT_LEVELS)}")
    return cfg
