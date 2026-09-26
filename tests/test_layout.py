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

    def test_no_component_collisions_and_no_collinear_overlaps(self):
        text = """
        ESP32: ESP32-WROOM-32
          pins: 3V3, GND, GPIO23(MOSI), GPIO19(MISO), GPIO18(SCK), GPIO5(CS), GPIO2(LED)

        SDCard: MicroSD Adapter
          pins: 3V3, GND, MOSI, MISO, SCK, CS

        PowerSupply: AMS1117-3.3V
          pins: VIN, GND, VOUT

        StatusLED: Status Indicator
          pins: ANODE, CATHODE

        PowerSupply.VOUT -> ESP32.3V3, SDCard.3V3 : 3V3
        PowerSupply.GND  -> ESP32.GND, SDCard.GND, StatusLED.CATHODE : GND
        ESP32.GPIO23 -> SDCard.MOSI : SPI_MOSI
        ESP32.GPIO19 <- SDCard.MISO : SPI_MISO
        ESP32.GPIO18 -> SDCard.SCK  : SPI_SCK
        ESP32.GPIO5  -> SDCard.CS   : SPI_CS
        ESP32.GPIO2  -> StatusLED.ANODE : LED_DRIVE
        """
        sch = process_schematic(text)

        # 1. No wire passes through interior of any component
        for w in sch.wires:
            for c in sch.components.values():
                inner = c.rect.expanded(-1.0)
                if w.is_horizontal:
                    y = w.start.y
                    x1, x2 = min(w.start.x, w.end.x), max(w.start.x, w.end.x)
                    self.assertFalse(
                        inner.top < y < inner.bottom and not (x2 <= inner.left or x1 >= inner.right),
                        f"Wire segment ({w.start.x},{y})->({x2},{y}) [{w.net_name}] passes through {c.id}",
                    )
                elif w.is_vertical:
                    x = w.start.x
                    y1, y2 = min(w.start.y, w.end.y), max(w.start.y, w.end.y)
                    self.assertFalse(
                        inner.left < x < inner.right and not (y2 <= inner.top or y1 >= inner.bottom),
                        f"Wire segment ({x},{y1})->({x},{y2}) [{w.net_name}] passes through {c.id}",
                    )

        # 2. No two parallel wires overlap collinearly
        for i in range(len(sch.wires)):
            w1 = sch.wires[i]
            for j in range(i + 1, len(sch.wires)):
                w2 = sch.wires[j]
                if w1.is_horizontal and w2.is_horizontal:
                    if abs(w1.start.y - w2.start.y) < 0.5:
                        x1_min, x1_max = min(w1.start.x, w1.end.x), max(w1.start.x, w1.end.x)
                        x2_min, x2_max = min(w2.start.x, w2.end.x), max(w2.start.x, w2.end.x)
                        overlap = min(x1_max, x2_max) - max(x1_min, x2_min)
                        self.assertLessEqual(overlap, 0.5, f"Collinear horizontal overlap at y={w1.start.y}")
                elif w1.is_vertical and w2.is_vertical:
                    if abs(w1.start.x - w2.start.x) < 0.5:
                        y1_min, y1_max = min(w1.start.y, w1.end.y), max(w1.start.y, w1.end.y)
                        y2_min, y2_max = min(w2.start.y, w2.end.y), max(w2.start.y, w2.end.y)
                        overlap = min(y1_max, y2_max) - max(y1_min, y2_min)
                        self.assertLessEqual(overlap, 0.5, f"Collinear vertical overlap at x={w1.start.x}")

    def test_all_entities_strictly_on_grid(self):
        text = """
        ESP32: ESP32-WROOM-32
          pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL), GPIO4(INT)

        BME280: Temp & Humidity Sensor
          pins: VIN, GND, SCL, SDA

        OLED: 0.96" SSD1306
          pins: VDD, GND, SCK, SDA

        ESP32.3V3 -> BME280.VIN, OLED.VDD : 3V3
        ESP32.GND -> BME280.GND, OLED.GND : GND
        ESP32.GPIO21 -> BME280.SDA, OLED.SDA : I2C_SDA
        ESP32.GPIO22 -> BME280.SCL, OLED.SCK : I2C_SCL
        """
        sch = process_schematic(text)
        grid = 20.0

        def check_val(val, desc):
            rem = abs(val % grid)
            is_grid = rem < 1e-4 or abs(rem - grid) < 1e-4
            self.assertTrue(is_grid, f"{desc} with value {val} is not on {grid}px grid")

        # Components
        for c in sch.components.values():
            check_val(c.x, f"Component {c.id} x")
            check_val(c.y, f"Component {c.id} y")
            check_val(c.width, f"Component {c.id} width")
            check_val(c.height, f"Component {c.id} height")
            for p in c.pins.values():
                check_val(p.lead_start.x, f"Pin {c.id}.{p.name} lead_start.x")
                check_val(p.lead_start.y, f"Pin {c.id}.{p.name} lead_start.y")
                check_val(p.position.x, f"Pin {c.id}.{p.name} position.x")
                check_val(p.position.y, f"Pin {c.id}.{p.name} position.y")

        # Wires
        for w in sch.wires:
            check_val(w.start.x, f"Wire start.x [{w.net_name}]")
            check_val(w.start.y, f"Wire start.y [{w.net_name}]")
            check_val(w.end.x, f"Wire end.x [{w.net_name}]")
            check_val(w.end.y, f"Wire end.y [{w.net_name}]")

        # Junctions
        for j in sch.junctions:
            check_val(j.point.x, f"Junction point.x [{j.net_name}]")
            check_val(j.point.y, f"Junction point.y [{j.net_name}]")

    def test_all_connected_pins_touch_wire_terminals(self):
        """Verifies that every connection has a wire segment meeting the pin terminal exactly."""
        examples = [
            "examples/esp32_sd_spi.txt",
            "examples/esp32_sensors_bus.txt",
            "examples/simple_quickstart.txt",
        ]
        for ex_path in examples:
            with open(ex_path) as f:
                content = f.read()
            sch = process_schematic(content)
            for net in sch.nets:
                for conn in net.connections:
                    comp = sch.components.get(conn.component_id)
                    self.assertIsNotNone(comp, f"Component {conn.component_id} not found in {ex_path}")
                    pin = comp.pins.get(conn.pin_id) if conn.pin_id else comp.get_pin(conn.pin_name)
                    self.assertIsNotNone(pin, f"Pin {conn.pin_name} not found on {conn.component_id} in {ex_path}")
                    pos = pin.position
                    # Must have at least one wire segment touching this pin terminal
                    has_touching_wire = any(
                        (w.start.distance_to(pos) < 0.5 or w.end.distance_to(pos) < 0.5)
                        and w.net_name == net.name
                        for w in sch.wires
                    )
                    self.assertTrue(
                        has_touching_wire,
                        f"Pin {conn.component_id}.{conn.pin_name} (net {net.name}) at {pos} has no touching wire in {ex_path}!",
                    )

    def test_equivalent_gnd_nearest_selection(self):
        text = """
        ESP32: ESP32-WROOM-32
          left: 3V3, EN, GPIO36, GND
          right: GND, GPIO23, GPIO22, 5V

        SensorLeft: BME280
          pins: VIN, GND, SDA, SCL

        SensorRight: MicroSD
          pins: 3V3, GND, MOSI, MISO

        ESP32.GND -> SensorLeft.GND
        ESP32.GND -> SensorRight.GND
        ESP32.GPIO36 -> SensorLeft.SDA
        ESP32.GPIO23 -> SensorRight.MOSI
        """
        sch = process_schematic(text)
        esp = sch.components["ESP32"]
        self.assertEqual(len(esp.pins), 8)
        gnd_pins = esp.get_pins_by_name("GND")
        self.assertEqual(len(gnd_pins), 2)

        # Both left and right GND pins on ESP32 should touch wires from their respective nearest sensors
        for gnd in gnd_pins:
            touches = any(
                (w.start.distance_to(gnd.position) < 0.5 or w.end.distance_to(gnd.position) < 0.5)
                and w.net_name == "GND"
                for w in sch.wires
            )
            self.assertTrue(touches, f"ESP32 GND pin on {gnd.side} was not connected to wire!")

    def test_no_dual_bends_at_same_node_and_clean_crossings(self):
        """Verifies that 2 different nets NEVER bend at the same node, and crossings are purely orthogonal."""
        import glob
        for f in glob.glob("examples/*.txt"):
            with open(f) as fp:
                text = fp.read()
            sch = process_schematic(text)

            net_wires = {}
            for w in sch.wires:
                net_wires.setdefault(w.net_name, []).append(w)

            net_bends = {}
            node_nets = {}
            for net_name, wires in net_wires.items():
                adj = {}
                for w in wires:
                    p1 = (round(w.start.x, 1), round(w.start.y, 1))
                    p2 = (round(w.end.x, 1), round(w.end.y, 1))
                    adj.setdefault(p1, set()).add(p2)
                    adj.setdefault(p2, set()).add(p1)
                    node_nets.setdefault(p1, set()).add(net_name)
                    node_nets.setdefault(p2, set()).add(net_name)

                for node, nbrs in adj.items():
                    if len(nbrs) == 2:
                        n1, n2 = list(nbrs)
                        d1 = (round(n1[0] - node[0], 1), round(n1[1] - node[1], 1))
                        d2 = (round(n2[0] - node[0], 1), round(n2[1] - node[1], 1))
                        if (d1[0] + d2[0] != 0) or (d1[1] + d2[1] != 0):
                            net_bends.setdefault(node, set()).add(net_name)
                    elif len(nbrs) > 2:
                        net_bends.setdefault(node, set()).add(net_name)

            # 1. No node may have bends or junctions from more than 1 net
            dual_bends = {node: nets for node, nets in net_bends.items() if len(nets) > 1}
            self.assertEqual(len(dual_bends), 0, f"Found dual bends at same node in {f}: {dual_bends}")

            # 2. At any crossing node (visited by > 1 net), neither net may bend at that node
            crossing_nodes = {node: nets for node, nets in node_nets.items() if len(nets) > 1}
            for node, nets in crossing_nodes.items():
                self.assertNotIn(node, net_bends, f"A net bends at crossing node {node} in {f}!")


if __name__ == "__main__":
    unittest.main()

