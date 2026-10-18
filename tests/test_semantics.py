"""Tests for semantic classification and input normalisation."""

import unittest

from schematic import parse_schematic
from schematic.layout.normalize import merge_shared_pin_nets
from schematic.layout.semantics import NetClass, analyze, classify_name, diff_pair_base


class TestNetClassification(unittest.TestCase):
    def test_classify_names(self):
        for name in ("GND", "AGND", "PGND", "VSS", "GND_DIGITAL"):
            self.assertEqual(classify_name(name), NetClass.GROUND, name)
        for name in ("3V3", "VCC", "5V_RAW", "+12V", "VBAT", "1V8", "DISP_VCC", "BATT_POS"):
            self.assertEqual(classify_name(name), NetClass.POWER, name)
        for name in ("I2C_SDA", "SPI_MOSI", "LED_DRIVE", "SPEED_SIGNAL", "SHUNT_P"):
            self.assertIsNone(classify_name(name), name)

    def test_diff_pair_suffixes(self):
        self.assertEqual(diff_pair_base("USB_D_P"), ("USB_D", "P"))
        self.assertEqual(diff_pair_base("CAN_L"), ("CAN", "L"))
        self.assertEqual(diff_pair_base("D+"), ("D", "+"))


class TestAnalyze(unittest.TestCase):
    TEXT = """
    MCU: Controller
      pins: 3V3, GND, SDA, SCL, DP, DM, EN
    Sensor: Sensor
      pins: VCC, GND, SDA, SCL
    Display: Display
      pins: VCC, GND, SDA, SCL
    USB: Connector
      pins: D+, D-
    Rpu: resistor 10k
      pins: 1, 2
    Cdec: capacitor 100nF
      pins: 1, 2
    F1: fuse 1A
      pins: IN, OUT
    Load: Load
      pins: IN

    MCU.3V3 -> Sensor.VCC, Display.VCC, Rpu.1, Cdec.1 : 3V3
    MCU.GND -> Sensor.GND, Display.GND, Cdec.2 : GND
    MCU.SDA -> Sensor.SDA, Display.SDA : I2C_SDA
    MCU.SCL -> Sensor.SCL, Display.SCL : I2C_SCL
    MCU.DP -> USB.D+ : USB_D_P
    MCU.DM -> USB.D- : USB_D_N
    Rpu.2 -> MCU.EN : EN
    MCU.3V3 -> F1.IN : 3V3
    F1.OUT -> Load.IN : LOAD_PWR
    """

    def setUp(self):
        self.sch = parse_schematic(self.TEXT)
        self.sem = analyze(self.sch)

    def test_rails_and_classes(self):
        self.assertTrue(self.sem.nets["GND"].is_rail)
        self.assertEqual(self.sem.nets["GND"].cls, NetClass.GROUND)
        self.assertTrue(self.sem.nets["3V3"].is_rail)
        self.assertEqual(self.sem.nets["I2C_SDA"].cls, NetClass.SIGNAL)
        self.assertFalse(self.sem.nets["I2C_SDA"].is_rail)

    def test_bundles_and_pairs(self):
        sda, scl = self.sem.nets["I2C_SDA"], self.sem.nets["I2C_SCL"]
        self.assertIsNotNone(sda.bundle)
        self.assertEqual(sda.bundle, scl.bundle)
        self.assertEqual(self.sem.bundles[sda.bundle].kind, "bus")
        self.assertTrue(self.sem.related("I2C_SDA", "I2C_SCL"))
        self.assertEqual(self.sem.nets["USB_D_P"].diff_partner, "USB_D_N")
        self.assertEqual(self.sem.bundles[self.sem.nets["USB_D_P"].bundle].kind, "diff")
        self.assertFalse(self.sem.related("I2C_SDA", "USB_D_P"))

    def test_component_roles(self):
        roles = self.sem.component_roles
        self.assertEqual(roles["Rpu"], "shunt")        # pull-up: supply + signal
        self.assertEqual(roles["Cdec"], "decoupling")  # supply + ground
        self.assertEqual(roles["F1"], "series")        # in the power path
        self.assertEqual(roles["MCU"], "module")
        self.assertIn("MCU", self.sem.hubs)


class TestKindDetection(unittest.TestCase):
    def test_single_letter_words_are_not_kinds(self):
        sch = parse_schematic("DevC: Device C\nHdrL: Header L\nPortD: Port D\nC1: 100nF Capacitor\nR1: R 10k\n")
        self.assertFalse(sch.components["DevC"].is_standard)
        self.assertFalse(sch.components["HdrL"].is_standard)
        self.assertFalse(sch.components["PortD"].is_standard)
        self.assertEqual(sch.components["C1"].kind, "capacitor")
        self.assertEqual(sch.components["R1"].kind, "resistor")


class TestNormalisation(unittest.TestCase):
    def test_nets_sharing_a_pin_are_merged(self):
        sch = parse_schematic(
            """
            ESP32.3V3 -> R1.1
            Fuse.OUT -> ESP32.3V3
            ESP32.IO1 -> R1.2 : SENSE
            """
        )
        names = [n.name for n in sch.nets]
        self.assertIn("3V3", names)
        self.assertIn("Fuse_OUT", names)
        renamed = merge_shared_pin_nets(sch)
        names = [n.name for n in sch.nets]
        self.assertEqual(renamed.get("Fuse_OUT"), "3V3")
        self.assertEqual(names.count("3V3"), 1)
        self.assertNotIn("Fuse_OUT", names)
        merged = next(n for n in sch.nets if n.name == "3V3")
        members = {(c.component_id, c.pin_name) for c in merged.connections}
        self.assertEqual(members, {("ESP32", "3V3"), ("R1", "1"), ("Fuse", "OUT")})

    def test_user_names_win_over_generated_names(self):
        sch = parse_schematic("A.X -> B.Y\nB.Y -> C.Z : MAIN\n")
        merge_shared_pin_nets(sch)
        self.assertEqual([n.name for n in sch.nets], ["MAIN"])
        self.assertFalse(sch.nets[0].auto_named)


if __name__ == "__main__":
    unittest.main()
