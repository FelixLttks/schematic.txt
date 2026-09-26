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


if __name__ == "__main__":
    unittest.main()
