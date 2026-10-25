"""Structure: net tags for supplies, hub, functional blocks from the wiring,
and how the layout shows them."""

from pathlib import Path

import pytest

from schematic_txt import config_for_effort, layout_schematic, parse_schematic, render_svg
from schematic_txt.colors import supply_kind
from schematic_txt.layout.config import Weights
from schematic_txt.layout.engine import _column_positions
from schematic_txt.layout.netlist import build_netlist, merge_shared_pin_nets
from schematic_txt.layout.structure import analyse

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "cases"


def _analyse(text):
    sch = parse_schematic(text)
    merge_shared_pin_nets(sch)
    nl = build_netlist(sch, Weights())
    st = analyse(list(sch.components), nl)
    tags = {nl.nets[i].name for i in st.symbol_nets}
    blocks = [set(b) for bs in st.blocks for b in bs]
    return st, tags, blocks


@pytest.mark.parametrize(
    "name, kind",
    [
        ("GND", "ground"),
        ("PGND", "ground"),
        ("3V3", "positive"),
        ("5V", "positive"),
        ("VCC", "positive"),
        ("VOUT_5V", "positive"),
        ("DISP_VCC", "positive"),
        ("DCDC_IN_POS", "positive"),
        ("5V_RAW", "positive"),
        ("PWR", "positive"),
        ("PWR_EN", None),  # a signal about the supply, not the supply
        ("VIN_SENSE", None),
        ("BAT_ADC", None),
        ("3V3_EN", None),
        ("VBUS_DET", None),
        ("LIGHT_NEG", None),  # a switched low side, not ground
        ("I2C_SDA", None),
        ("SPEED_SIGNAL", None),
    ],
)
def test_supply_kind(name, kind):
    assert supply_kind(name) == kind


def test_hub_and_blocks_come_from_the_wiring():
    _, tags, blocks = _analyse((CASES / "14_hub_blocks.txt").read_text())
    assert tags == {"GND", "3V3", "5V"}
    assert {"MCU"} in blocks
    assert {"Pump", "Q1", "Rgate", "Rpd", "D1"} in blocks
    assert {"RS485", "Rterm", "Port"} in blocks
    assert {"Rain", "Rrain", "Crain"} in blocks
    assert {"Battery", "Gauge", "NTC"} in blocks
    assert {"Fuse", "DCDC", "Switch"} in blocks


def test_grouping_ignores_part_names():
    text = (CASES / "14_hub_blocks.txt").read_text()
    names = ["Pump", "Rgate", "RS485", "Port", "Battery", "Gauge", "MCU"]
    renamed = text
    for i, n in enumerate(names):
        renamed = renamed.replace(n, f"P{i}x")
    _, _, a = _analyse(text)
    _, _, b = _analyse(renamed)
    assert sorted(len(x) for x in a) == sorted(len(x) for x in b)
    assert {"P0x", "Q1", "P1x", "Rpd", "D1"} in b


def test_bus_and_local_supply_stay_wires():
    # an I2C bus with six terminals is not a supply
    _, tags, _ = _analyse((CASES / "05_i2c_bus.txt").read_text())
    assert "I2C_SDA" not in tags and "I2C_SCL" not in tags
    # a converter output with four terminals inside one stage stays a wire
    _, tags, _ = _analyse((CASES / "10_long_distance.txt").read_text())
    assert tags == {"GND"}


def test_small_island_is_one_block():
    _, _, blocks = _analyse((CASES / "01_linear_chain.txt").read_text())
    assert len(blocks) == 1


@pytest.fixture(scope="module")
def hub_layout():
    sch = parse_schematic((CASES / "14_hub_blocks.txt").read_text())
    cfg = config_for_effort("fast")
    res = layout_schematic(sch, cfg)
    return sch, res, cfg


def test_tags_replace_supply_wires(hub_layout):
    sch, res, _ = hub_layout
    assert res.report.violation_count == 0, res.report.violations
    assert not [w for w in sch.wires if w.net_name in ("GND", "3V3", "5V")]
    tagged = {(t.component_id, t.pin_id) for t in sch.net_tags if t.net_name == "GND"}
    assert ("MCU", "GND") in tagged and ("Q1", "S") in tagged
    assert res.report.net_tags == len(sch.net_tags) == 24
    svg = render_svg(sch)
    assert svg.count('class="net-tag"') == 24


def test_blocks_keep_their_distance(hub_layout):
    sch, res, cfg = hub_layout
    block_of = {p: i for i, b in enumerate(res.blocks) for p in b}
    comps = list(sch.components.values())
    gap = cfg.block_gap * 20.0
    for i, a in enumerate(comps):
        for b in comps[i + 1 :]:
            if block_of[a.id] == block_of[b.id]:
                continue
            dx = max(b.x - (a.x + a.width), a.x - (b.x + b.width))
            dy = max(b.y - (a.y + a.height), a.y - (b.y + b.height))
            assert max(dx, dy) >= gap, f"{a.id} and {b.id} too close"


def test_blocks_sit_on_the_side_of_their_pins(hub_layout):
    sch, _, _ = hub_layout
    mcu = sch.components["MCU"]
    # GPIO15 (pump) and the SPI pins are on the right of the MCU, the UART
    # to the RS485 transceiver on the left
    assert sch.components["Rgate"].x > mcu.x + mcu.width
    assert sch.components["SD"].x > mcu.x + mcu.width
    assert sch.components["RS485"].x + sch.components["RS485"].width < mcu.x


def test_bus_to_the_core_runs_straight(hub_layout):
    """The SD card's four SPI wires are the strongest tie in its column: the
    card sits level with its pins on the MCU, every wire one straight line."""
    sch, _, _ = hub_layout
    for net in ("SPI_MOSI", "SPI_MISO", "SPI_SCK", "SPI_CS"):
        assert len([w for w in sch.wires if w.net_name == net]) == 1, net


def test_column_positions_favour_strong_ties():
    # three blocks of height 5 that all want position 0: the strongly wired
    # last one gets it, the others move up, in order and with the gap
    assert _column_positions([0, 0, 0], [1, 1, 4], [5, 5, 5], 2) == [-14, -7, 0]
    # no conflict: every block keeps its target
    assert _column_positions([0, 20, 40], [1, 1, 1], [5, 5, 5], 2) == [0, 20, 40]
