"""Tests for SVG schematic rendering."""

import unittest
from schematic import generate_schematic_svg


class TestRenderer(unittest.TestCase):
    def test_svg_generation(self):
        text = """
        ESP32.GND -> Sensor.GND
        ESP32.3V3 -> Sensor.VCC
        """
        svg = generate_schematic_svg(text)
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(svg.strip().endswith("</svg>"))
        self.assertIn("ESP32", svg)
        self.assertIn("Sensor", svg)
        self.assertIn("schematic-wires", svg)


    def test_standard_components_rendering(self):
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
        svg = generate_schematic_svg(text)
        self.assertIn("component-resistor", svg)
        self.assertIn("component-capacitor", svg)
        self.assertIn("component-switch", svg)
        self.assertIn("component-fuse", svg)
        self.assertIn("2kOhm", svg)
        self.assertIn("100nF", svg)
        self.assertIn("PullUp", svg)
        self.assertIn("FilterCap", svg)


if __name__ == "__main__":
    unittest.main()
