"""Parser for text-based schematic definitions (DSL and YAML)."""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple
import yaml

from schematic.models import Component, Net, NetConnection, Pin, Schematic


class ParseError(Exception):
    def __init__(self, message: str, line_num: Optional[int] = None, line_content: Optional[str] = None):
        self.message = message
        self.line_num = line_num
        self.line_content = line_content
        detail = f"Line {line_num}: {message}" if line_num is not None else message
        if line_content:
            detail += f"\n  > {line_content}"
        super().__init__(detail)


def parse_pin_ref(token: str, line_num: Optional[int] = None, line_content: Optional[str] = None) -> Tuple[str, str, str]:
    """
    Parses a component and pin reference token.
    Supported patterns:
      - ESP32.GND
      - ESP32.GPIO21(SDA)
      - "Sensor A".VCC
      - SensorA."3.3V"
    Returns (comp_id, pin_name, pin_label).
    """
    token = token.strip()
    if not token:
        raise ParseError("Empty pin reference encountered", line_num, line_content)

    # Match Component.Pin or Component.Pin(Label)
    # Also support quoted identifiers e.g. "Sensor 1"."VCC (3V)"
    dot_index = token.find(".")
    if dot_index == -1:
        raise ParseError(
            f"Invalid pin reference '{token}'. Must be in format 'Component.Pin' (e.g. ESP32.GND or ESP32.GPIO21(SDA))",
            line_num,
            line_content,
        )

    comp_part = token[:dot_index].strip().strip('"\'')
    pin_part = token[dot_index + 1:].strip()

    if not comp_part:
        raise ParseError(f"Missing component name before '.' in '{token}'", line_num, line_content)
    if not pin_part:
        raise ParseError(f"Missing pin name after '.' in '{token}'", line_num, line_content)

    # Check for label in parentheses, e.g. GPIO21(SDA)
    label_match = re.match(r"^([^\(\)]+)(?:\((.*)\))?$", pin_part)
    if label_match:
        pin_name = label_match.group(1).strip().strip('"\'')
        pin_label = (label_match.group(2) or "").strip()
    else:
        pin_name = pin_part.strip('"\'')
        pin_label = ""

    return comp_part, pin_name, pin_label


class SchematicParser:
    def __init__(self):
        self.schematic = Schematic()

    def parse(self, text: str) -> Schematic:
        self.schematic = Schematic()
        stripped = text.strip()
        if not stripped:
            return self.schematic

        # If it looks like YAML with top-level dictionary containing 'components' or 'nets' or 'connections'
        if self._is_structured_yaml(text):
            try:
                data = yaml.safe_load(text)
                if isinstance(data, dict):
                    self._parse_yaml_dict(data)
                    return self.schematic
            except yaml.YAMLError as exc:
                # If yaml parsing fails, check if fallback to DSL is better or raise
                mark = getattr(exc, "problem_mark", None)
                line_no = mark.line + 1 if mark else None
                raise ParseError(f"YAML syntax error: {exc}", line_num=line_no)

        # Otherwise parse as line-oriented Schematic DSL
        self._parse_dsl(text)
        return self.schematic

    def _is_structured_yaml(self, text: str) -> bool:
        """Checks if the document has standard YAML root keys like 'components:' or 'nets:'."""
        first_keys = ["components:", "nets:", "connections:"]
        for line in text.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("#") or line_s.startswith("//"):
                continue
            if any(line_s.startswith(k) for k in first_keys):
                return True
            break
        return False

    def _parse_yaml_dict(self, data: dict) -> None:
        # 1. Parse components
        comps_data = data.get("components", {})
        if isinstance(comps_data, dict):
            for comp_id, comp_info in comps_data.items():
                comp = self.schematic.get_or_create_component(str(comp_id))
                if isinstance(comp_info, dict):
                    comp.type_name = str(comp_info.get("type", comp_info.get("name", "")))
                    pins = comp_info.get("pins", [])
                    if isinstance(pins, list):
                        for p in pins:
                            p_str = str(p)
                            if "(" in p_str and p_str.endswith(")"):
                                name, label = p_str[:-1].split("(", 1)
                                comp.get_or_create_pin(name.strip(), label.strip())
                            else:
                                comp.get_or_create_pin(p_str.strip())
                elif isinstance(comp_info, str):
                    comp.type_name = comp_info

        # 2. Parse connections / nets
        connections_data = data.get("connections", [])
        if isinstance(connections_data, list):
            for idx, item in enumerate(connections_data, start=1):
                if isinstance(item, str):
                    self._parse_dsl_connection_line(item, line_num=idx)
                elif isinstance(item, dict):
                    # Check if YAML parsed "ConnLine : NetName" as a dict key-value
                    handled = False
                    for k, v in item.items():
                        if isinstance(k, str) and ("->" in k or "--" in k):
                            net_str = f" : {v}" if v else ""
                            self._parse_dsl_connection_line(f"{k}{net_str}", line_num=idx)
                            handled = True
                            break
                    if handled:
                        continue

                    # Otherwise e.g. { from: "ESP32.GND", to: ["Sensor.GND"], net: "GND" }
                    from_pin = item.get("from")
                    to_pins = item.get("to", [])
                    net_name = item.get("net", item.get("name", ""))
                    if isinstance(to_pins, str):
                        to_pins = [to_pins]
                    all_pins = [from_pin] + to_pins if from_pin else to_pins
                    self._add_net_by_pin_strings(all_pins, net_name=net_name, line_num=idx)

        nets_data = data.get("nets", [])
        if isinstance(nets_data, list):
            for idx, item in enumerate(nets_data, start=1):
                if isinstance(item, dict):
                    name = item.get("name", f"Net_{len(self.schematic.nets) + 1}")
                    connect_list = item.get("connect", item.get("pins", []))
                    if isinstance(connect_list, list):
                        self._add_net_by_pin_strings(connect_list, net_name=name, line_num=idx)

    def _parse_dsl(self, text: str) -> None:
        lines = text.splitlines()
        current_comp: Optional[Component] = None

        for line_idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            # Ignore empty lines and comments
            if not line or line.startswith("#") or line.startswith("//"):
                continue

            # Check if this line is a component declaration
            # Examples:
            #   ESP32: ESP32-WROOM-32
            #   [ESP32: ESP32-WROOM]
            #   SensorA: BME280
            #   pins: 3V3, GND, GPIO21(SDA), GPIO22(SCL)
            if line.startswith("pins:") or line.startswith("pins ="):
                if current_comp is None:
                    raise ParseError("'pins:' defined without a preceding component declaration", line_idx, raw_line)
                pins_text = line.split(":", 1)[-1] if ":" in line else line.split("=", 1)[-1]
                for p_token in pins_text.split(","):
                    p_token = p_token.strip()
                    if not p_token:
                        continue
                    if "(" in p_token and p_token.endswith(")"):
                        p_name, p_label = p_token[:-1].split("(", 1)
                        current_comp.get_or_create_pin(p_name.strip(), p_label.strip())
                    else:
                        current_comp.get_or_create_pin(p_token)
                continue

            # Check for "net <name>: Comp1.Pin1, Comp2.Pin2..."
            net_match = re.match(r"^net\s+([\w\-\.]+)\s*:\s*(.+)$", line, re.IGNORECASE)
            if net_match:
                net_name = net_match.group(1).strip()
                pin_targets = [p.strip() for p in net_match.group(2).split(",") if p.strip()]
                self._add_net_by_pin_strings(pin_targets, net_name=net_name, line_num=line_idx, line_content=raw_line)
                current_comp = None
                continue

            # Check for connection syntax: A.Pin -> B.Pin, C.Pin [: NetName]
            # Supports ->, <->, --, =>, etc.
            arrow_match = re.search(r"\s*(->|<-|<->|--|=>)\s*", line)
            if arrow_match:
                self._parse_dsl_connection_line(line, line_num=line_idx, line_content=raw_line)
                current_comp = None
                continue

            # Component header: "ESP32: ESP32-WROOM" or "[ESP32: ESP32-WROOM]" or "ESP32:"
            comp_match = re.match(r"^\[?\s*([A-Za-z0-9_\-]+)\s*(?::\s*([^\]]*))?\]?$", line)
            if comp_match:
                comp_id = comp_match.group(1).strip()
                type_name = (comp_match.group(2) or "").strip().rstrip("]")
                current_comp = self.schematic.get_or_create_component(comp_id, type_name)
                continue

            # If line has dots, perhaps user forgot the arrow, e.g. "ESP32.GND SDCard.GND"
            raise ParseError(
                f"Unrecognized syntax: '{line}'. Expected component declaration ('Name: Type') or connection ('A.Pin -> B.Pin')",
                line_num=line_idx,
                line_content=raw_line,
            )

    def _parse_dsl_connection_line(self, line: str, line_num: int, line_content: Optional[str] = None) -> None:
        """
        Parses connection lines like:
        ESP32.GND -> SDCard.GND, SensorA.GND
        ESP32.GPIO21(SDA) -> SensorA.SDA, OLED.SDA : I2C_SDA
        ESP32.3V3 -> SensorA.VCC [net=3V3]
        """
        # Extract optional net name at end (: Name or [net=Name])
        net_name = ""
        clean_line = line

        # Check for [net=...] or [name=...]
        attr_match = re.search(r"\[(?:net|name)=([^\]]+)\]", clean_line, re.IGNORECASE)
        if attr_match:
            net_name = attr_match.group(1).strip()
            clean_line = clean_line[:attr_match.start()] + clean_line[attr_match.end():]

        # Check for : NetName at the very end
        # We need to make sure we don't confuse with component:type
        if ":" in clean_line:
            parts = clean_line.rsplit(":", 1)
            # Only treat as net name if the part after colon does NOT contain dots or arrows
            if "." not in parts[1] and "->" not in parts[1] and "--" not in parts[1]:
                net_name = parts[1].strip()
                clean_line = parts[0].strip()

        # Find the arrow delimiter
        arrow_match = re.search(r"\s*(->|<-|<->|--|=>)\s*", clean_line)
        if not arrow_match:
            raise ParseError("Connection line missing arrow ('->', '--')", line_num, line_content)

        source_part = clean_line[:arrow_match.start()].strip()
        target_part = clean_line[arrow_match.end():].strip()

        if not source_part:
            raise ParseError("Missing source pin before arrow", line_num, line_content)
        if not target_part:
            raise ParseError("Missing target pin(s) after arrow", line_num, line_content)

        # Source pin
        src_comp, src_pin, src_label = parse_pin_ref(source_part, line_num, line_content)
        comp_obj = self.schematic.get_or_create_component(src_comp)
        comp_obj.get_or_create_pin(src_pin, src_label)

        # Target pins (comma-separated list)
        targets: List[Tuple[str, str, str]] = []
        for t_token in target_part.split(","):
            t_token = t_token.strip()
            if not t_token:
                continue
            t_comp, t_pin, t_label = parse_pin_ref(t_token, line_num, line_content)
            t_comp_obj = self.schematic.get_or_create_component(t_comp)
            t_comp_obj.get_or_create_pin(t_pin, t_label)
            targets.append((t_comp, t_pin, t_label))

        if not targets:
            raise ParseError("No valid target pin found after arrow", line_num, line_content)

        # Auto-name net if not given
        if not net_name:
            # Common sensible net names like GND, 3V3, 5V, VCC, SDA, SCL
            common_signals = {"gnd", "3v3", "5v", "vcc", "vin", "sda", "scl", "miso", "mosi", "sck", "cs", "rst", "tx", "rx"}
            src_lower = src_pin.lower()
            if src_lower in common_signals:
                net_name = src_pin.upper()
            elif src_label and src_label.lower() in common_signals:
                net_name = src_label.upper()
            else:
                net_name = f"{src_comp}_{src_pin}"

        # Group or merge into Net
        existing_net = next((n for n in self.schematic.nets if n.name == net_name), None)
        if existing_net is None:
            existing_net = Net(name=net_name)
            self.schematic.nets.append(existing_net)

        existing_net.add_connection(src_comp, src_pin)
        for t_comp, t_pin, _ in targets:
            existing_net.add_connection(t_comp, t_pin)

    def _add_net_by_pin_strings(self, pin_strings: List[str], net_name: str = "", line_num: Optional[int] = None, line_content: Optional[str] = None) -> None:
        if not pin_strings:
            return
        parsed_pins = []
        for p_str in pin_strings:
            p_str = p_str.strip()
            if not p_str:
                continue
            c_name, p_name, p_label = parse_pin_ref(p_str, line_num, line_content)
            comp_obj = self.schematic.get_or_create_component(c_name)
            comp_obj.get_or_create_pin(p_name, p_label)
            parsed_pins.append((c_name, p_name))

        if not parsed_pins:
            return

        if not net_name:
            net_name = f"{parsed_pins[0][0]}_{parsed_pins[0][1]}"

        net = next((n for n in self.schematic.nets if n.name == net_name), None)
        if net is None:
            net = Net(name=net_name)
            self.schematic.nets.append(net)

        for c_name, p_name in parsed_pins:
            net.add_connection(c_name, p_name)


def parse_schematic(text: str) -> Schematic:
    parser = SchematicParser()
    return parser.parse(text)
