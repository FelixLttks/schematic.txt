"""SVG rendering: content, themes, embedded part images."""

import re
from pathlib import Path

import pytest

from schematic_txt import ImageError, layout_schematic, parse_schematic, render, render_svg
from schematic_txt.models import Side

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

IMAGE_PART = """
part Board:
  image: parts/esp32_devkit.svg
  size: 9x18
  pins:
    GND: 0, 16
    3V3: 0, 2

B: Board
B.GND -> Sensor.GND
B.3V3 -> Sensor.VCC
"""


def _laid_out(text):
    sch = parse_schematic(text)
    layout_schematic(sch)
    return sch


def test_svg_generation():
    svg = render("ESP32.GND -> Sensor.GND\nESP32.3V3 -> Sensor.VCC", effort="fast")
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")
    assert "ESP32" in svg
    assert "Sensor" in svg
    assert "schematic-wires" in svg


def test_svg_has_its_natural_size():
    svg = render("A.1 -> B.1", effort="fast")
    width, height, vb_w, vb_h = re.search(
        r'width="(\d+)" height="(\d+)" viewBox="\S+ \S+ ([\d.]+) ([\d.]+)"', svg
    ).groups()
    assert (float(width), float(height)) == (float(vb_w), float(vb_h))


def test_themes_differ_in_background():
    sch = _laid_out("A.1 -> B.1")
    assert render_svg(sch, theme="light") != render_svg(sch, theme="dark")
    with pytest.raises(ValueError, match="unknown theme"):
        render_svg(sch, theme="sepia")


def test_standard_components_rendering():
    text = """
        ESP32: ESP32-C3
          pins: 3V3, GND, GPIO0

        PullUp: resistor 2kOhm
          pins: 1, 2

        FilterCap: 100nF Capacitor
          pins: 1, 2

        PowerSwitch: Slide Switch (SPDT)
          pins: 1, 2, 3

        Fuse: Inline Fuse
          pins: IN, OUT

        ESP32.3V3 -> PullUp.1
        PullUp.2 -> ESP32.GPIO0, FilterCap.1
        FilterCap.2 -> ESP32.GND
        PowerSwitch.1 -> Fuse.IN
        Fuse.OUT -> ESP32.3V3
        """
    svg = render(text, effort="fast")
    for kind in ("resistor", "capacitor", "switch", "fuse"):
        assert f"component-{kind}" in svg
    for text in ("2kOhm", "100nF", "PullUp", "FilterCap"):
        assert text in svg


def test_shunt_is_drawn_with_kelvin_taps():
    text = """
        Shunt: 7.5mOhm Shunt
          left: IN, SENSE+
          right: OUT, SENSE-

        Bat.POS -> Shunt.IN
        Shunt.OUT -> Load.VCC
        Shunt.SENSE+ -> Mon.VIN+
        Shunt.SENSE- -> Mon.VIN-
        """
    sch = _laid_out(text)
    pins = sch.components["Shunt"].pins
    assert pins["IN"].side == Side.LEFT and pins["OUT"].side == Side.RIGHT
    assert pins["SENSE+"].side == pins["SENSE-"].side in (Side.TOP, Side.BOTTOM)
    # each tap sits at the end of its current terminal
    assert pins["SENSE+"].position.x < pins["SENSE-"].position.x
    svg = render_svg(sch)
    assert "component-shunt" in svg
    assert "7.5mOhm" in svg


def test_mosfet_is_drawn_as_symbol():
    text = """
        Q1: AO3400A N-MOSFET
          pins: S, G, D

        MCU.GPIO2 -> Q1.G
        Q1.D -> Light.V-
        Q1.S -> MCU.GND
        """
    sch = _laid_out(text)
    pins = sch.components["Q1"].pins
    # pins are found by name, not by order
    assert pins["G"].side in (Side.LEFT, Side.RIGHT)
    assert {pins["D"].side, pins["S"].side} == {Side.TOP, Side.BOTTOM}
    assert pins["D"].position.x == pins["S"].position.x
    svg = render_svg(sch)
    assert "component-nmos" in svg
    assert "AO3400A" in svg


def test_part_images_are_embedded_from_base_dir():
    svg = render_svg(_laid_out(IMAGE_PART), base_dir=EXAMPLES)
    assert '<image href="data:image/svg+xml;base64,' in svg


def test_part_image_paths_are_kept_without_base_dir():
    svg = render_svg(_laid_out(IMAGE_PART))
    assert '<image href="parts/esp32_devkit.svg"' in svg


def test_missing_part_image_is_an_error(tmp_path):
    with pytest.raises(ImageError, match=r"esp32_devkit\.svg"):
        render_svg(_laid_out(IMAGE_PART), base_dir=tmp_path)
