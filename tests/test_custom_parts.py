"""Tests for custom graphical parts (Fritzing-style) and coordinate pins."""

import unittest
from schematic import parse_schematic, layout_schematic, render_schematic_to_svg
from schematic.models import GRID_SIZE, Point, Side


class TestCustomParts(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
