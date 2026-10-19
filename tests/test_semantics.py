"""Tests for semantic classification and input normalisation."""

import unittest

from schematic import parse_schematic
from schematic.layout.normalize import merge_shared_pin_nets
from schematic.layout.semantics import analyze


class TestAnalyze(unittest.TestCase):
    TEXT = """
    MCU: Controller
      pins: 3V3, GND, SDA, SCL, DP, DM, EN
    Sensor: Sensor
      pins: VCC, GND, SDA, SCL
    Display: Display
      pins: VCC, GND, SDA, SCL
    Aux: Aux
      pins: VCC, GND
    USB: Connector
      pins: D+, D-
    Rpu: resistor 10k
      pins: 1, 2
    Cdec: capacitor 100nF
      pins: 1, 2
    F1: fuse 1A
      pins: IN, OUT
    Rs: resistor 1k
      pins: 1, 2
    Load: Load
      pins: IN

    MCU.3V3 -> Sensor.VCC, Display.VCC, Aux.VCC, Rpu.1, Cdec.1 : 3V3
    MCU.GND -> Sensor.GND, Display.GND, Aux.GND, Cdec.2 : GND
    MCU.SDA -> Sensor.SDA, Display.SDA : I2C_SDA
    MCU.SCL -> Sensor.SCL, Display.SCL : I2C_SCL
    MCU.DP -> USB.D+ : USB_D_P
    MCU.DM -> USB.D- : USB_D_N
    Rpu.2 -> MCU.EN : EN
    MCU.3V3 -> F1.IN : 3V3
    F1.OUT -> Load.IN : LOAD_PWR
    Load.IN -> Rs.1 : LOAD_PWR
    Rs.2 -> USB.D- : USB_D_N
    """

    def setUp(self):
        self.sch = parse_schematic(self.TEXT)
        self.sem = analyze(self.sch)

    def test_rails_are_structural(self):
        self.assertTrue(self.sem.nets["GND"].is_rail)
        self.assertTrue(self.sem.nets["3V3"].is_rail)
        self.assertFalse(self.sem.nets["I2C_SDA"].is_rail)

    def test_bundles(self):
        sda, scl = self.sem.nets["I2C_SDA"], self.sem.nets["I2C_SCL"]
        self.assertIsNotNone(sda.bundle)
        self.assertEqual(sda.bundle, scl.bundle)
        self.assertEqual(self.sem.bundles[sda.bundle].kind, "bus")
        self.assertTrue(self.sem.related("I2C_SDA", "I2C_SCL"))
        self.assertFalse(self.sem.related("I2C_SDA", "USB_D_P"))

    def test_component_roles(self):
        roles = self.sem.component_roles
        self.assertEqual(roles["Rpu"], "shunt")        # one distribution net + one other net
        self.assertEqual(roles["Cdec"], "decoupling")  # between two distribution nets
        self.assertEqual(roles["F1"], "shunt")        # same shape as a pull-up: names/kinds do not matter
        self.assertEqual(roles["Rs"], "series")        # between two ordinary nets
        self.assertEqual(roles["MCU"], "module")
        self.assertIn("MCU", self.sem.hubs)

    def test_names_are_irrelevant(self):
        """Renaming nets and pins must not change the analysis."""
        text = self.TEXT
        for old, new in (("3V3", "NETA"), ("GND", "NETB"), ("I2C_SDA", "X1"), ("I2C_SCL", "X2")):
            text = text.replace(old, new)
        sem2 = analyze(parse_schematic(text))
        self.assertEqual(sem2.component_roles, self.sem.component_roles)
        self.assertEqual(sem2.hubs, self.sem.hubs)
        self.assertEqual([i.is_rail for i in sem2.nets.values()],
                         [i.is_rail for i in self.sem.nets.values()])


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
