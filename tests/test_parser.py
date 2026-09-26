"""Unit tests for the schematic parser."""

import unittest
from schematic.parser import parse_schematic, ParseError


class TestParser(unittest.TestCase):
    def test_simple_connections(self):
        code = """
        ESP32.GND -> SDCard.GND, SensorA.GND
        ESP32.3V3 -> SDCard.VCC, SensorA.VIN
        ESP32.GPIO21(SDA) -> SensorA.SDA : I2C_SDA
        """
        sch = parse_schematic(code)
        self.assertEqual(len(sch.components), 3)
        self.assertIn("ESP32", sch.components)
        self.assertIn("SDCard", sch.components)
        self.assertIn("SensorA", sch.components)

        esp = sch.components["ESP32"]
        self.assertIn("GND", esp.pins)
        self.assertIn("3V3", esp.pins)
        self.assertIn("GPIO21", esp.pins)
        self.assertEqual(esp.pins["GPIO21"].label, "SDA")

        # Check nets
        net_names = [n.name for n in sch.nets]
        self.assertIn("GND", net_names)
        self.assertIn("3V3", net_names)
        self.assertIn("I2C_SDA", net_names)

        gnd_net = next(n for n in sch.nets if n.name == "GND")
        self.assertEqual(len(gnd_net.connections), 3)

    def test_component_declarations(self):
        code = """
        ESP32: ESP32-WROOM
          pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL), GPIO4

        BME280: Environmental Sensor
          pins: VIN, GND, SCL, SDA

        ESP32.GPIO21 -> BME280.SDA
        ESP32.GPIO22 -> BME280.SCL
        """
        sch = parse_schematic(code)
        self.assertEqual(sch.components["ESP32"].type_name, "ESP32-WROOM")
        self.assertEqual(len(sch.components["ESP32"].pins), 5)
        self.assertEqual(sch.components["BME280"].type_name, "Environmental Sensor")

    def test_yaml_syntax(self):
        yaml_code = """
        components:
          MCU:
            type: ESP32
            pins: [3V3, GND, IO21, IO22]
          OLED:
            type: SSD1306
            pins: [VCC, GND, SDA, SCL]
        connections:
          - MCU.GND -> OLED.GND
          - MCU.3V3 -> OLED.VCC
          - MCU.IO21 -> OLED.SDA : I2C_SDA
        """
        sch = parse_schematic(yaml_code)
        self.assertEqual(len(sch.components), 2)
        self.assertEqual(len(sch.nets), 3)

    def test_syntax_error_reporting(self):
        invalid_code = """
        ESP32.GND ->
        """
        with self.assertRaises(ParseError) as ctx:
            parse_schematic(invalid_code)
        self.assertIn("Line 2", str(ctx.exception))


    def test_side_pin_declarations(self):
        code = """
        ESP32: ESP32-WROOM-32
          left: 3V3, EN, GPIO36, GND
          right: GND, GPIO23, GPIO22, 5V
        """
        sch = parse_schematic(code)
        esp = sch.components["ESP32"]
        self.assertEqual(len(esp.pins), 8)

        # Check left pins and order
        left_pins = [p for p in esp.pins.values() if p.side.name == "LEFT"]
        self.assertEqual([p.name for p in left_pins], ["3V3", "EN", "GPIO36", "GND"])
        self.assertEqual([p.order for p in left_pins], [0, 1, 2, 3])

        # Check right pins and order
        right_pins = [p for p in esp.pins.values() if p.side.name == "RIGHT"]
        self.assertEqual([p.name for p in right_pins], ["GND", "GPIO23", "GPIO22", "5V"])
        self.assertEqual([p.order for p in right_pins], [0, 1, 2, 3])

        # Check that both GND pins exist and have unique IDs
        gnd_pins = esp.get_pins_by_name("GND")
        self.assertEqual(len(gnd_pins), 2)
        self.assertNotEqual(gnd_pins[0].id, gnd_pins[1].id)

    def test_yaml_side_pins(self):
        yaml_code = """
        components:
          ESP32:
            type: ESP32-WROOM
            left: [3V3, EN, GND]
            right: [GND, GPIO23, 5V]
        """
        sch = parse_schematic(yaml_code)
        esp = sch.components["ESP32"]
        self.assertEqual(len(esp.pins), 6)
        self.assertEqual(len(esp.get_pins_by_name("GND")), 2)


if __name__ == "__main__":
    unittest.main()
