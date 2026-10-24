"""Tests for custom graphical parts (Fritzing-style) and coordinate pins."""

import re

from schematic_txt import layout_schematic, parse_schematic, render_svg
from schematic_txt.layout.placement import PartModel
from schematic_txt.layout.shapes import apply_shape, image_shape
from schematic_txt.models import GRID_SIZE, Side


def test_bottom_pin_tags_run_along_their_wires():
    text = """
part Board:
  image: parts/board.png
  size: 8x4
  rotatable: false
  pins:
    VIN: 1, 4
    GND: 2, 4
    VIN-: 3, 4
    ALRT: 4, 4

Board1: Board
Peer: Peer
  pins: A
Board1.VIN -> Peer.A
"""
    sch = parse_schematic(text)
    layout_schematic(sch)
    svg = render_svg(sch)
    tags = re.findall(r'<text [^>]*transform="rotate\(90 [^"]+\)"[^>]*>(VIN|GND|VIN-|ALRT)</text>', svg)
    assert sorted(tags) == ["ALRT", "GND", "VIN", "VIN-"]


def test_parse_part_template_and_instantiation():
    text = """
part ESP32:
  image: parts/esp32_devkit.svg
  size: 9x18
  pins:
    3V3:    0, 2
    EN:     0, 3
    GND:    0, 15
    GPIO21: 8, 5
    GPIO22: 8, 6

ESP: ESP32
Sensor: BME280
  pins: VIN, GND, SDA, SCL

ESP.3V3 -> Sensor.VIN
ESP.GND -> Sensor.GND
ESP.GPIO21 -> Sensor.SDA : I2C_SDA
ESP.GPIO22 -> Sensor.SCL : I2C_SCL
"""
    sch = parse_schematic(text)
    assert "ESP32" in sch.parts
    part = sch.parts["ESP32"]
    assert part.image == "parts/esp32_devkit.svg"
    assert part.grid_width == 9
    assert part.grid_height == 18
    assert "3V3" in part.pins
    assert part.pins["3V3"][:2] == (0, 2)

    # Check instantiated component
    esp = sch.components["ESP"]
    assert esp.image == "parts/esp32_devkit.svg"
    assert esp.grid_width == 9
    assert esp.grid_height == 18
    assert "3V3" in esp.pins
    assert esp.pins["3V3"].grid_x == 0
    assert esp.pins["3V3"].grid_y == 2
    assert "GPIO21" in esp.pins
    assert esp.pins["GPIO21"].grid_x == 8
    assert esp.pins["GPIO21"].grid_y == 5


def test_inline_custom_part():
    text = """
MCU:
  image: parts/pico.svg
  size: 6x14
  pins:
    VBUS: 5, 1
    GND: 0, 3

LED1: led
  pins: 1, 2

MCU.VBUS -> LED1.1
MCU.GND -> LED1.2
"""
    sch = parse_schematic(text)
    mcu = sch.components["MCU"]
    assert mcu.image == "parts/pico.svg"
    assert mcu.grid_width == 6
    assert mcu.grid_height == 14
    assert mcu.pins["VBUS"].grid_x == 5
    assert mcu.pins["VBUS"].grid_y == 1


def test_layout_and_svg_render():
    text = """
part ESP32:
  image: parts/esp32_devkit.svg
  size: 9x18
  pins:
    3V3: 0, 2
    GND: 0, 15

ESP32.3V3 -> Sensor.VCC
ESP32.GND -> Sensor.GND
"""
    sch = parse_schematic(text)
    layout_schematic(sch)

    esp = sch.components["ESP32"]
    assert esp.width == 9 * GRID_SIZE
    assert esp.height == 18 * GRID_SIZE

    # Pins must be on the 20px grid
    for p in esp.pins.values():
        assert p.position.x % GRID_SIZE == 0.0
        assert p.position.y % GRID_SIZE == 0.0
        assert p.lead_start.x % GRID_SIZE == 0.0
        assert p.lead_start.y % GRID_SIZE == 0.0

    svg = render_svg(sch)
    assert '<image href="parts/esp32_devkit.svg"' in svg
    assert 'class="component-custom"' in svg
    # Check that pin pad circle is rendered
    assert '<circle cx="' in svg


def test_image_rect_support():
    text = """
part ESP32:
  image: parts/esp32_devkit.svg
  size: 9x18
  image_rect: -12, -8, 204, 376
  pins:
    3V3: 0, 2

ESP: ESP32
Sensor: Sensor
  pins: VCC

ESP.3V3 -> Sensor.VCC
"""
    sch = parse_schematic(text)
    part = sch.parts["ESP32"]
    assert part.image_rect == (-12.0, -8.0, 204.0, 376.0)
    esp = sch.components["ESP"]
    assert esp.image_rect == (-12.0, -8.0, 204.0, 376.0)

    layout_schematic(sch)
    esp = sch.components["ESP"]
    expected_x = esp.x - 12.0
    expected_y = esp.y - 8.0
    svg = render_svg(sch)
    assert f'x="{expected_x:.1f}" y="{expected_y:.1f}" width="204.0" height="376.0"' in svg


def test_rotatable_default_and_explicit_disable():
    text = """
part Fixed:
  image: parts/fixed.png
  size: 6x3
  rotatable: false
  pins:
    A: 0, 1
    B: 5, 1

part Free:
  image: parts/free.png
  size: 4x2
  pins:
    A: 0, 1
    B: 3, 1

Fixed1: Fixed
Free1: Free
Inline: Free [rotatable=false]
"""
    sch = parse_schematic(text)
    assert not sch.parts["Fixed"].rotatable
    assert not sch.components["Fixed1"].rotatable
    assert sch.components["Free1"].rotatable
    assert not sch.components["Inline"].rotatable


def test_rotation_transforms_grid_and_geometry():
    text = """
part Board:
  image: parts/board.png
  size: 4x5
  pins:
    A: 0, 1
    B: 3, 2

Board1: Board
"""
    sch = parse_schematic(text)
    board = sch.components["Board1"]
    upright = image_shape(board, 0)
    assert (upright.w, upright.h) == (4, 5)
    turned = image_shape(board, 90)
    assert (turned.w, turned.h) == (5, 4)
    # (gx, gy) -> (gh - gy, gx) for a clockwise quarter turn
    assert (turned.pins["A"].lx, turned.pins["A"].ly) == (4, 0)
    assert (turned.pins["B"].lx, turned.pins["B"].ly) == (3, 3)
    assert turned.pins["A"].side == Side.TOP
    assert turned.pins["B"].side == Side.BOTTOM
    apply_shape(board, turned, 0, 0)
    assert (board.width, board.height) == (100.0, 80.0)
    svg = render_svg(sch)
    assert "rotate(90 " in svg
    apply_shape(board, image_shape(board, 180), 0, 0)
    assert "rotate(180 " in render_svg(sch)


def test_image_parts_are_rotatable_unless_disabled():
    text = """
part Module:
  image: parts/module.png
  size: 8x4
  pins:
    A: 0, 1

Module1: Module
"""
    sch = parse_schematic(text)
    assert PartModel(sch.components["Module1"]).variants == [0, 90, 270]
    sch = parse_schematic(text.replace("size: 8x4", "size: 8x4\n  rotatable: false"))
    assert PartModel(sch.components["Module1"]).variants == [0]


def test_duplicate_pin_names_in_custom_part_and_best_selection():
    text = """
part CustomMCU:
  image: parts/mcu.png
  size: 10x10
  pins:
    GND: 1, 2
    GND: 8, 2
    GND: 8, 8
    5V: 1, 9
    5V: 8, 9

MCU: CustomMCU
LeftLoad: Resistor
  pins: 1, 2
RightLoad: Resistor
  pins: 1, 2

LeftLoad.1 -> MCU.GND
RightLoad.1 -> MCU.5V
"""
    sch = parse_schematic(text)
    mcu = sch.components["MCU"]
    assert len([p for p in mcu.pins.values() if p.name == "GND"]) == 3
    assert len([p for p in mcu.pins.values() if p.name == "5V"]) == 2
    res = layout_schematic(sch)
    assert res.report.violation_count == 0
    svg = render_svg(sch)
    # Verify rendered text shows 'GND' and '5V' without internal '#2' suffixes
    assert ">GND<" in svg
    assert ">GND#2<" not in svg


def test_part_without_image_gets_its_pins_once():
    text = """
part Board:
  size: 4x4
  pins:
    VIN: 0, 1
    GND: 0, 2
    GND: 3, 2

X: Board
X.VIN -> Y.VIN
"""
    sch = parse_schematic(text)
    pins = sorted((p.name, p.grid_x, p.grid_y) for p in sch.components["X"].pins.values())
    assert pins == [("GND", 0, 2), ("GND", 3, 2), ("VIN", 0, 1)]
