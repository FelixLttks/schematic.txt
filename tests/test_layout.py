"""Tests for 2D placement and orthogonal routing."""

import unittest
from schematic import process_schematic, parse_schematic
from schematic.layout.placer import place_schematic
from schematic.layout.router import route_schematic


class TestLayoutAndRouting(unittest.TestCase):
    def test_end_to_end_layout(self):
        text = """
        ESP32: ESP32-WROOM
          pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL)

        SensorA: BME280
          pins: VIN, GND, SDA, SCL

        ESP32.3V3 -> SensorA.VIN
        ESP32.GND -> SensorA.GND
        ESP32.GPIO21 -> SensorA.SDA : I2C_SDA
        ESP32.GPIO22 -> SensorA.SCL : I2C_SCL
        """
        sch = process_schematic(text)

        # Check components are placed
        self.assertGreater(sch.components["ESP32"].width, 50)
        self.assertGreater(sch.components["SensorA"].width, 50)

        # Check components do not overlap
        c1 = sch.components["ESP32"]
        c2 = sch.components["SensorA"]
        self.assertFalse(c1.rect.intersects(c2.rect))

        # Check wires were generated
        self.assertGreater(len(sch.wires), 0)

    def test_multi_drop_bus_junctions(self):
        text = """
        ESP32.GND -> Sensor1.GND, Sensor2.GND, OLED.GND
        ESP32.3V3 -> Sensor1.VCC, Sensor2.VCC, OLED.VCC
        ESP32.GPIO21 -> Sensor1.SDA, Sensor2.SDA, OLED.SDA : I2C_SDA
        """
        sch = process_schematic(text)
        self.assertEqual(len(sch.components), 4)

        # Multi-drop nets should produce junction dots
        self.assertGreater(len(sch.junctions), 0)
        junction_nets = {j.net_name for j in sch.junctions}
        self.assertIn("GND", junction_nets)
        self.assertIn("3V3", junction_nets)
        self.assertIn("I2C_SDA", junction_nets)


if __name__ == "__main__":
    unittest.main()
