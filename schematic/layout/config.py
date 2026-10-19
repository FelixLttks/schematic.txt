"""Tunable parameters of the layout engine.

There is exactly **one** cost model.  The same weights price a placement
(through the route estimate), steer the router (A* step costs) and score the
final drawing, so the three stages always agree on what "better" means.

Units: with the defaults a bend is worth 10 grid steps (200 px) of extra
wire and a crossing 30 grid steps (600 px).  Long wires are hard to follow
too, so length is not negligible: a wire across a drawing of 100 grid steps
costs as much as four bends.  The ratios encode the priorities
of a wiring document: a wire that can be traced at a glance (no crossings,
few bends) matters far more than a short or compact one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EFFORT_LEVELS = ("fast", "balanced", "thorough")


@dataclass
class Weights:
    crossing: float = 12.0      # two different nets crossing
    bend: float = 4.0           # a wire turning by 90 degrees
    length: float = 0.4         # per grid step of wire
    junction: float = 0.5       # per branch point (T) of a net
    # Estimate only (the router makes these impossible or resolves them):
    overlap: float = 12.0       # estimated contact of two nets (same line, wire ending on
                                # another): the router must cross or detour
    obstacle: float = 40.0      # estimated route through a component body
    # Router and estimate:
    near: float = 0.5           # per step directly beside a wire of an unrelated net (20 px)
    stray: float = 0.4          # per step a bundle member does not run beside a routed mate
                                # (bundle: signal nets joining exactly the same parts)
    text: float = 2.0           # per step through a text zone (pin tag, part name)
    pin_front: float = 2.0      # passing two steps in front of another net's pin (would box it in)
    # Placement only:
    perimeter: float = 0.2      # per grid step of drawing width + height (compactness)
    rotation: float = 6.0       # image part turned by 90 degrees (180 is never used):
                                # photos and their printed labels read best upright
    # Distribution nets (many terminals, e.g. supply and ground) are drawn as
    # wires like any other net, but their bends and length weigh less: they
    # must reach everything, the signals must stay clean.
    rail_factor: float = 0.5
    rail_min_terminals: int = 5


@dataclass
class LayoutConfig:
    weights: Weights = field(default_factory=Weights)
    clearance: int = 2              # free grid lines between footprints (min.)
    group_gap: int = 6              # between unconnected sub-circuits
    margin: int = 2                 # empty border (grid)
    restarts: int = 6               # independent annealing runs (different seeds, run in parallel)
    moves_per_part: int = 1000      # annealing moves per component and run
    start_temperature: float = 0.3  # x mean uphill step of the start layout
    routed_candidates: int = 3      # best placements that are routed for real
    reroute_passes: int = 3         # rip-up-and-reroute passes
    net_labels: bool = True
    workers: int = 0                # parallel processes for independent runs (0: CPU count - 1)
    seed: int = 1


def config_for_effort(effort: str = "balanced") -> LayoutConfig:
    """Quality / running-time presets.

    Measured on the examples and test cases: the annealing effort is the
    main lever (150 -> 1000 moves per part: -18 % cost, 1000 -> 2500: -4 %),
    and the best of several independent runs removes most of the scatter
    between seeds.  Runs execute in parallel processes.

    * ``fast``: 2 runs x 300 moves -- live preview;
    * ``balanced`` (default): 6 runs x 1000 moves, best 3 routed;
    * ``thorough``: 14 runs x 2500 moves, best 4 routed (minutes).
    """
    cfg = LayoutConfig()
    if effort == "fast":
        cfg.restarts = 2
        cfg.moves_per_part = 300
        cfg.routed_candidates = 1
        cfg.reroute_passes = 2
    elif effort == "thorough":
        cfg.restarts = 14
        cfg.moves_per_part = 2500
        cfg.routed_candidates = 4
        cfg.reroute_passes = 4
    elif effort != "balanced":
        raise ValueError(f"unknown effort level {effort!r}; expected one of {', '.join(EFFORT_LEVELS)}")
    return cfg


DEFAULT_CONFIG = LayoutConfig()
