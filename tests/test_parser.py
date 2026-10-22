"""Unit tests for the schematic parser."""

import re
from pathlib import Path

import pytest

from schematic_txt.parser import ParseError, parse_schematic


def test_simple_connections():
    code = """
        ESP32.GND -> SDCard.GND, SensorA.GND
        ESP32.3V3 -> SDCard.VCC, SensorA.VIN
        ESP32.GPIO21(SDA) -> SensorA.SDA : I2C_SDA
        """
    sch = parse_schematic(code)
    assert len(sch.components) == 3
    assert "ESP32" in sch.components
    assert "SDCard" in sch.components
    assert "SensorA" in sch.components

    esp = sch.components["ESP32"]
    assert "GND" in esp.pins
    assert "3V3" in esp.pins
    assert "GPIO21" in esp.pins
    assert esp.pins["GPIO21"].label == "SDA"

    # Check nets
    net_names = [n.name for n in sch.nets]
    assert "GND" in net_names
    assert "3V3" in net_names
    assert "I2C_SDA" in net_names

    gnd_net = next(n for n in sch.nets if n.name == "GND")
    assert len(gnd_net.connections) == 3


def test_component_declarations():
    code = """
        ESP32: ESP32-WROOM
          pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL), GPIO4

        BME280: Environmental Sensor
          pins: VIN, GND, SCL, SDA

        ESP32.GPIO21 -> BME280.SDA
        ESP32.GPIO22 -> BME280.SCL
        """
    sch = parse_schematic(code)
    assert sch.components["ESP32"].type_name == "ESP32-WROOM"
    assert len(sch.components["ESP32"].pins) == 5
    assert sch.components["BME280"].type_name == "Environmental Sensor"


def test_yaml_syntax():
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
    assert len(sch.components) == 2
    assert len(sch.nets) == 3


def test_syntax_error_reporting():
    invalid_code = """
        ESP32.GND ->
        """
    with pytest.raises(ParseError, match="Line 2"):
        parse_schematic(invalid_code)


def test_side_pin_declarations():
    code = """
        ESP32: ESP32-WROOM-32
          left: 3V3, EN, GPIO36, GND
          right: GND, GPIO23, GPIO22, 5V
        """
    sch = parse_schematic(code)
    esp = sch.components["ESP32"]
    assert len(esp.pins) == 8

    # Check left pins and order
    left_pins = [p for p in esp.pins.values() if p.side.name == "LEFT"]
    assert [p.name for p in left_pins] == ["3V3", "EN", "GPIO36", "GND"]
    assert [p.order for p in left_pins] == [0, 1, 2, 3]

    # Check right pins and order
    right_pins = [p for p in esp.pins.values() if p.side.name == "RIGHT"]
    assert [p.name for p in right_pins] == ["GND", "GPIO23", "GPIO22", "5V"]
    assert [p.order for p in right_pins] == [0, 1, 2, 3]

    # Check that both GND pins exist and have unique IDs
    gnd_pins = esp.get_pins_by_name("GND")
    assert len(gnd_pins) == 2
    assert gnd_pins[0].id != gnd_pins[1].id


def test_yaml_side_pins():
    yaml_code = """
        components:
          ESP32:
            type: ESP32-WROOM
            left: [3V3, EN, GND]
            right: [GND, GPIO23, 5V]
        """
    sch = parse_schematic(yaml_code)
    esp = sch.components["ESP32"]
    assert len(esp.pins) == 6
    assert len(esp.get_pins_by_name("GND")) == 2


def test_standard_components_parsing_dsl():
    code = """
        R1: resistor 2kOhm
          pins: 1, 2

        R2: 10k
          kind: resistor
          pins: 1, 2

        R3: Resistor
          value: 4.7k
          pins: 1, 2

        C1: 100nF Capacitor
          pins: 1, 2

        SW1: Slide Switch (SPDT)
          pins: 1, 2, 3

        F1: Inline Fuse
          pins: IN, OUT

        R4 [kind=resistor, value=100R]:
          pins: 1, 2

        RS1: 7.5mOhm FL-2C Shunt
          pins: IN, OUT, SENSE+, SENSE-

        Q1: AO3400A N-MOSFET
          pins: G, D, S

        Q2: P-Channel MOSFET AO3401
          pins: G, D, S

        L1: Lamp 12V
          pins: 1, 2

        LS: Light Sensor
          pins: S, VCC, GND
        """
    sch = parse_schematic(code)
    assert sch.components["R1"].is_standard
    assert sch.components["R1"].kind == "resistor"
    assert sch.components["R1"].value == "2kOhm"

    assert sch.components["R2"].is_standard
    assert sch.components["R2"].kind == "resistor"
    assert sch.components["R2"].value == "10k"

    assert sch.components["R3"].is_standard
    assert sch.components["R3"].kind == "resistor"
    assert sch.components["R3"].value == "4.7k"

    assert sch.components["C1"].is_standard
    assert sch.components["C1"].kind == "capacitor"
    assert sch.components["C1"].value == "100nF"

    assert sch.components["SW1"].is_standard
    assert sch.components["SW1"].kind == "switch"
    assert sch.components["SW1"].value == "SPDT"

    assert sch.components["F1"].is_standard
    assert sch.components["F1"].kind == "fuse"
    assert sch.components["F1"].value == "Inline"

    assert sch.components["R4"].is_standard
    assert sch.components["R4"].kind == "resistor"
    assert sch.components["R4"].value == "100R"

    assert sch.components["RS1"].is_standard
    assert sch.components["RS1"].kind == "shunt"
    assert sch.components["RS1"].value == "7.5mOhm FL-2C"

    assert sch.components["Q1"].kind == "nmos"
    assert sch.components["Q1"].value == "AO3400A"
    assert sch.components["Q2"].kind == "pmos"
    assert sch.components["Q2"].value == "AO3401"

    assert sch.components["L1"].kind == "lamp"
    assert sch.components["L1"].value == "12V"
    assert not sch.components["LS"].is_standard


def test_standard_components_parsing_yaml():
    yaml_code = """
        components:
          SpeedPullUp:
            kind: resistor
            value: 2kOhm
            pins: [1, 2]
          SpeedCap:
            symbol: capacitor
            value: 100nF
            pins: [1, 2]
        """
    sch = parse_schematic(yaml_code)
    assert sch.components["SpeedPullUp"].is_standard
    assert sch.components["SpeedPullUp"].kind == "resistor"
    assert sch.components["SpeedPullUp"].value == "2kOhm"

    assert sch.components["SpeedCap"].is_standard
    assert sch.components["SpeedCap"].kind == "capacitor"
    assert sch.components["SpeedCap"].value == "100nF"


def _doc_examples():
    text = (Path(__file__).resolve().parent.parent / "docs" / "syntax.md").read_text(encoding="utf-8")
    return re.findall(r"^```(?:text|yaml)\n(.*?)^```$", text, flags=re.M | re.S)


@pytest.mark.parametrize("example", _doc_examples())
def test_syntax_doc_examples_parse(example):
    assert parse_schematic(example).components
