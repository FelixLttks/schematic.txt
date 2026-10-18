"""Tests for custom graphical parts (Fritzing-style) and coordinate pins."""

import unittest
import re
from schematic import parse_schematic, layout_schematic, render_schematic_to_svg
from schematic.layout.config import LayoutConfig
from schematic.layout.pin_allocator import rotate_component_90, size_and_place_pins
from schematic.layout.placer import Placer
from schematic.models import GRID_SIZE, Point, Side


class TestCustomParts(unittest.TestCase):
    def test_custom_bottom_pin_labels_use_separate_lanes(self):
        text = """
part Board:
  image: parts/board.png
  size: 8x4
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
        svg = render_schematic_to_svg(sch)
        labels = re.findall(
            r'<text x="[^"]+" y="([^"]+)" text-anchor="middle"[^>]*>(VIN|GND|VIN-|ALRT)</text>',
            svg,
        )
        self.assertEqual(len(labels), 4)
        self.assertGreater(len({y for y, _ in labels}), 1)

    def test_parse_part_template_and_instantiation(self):
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
        self.assertIn("ESP32", sch.parts)
        part = sch.parts["ESP32"]
        self.assertEqual(part.image, "parts/esp32_devkit.svg")
        self.assertEqual(part.grid_width, 9)
        self.assertEqual(part.grid_height, 18)
        self.assertIn("3V3", part.pins)
        self.assertEqual(part.pins["3V3"][:2], (0, 2))

        # Check instantiated component
        esp = sch.components["ESP"]
        self.assertEqual(esp.image, "parts/esp32_devkit.svg")
        self.assertEqual(esp.grid_width, 9)
        self.assertEqual(esp.grid_height, 18)
        self.assertIn("3V3", esp.pins)
        self.assertEqual(esp.pins["3V3"].grid_x, 0)
        self.assertEqual(esp.pins["3V3"].grid_y, 2)
        self.assertIn("GPIO21", esp.pins)
        self.assertEqual(esp.pins["GPIO21"].grid_x, 8)
        self.assertEqual(esp.pins["GPIO21"].grid_y, 5)

    def test_inline_custom_part(self):
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
        self.assertEqual(mcu.image, "parts/pico.svg")
        self.assertEqual(mcu.grid_width, 6)
        self.assertEqual(mcu.grid_height, 14)
        self.assertEqual(mcu.pins["VBUS"].grid_x, 5)
        self.assertEqual(mcu.pins["VBUS"].grid_y, 1)

    def test_layout_and_svg_render(self):
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
        self.assertEqual(esp.width, 9 * GRID_SIZE)
        self.assertEqual(esp.height, 18 * GRID_SIZE)

        # Pins must be on the 20px grid
        for p in esp.pins.values():
            self.assertEqual(p.position.x % GRID_SIZE, 0.0)
            self.assertEqual(p.position.y % GRID_SIZE, 0.0)
            self.assertEqual(p.lead_start.x % GRID_SIZE, 0.0)
            self.assertEqual(p.lead_start.y % GRID_SIZE, 0.0)

        svg = render_schematic_to_svg(sch)
        self.assertIn('<image href="/parts/esp32_devkit.svg"', svg)
        self.assertIn('class="component-custom"', svg)
        # Check that pin pad circle is rendered
        self.assertIn('<circle cx="', svg)


    def test_image_rect_support(self):
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
        self.assertEqual(part.image_rect, (-12.0, -8.0, 204.0, 376.0))
        esp = sch.components["ESP"]
        self.assertEqual(esp.image_rect, (-12.0, -8.0, 204.0, 376.0))

        layout_schematic(sch)
        esp = sch.components["ESP"]
        expected_x = esp.x - 12.0
        expected_y = esp.y - 8.0
        svg = render_schematic_to_svg(sch)
        self.assertIn(f'x="{expected_x:.1f}" y="{expected_y:.1f}" width="204.0" height="376.0"', svg)

    def test_rotatable_default_and_explicit_disable(self):
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
        self.assertFalse(sch.parts["Fixed"].rotatable)
        self.assertFalse(sch.components["Fixed1"].rotatable)
        self.assertTrue(sch.components["Free1"].rotatable)
        self.assertFalse(sch.components["Inline"].rotatable)

    def test_rotation_transforms_grid_and_geometry(self):
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
        size_and_place_pins(board)
        self.assertEqual((board.width, board.height), (80.0, 100.0))

        rotate_component_90(board, clockwise=True)
        size_and_place_pins(board)
        self.assertEqual(board.rotation, 90)
        self.assertEqual((board.grid_width, board.grid_height), (5, 4))
        self.assertEqual((board.width, board.height), (100.0, 80.0))
        self.assertEqual((board.pins["A"].grid_x, board.pins["A"].grid_y), (4, 0))
        self.assertEqual((board.pins["B"].grid_x, board.pins["B"].grid_y), (3, 3))
        self.assertEqual(board.pins["A"].side, Side.TOP)
        self.assertEqual(board.pins["B"].side, Side.BOTTOM)
        svg = render_schematic_to_svg(sch)
        self.assertIn("rotate(90 ", svg)

    def test_image_parts_get_a_rotation_candidate_by_default(self):
        text = """
part Module:
  image: parts/module.png
  size: 8x4
  pins:
    A: 0, 1
    B: 0, 2
    C: 0, 3

Module1: Module
Peer: Peer
  pins: P1, P2, P3

Module1.A -> Peer.P1
Module1.B -> Peer.P2
Module1.C -> Peer.P3
"""
        sch = parse_schematic(text)
        placer = Placer(sch, config=LayoutConfig())
        placer.place("layered", search=False)
        variants = placer.orientation_variants()
        self.assertIn("Module1", [cid for cid, _ in variants])

        sch = parse_schematic(text.replace("size: 8x4", "size: 8x4\n  rotatable: false"))
        placer = Placer(sch, config=LayoutConfig())
        placer.place("layered", search=False)
        self.assertNotIn("Module1", [cid for cid, _ in placer.orientation_variants()])

    def test_orientation_override_rotates_part_unless_disabled(self):
        text = """
part Board:
  image: parts/board.png
  size: 4x5
  pins:
    A: 0, 1
    B: 3, 2

Board1: Board
Peer: Peer
  pins: P1, P2
Board1.A -> Peer.P1
Board1.B -> Peer.P2
"""
        sch = parse_schematic(text)
        Placer(sch, config=LayoutConfig(), orientation_overrides={"Board1": True},
               keep_positions=True).place("layered", search=False)
        board = sch.components["Board1"]
        self.assertEqual(board.rotation, 90)
        self.assertEqual((board.width, board.height), (100.0, 80.0))

        sch = parse_schematic(text.replace("size: 4x5", "size: 4x5\n  rotatable: false"))
        Placer(sch, config=LayoutConfig(), orientation_overrides={"Board1": True},
               keep_positions=True).place("layered", search=False)
        self.assertEqual(sch.components["Board1"].rotation, 0)


    def test_duplicate_pin_names_in_custom_part_and_best_selection(self):
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
        self.assertEqual(len([p for p in mcu.pins.values() if p.name == "GND"]), 3)
        self.assertEqual(len([p for p in mcu.pins.values() if p.name == "5V"]), 2)
        res = layout_schematic(sch)
        self.assertEqual(res.report.violation_count, 0)
        svg = render_schematic_to_svg(sch)
        # Verify rendered text shows 'GND' and '5V' without internal '#2' suffixes
        self.assertIn(">GND<", svg)
        self.assertNotIn(">GND#2<", svg)


if __name__ == "__main__":
    unittest.main()
