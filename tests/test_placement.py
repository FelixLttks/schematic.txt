"""Placement state, incremental cost and invariants."""

import random
from pathlib import Path

import pytest

from schematic_txt import parse_schematic
from schematic_txt.layout.config import config_for_effort
from schematic_txt.layout.netlist import build_netlist, merge_shared_pin_nets
from schematic_txt.layout.placement import Layout, PartModel, Placer, construction_order, place_group
from schematic_txt.models import Side

ROOT = Path(__file__).resolve().parent.parent


def _setup(path):
    sch = parse_schematic((ROOT / path).read_text(encoding="utf-8"))
    cfg = config_for_effort("fast")
    merge_shared_pin_nets(sch)
    nl = build_netlist(sch, cfg.weights)
    models = {c: PartModel(x) for c, x in sch.components.items()}
    parts = nl.groups(list(sch.components))[0]
    return sch, cfg, nl, models, parts


@pytest.mark.parametrize(
    "path",
    [
        "examples/esp32_custom_part.txt",
        "tests/cases/05_i2c_bus.txt",
        "tests/cases/03_dense_network.txt",
    ],
)
def test_incremental_total_equals_full_recompute(path):
    """The annealing trusts the incrementally updated cost; it must
    never drift from a full recomputation, for any kind of move."""
    _, cfg, nl, models, parts = _setup(path)
    layout = Layout(models, nl, cfg, parts)
    placer = Placer(layout, random.Random(7))
    placer.analytic(construction_order(parts, nl), 6.0, 3.0)
    rng = random.Random(3)
    checked = 0
    for _ in range(400):
        mv = placer._random_move()
        if not mv:
            continue
        res = layout.apply(mv[0], sides=mv[1])
        if res is None:
            continue
        _, token = res
        if rng.random() < 0.5:
            layout.revert(token)
        assert layout.total == pytest.approx(layout.recompute(), abs=1e-6)
        checked += 1
    assert checked > 100


def test_incremental_total_with_blocks_and_escape():
    """Block gaps and the way out of a block are part of the cost the
    annealing trusts: also checked against a full recomputation."""
    _, cfg, nl, models, parts = _setup("tests/cases/08_clusters.txt")
    block_of = {p: (0 if p in ("Display", "Encoder") else 1) for p in parts}
    inner = [p for p in parts if block_of[p] == 1]
    escape = {
        n: Side.RIGHT
        for p in inner
        for n in nl.part_nets[p]
        if any(block_of[q] == 0 for q in nl.nets[n].parts)
    }
    for layout in (
        Layout(models, nl, cfg, parts, block_of=block_of),
        Layout(models, nl, cfg, inner, escape=escape),
    ):
        placer = Placer(layout, random.Random(7))
        placer.analytic(construction_order(layout.parts, nl), 6.0, 3.0)
        rng = random.Random(3)
        for _ in range(300):
            mv = placer._random_move()
            if not mv:
                continue
            res = layout.apply(mv[0], sides=mv[1])
            if res is None:
                continue
            if rng.random() < 0.5:
                layout.revert(res[1])
            assert layout.total == pytest.approx(layout.recompute(), abs=1e-6)


def test_placed_parts_keep_their_clearance():
    for seed in (5, 6):
        _, cfg, nl, models, parts = _setup("examples/esp32_sensors_bus.txt")
        cfg.moves_per_part = 60
        layout = place_group(parts, models, nl, cfg, seed)
        for p in parts:
            assert layout.legal(p), f"{p} overlaps (seed {seed})"


def test_free_pin_sides_are_variables():
    _, cfg, nl, models, parts = _setup("tests/cases/01_linear_chain.txt")
    layout = Layout(models, nl, cfg, parts)
    Placer(layout, random.Random(1)).analytic(construction_order(parts, nl), 6.0, 3.0)
    box = next(p for p in parts if models[p].free)
    pid = models[box].free[0]
    before = layout.sides_of(box)[pid]
    flipped = Side.RIGHT if before == Side.LEFT else Side.LEFT
    res = layout.apply({}, check=False, sides={box: {pid: flipped}})
    assert res is not None
    assert layout.sides_of(box)[pid] == flipped
    layout.revert(res[1])
    assert layout.sides_of(box)[pid] == before
