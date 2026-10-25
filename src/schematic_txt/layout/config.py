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
    crossing: float = 12.0  # two different nets crossing
    bend: float = 4.0  # a wire turning by 90 degrees
    length: float = 0.4  # per grid step of wire
    junction: float = 0.5  # per branch point (T) of a net
    # Estimate only (the router makes these impossible or resolves them):
    overlap: float = 12.0  # estimated contact of two nets (same line, wire ending on
    # another): the router must cross or detour
    obstacle: float = 40.0  # estimated route through a component body
    # Router and estimate:
    near: float = 0.5  # per step directly beside a wire of an unrelated net (20 px)
    stray: float = 0.4  # per step a bundle member does not run beside a routed mate
    # (bundle: signal nets joining exactly the same parts)
    text: float = 2.0  # per step through a text zone (pin tag, part name)
    pin_front: float = 2.0  # passing two steps in front of another net's pin (would box it in)
    # Placement only:
    perimeter: float = 0.2  # per grid step of drawing width + height (compactness)
    rotation: float = 30.0  # image part turned by 90 degrees (180 is never used):
    # photos and their printed labels read best upright
    # Distribution nets (many terminals, e.g. supply and ground) are drawn as
    # wires like any other net, but their bends and length weigh less: they
    # must reach everything, the signals must stay clean.
    rail_factor: float = 0.5
    rail_min_terminals: int = 5


@dataclass
class LayoutConfig:
    weights: Weights = field(default_factory=Weights)
    clearance: int = 2  # free grid lines between footprints (min.)
    block_gap: int = 4  # between footprints of different functional blocks (min.)
    group_gap: int = 6  # between unconnected sub-circuits
    margin: int = 2  # empty border (grid)
    # Net tags: ground nets with at least this many terminals, and positive
    # supplies with at least that many, get a tag at every pin instead of a wire.
    net_tags: bool = True
    ground_tag_min_terminals: int = 3
    supply_tag_min_terminals: int = 5
    block_restarts: int = 3  # annealing runs per block layout (the best is kept)
    block_compactness: float = 2.0  # factor on the perimeter weight inside a block
    moves_per_part: int = 300  # annealing moves per component of a block
    min_block_moves: int = 2000  # at least this many per run (small blocks are cheap)
    start_temperature: float = 0.3  # x mean uphill step of the start layout
    arrange_rounds: int = 3  # rounds of moving single blocks after the first arrangement
    arrange_trials: int = 3  # independent arrangements (seeds), all routed, the best kept
    reroute_passes: int = 3  # rip-up-and-reroute passes
    refine_rounds: int = 0  # local search on the routed drawing (0: off)
    refine_batch: int = 8  # changes routed for real per refinement round
    refine_patience: int = 3  # rounds without improvement before it stops
    net_labels: bool = True
    seed: int = 1


def config_for_effort(effort: str = "balanced") -> LayoutConfig:
    """Quality / running-time presets.

    The layout is hierarchical (see ``engine``): blocks of a few parts are
    annealed on their own, then arranged as units.  Effort buys more
    annealing runs per block, more rounds of rearranging blocks and, for
    ``thorough``, a refinement in which candidate changes are routed for
    real.

    * ``fast``: two short runs per block, two trials, one rearranging round
      -- live preview;
    * ``balanced`` (default): three runs per block, three trials, three rounds;
    * ``thorough``: six longer runs per block, eight trials, five rounds,
      refinement.
    """
    cfg = LayoutConfig()
    if effort == "fast":
        cfg.block_restarts = 2
        cfg.moves_per_part = 150
        cfg.min_block_moves = 800
        cfg.arrange_rounds = 1
        cfg.arrange_trials = 2
        cfg.reroute_passes = 2
    elif effort == "thorough":
        cfg.block_restarts = 6
        cfg.moves_per_part = 800
        cfg.min_block_moves = 5000
        cfg.arrange_rounds = 5
        cfg.arrange_trials = 8
        cfg.refine_rounds = 20
        cfg.reroute_passes = 4
    elif effort != "balanced":
        raise ValueError(f"unknown effort level {effort!r}; expected one of {', '.join(EFFORT_LEVELS)}")
    return cfg


DEFAULT_CONFIG = LayoutConfig()
