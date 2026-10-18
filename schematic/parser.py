"""Parser for text-based schematic definitions (DSL and YAML)."""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple
import yaml

from schematic.models import Component, Net, NetConnection, PartTemplate, Pin, Schematic, Side, normalize_kind


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

        # Resolve custom part templates for components
        for comp in self.schematic.components.values():
            part = self.schematic.parts.get(comp.type_name) or self.schematic.parts.get(comp.id)
            if part and not comp.image:
                self._apply_part_template(comp, part)

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

    def _parse_side_pins(self, comp: Component, text: str, side: Side, side_specified: bool = True) -> None:
        """Parses comma-separated pin tokens and adds them to the component in the specified order and side."""
        current_order = len([p for p in comp.pins.values() if p.side == side])
        for p_token in text.split(","):
            p_token = p_token.strip()
            if not p_token:
                continue
            if "(" in p_token and p_token.endswith(")"):
                p_name, p_label = p_token[:-1].split("(", 1)
                comp.add_pin(
                    name=p_name.strip(),
                    label=p_label.strip(),
                    side=side,
                    order=current_order,
                    side_specified=side_specified,
                )
            else:
                comp.add_pin(
                    name=p_token,
                    side=side,
                    order=current_order,
                    side_specified=side_specified,
                )
            current_order += 1

    def _parse_yaml_dict(self, data: dict) -> None:
        # 1. Parse components
        comps_data = data.get("components", {})
        if isinstance(comps_data, dict):
            for comp_id, comp_info in comps_data.items():
                comp = self.schematic.get_or_create_component(str(comp_id))
                if isinstance(comp_info, dict):
                    comp.type_name = str(comp_info.get("type", comp_info.get("name", "")))
                    if "kind" in comp_info:
                        comp.set_kind(str(comp_info["kind"]))
                    elif "symbol" in comp_info:
                        comp.set_kind(str(comp_info["symbol"]))
                    elif "standard" in comp_info:
                        comp.set_kind(str(comp_info["standard"]))
                    if "value" in comp_info:
                        comp.value = str(comp_info["value"])
                    elif "val" in comp_info:
                        comp.value = str(comp_info["val"])
                    comp.detect_standard_type()

                    for side_str, side_enum in [("left", Side.LEFT), ("right", Side.RIGHT), ("top", Side.TOP), ("bottom", Side.BOTTOM)]:
                        raw_pins = comp_info.get(side_str) or comp_info.get(f"{side_str}_pins")
                        if raw_pins:
                            if isinstance(raw_pins, list):
                                raw_pins = ", ".join(str(p) for p in raw_pins)
                            self._parse_side_pins(comp, str(raw_pins), side_enum, side_specified=True)

                    pins_val = comp_info.get("pins", [])
                    if isinstance(pins_val, dict):
                        for side_str, side_enum in [("left", Side.LEFT), ("right", Side.RIGHT), ("top", Side.TOP), ("bottom", Side.BOTTOM)]:
                            raw_pins = pins_val.get(side_str)
                            if raw_pins:
                                if isinstance(raw_pins, list):
                                    raw_pins = ", ".join(str(p) for p in raw_pins)
                                self._parse_side_pins(comp, str(raw_pins), side_enum, side_specified=True)
                    elif isinstance(pins_val, list):
                        self._parse_side_pins(comp, ", ".join(str(p) for p in pins_val), Side.LEFT, side_specified=False)
                    elif isinstance(pins_val, str) and pins_val.strip():
                        self._parse_side_pins(comp, pins_val, Side.LEFT, side_specified=False)
                elif isinstance(comp_info, str):
                    comp.type_name = comp_info
                    comp.detect_standard_type()

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

    def _apply_part_template(self, comp: Component, part: PartTemplate) -> None:
        if not comp.image:
            comp.image = part.image
        if not comp.grid_width:
            comp.grid_width = part.grid_width
        if not comp.grid_height:
            comp.grid_height = part.grid_height
        if comp.image_rect is None and part.image_rect is not None:
            comp.image_rect = part.image_rect
        for p_name, (gx, gy, side_enum) in part.pins.items():
            lbl_m = re.match(r"^([^\(\)]+)(?:\((.*)\))?$", p_name)
            if lbl_m and lbl_m.group(2):
                base_name = lbl_m.group(1).strip()
                base_label = lbl_m.group(2).strip()
            else:
                base_name = p_name
                base_label = ""

            p = comp.get_pin(base_name) or comp.get_pin(p_name)
            if p is None:
                comp.add_pin(
                    base_name,
                    label=base_label,
                    grid_x=gx,
                    grid_y=gy,
                    side=side_enum or Side.LEFT,
                    side_specified=bool(side_enum),
                )
            else:
                p.grid_x = gx
                p.grid_y = gy
                if base_label and not p.label:
                    p.label = base_label
                if side_enum:
                    p.side = side_enum
                    p.side_specified = True

    def _parse_dsl(self, text: str) -> None:
        lines = text.splitlines()
        current_comp: Optional[Component] = None
        current_part: Optional[PartTemplate] = None

        for line_idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            # Ignore empty lines and comments
            if not line or line.startswith("#") or line.startswith("//"):
                continue

            # Check for part definition header: "part ESP32:"
            part_match = re.match(r"^part\s+([A-Za-z0-9_\-]+)\s*:$", line, re.IGNORECASE)
            if part_match:
                part_name = part_match.group(1).strip()
                current_part = self.schematic.get_or_create_part(part_name)
                current_comp = None
                continue

            # Check for image declaration: "image: parts/esp32.svg"
            image_match = re.match(r"^image\s*[:=]\s*(.+)$", line, re.IGNORECASE)
            if image_match:
                img_val = image_match.group(1).strip().strip('"\'')
                if current_part is not None:
                    current_part.image = img_val
                elif current_comp is not None:
                    current_comp.image = img_val
                else:
                    raise ParseError(f"'{line}' defined without a preceding component or part declaration", line_idx, raw_line)
                continue

            # Check for image_rect declaration: "image_rect: -10, -5, 200, 370"
            image_rect_match = re.match(r"^(?:image_rect|img_rect)\s*[:=]\s*(.+)$", line, re.IGNORECASE)
            if image_rect_match:
                r_val = image_rect_match.group(1).strip()
                r_parts = [float(p.strip()) for p in re.split(r"[, \s]+", r_val) if p.strip()]
                if len(r_parts) >= 4:
                    rect_val = (r_parts[0], r_parts[1], r_parts[2], r_parts[3])
                    if current_part is not None:
                        current_part.image_rect = rect_val
                    elif current_comp is not None:
                        current_comp.image_rect = rect_val
                continue

            # Check for size declaration: "size: 9x18" or "size: 9, 18"
            size_match = re.match(r"^size\s*[:=]\s*(.+)$", line, re.IGNORECASE)
            if size_match:
                s_val = size_match.group(1).strip()
                parts = re.split(r"[xX,\s]+", s_val)
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    gw, gh = int(parts[0]), int(parts[1])
                    if current_part is not None:
                        current_part.grid_width = gw
                        current_part.grid_height = gh
                    elif current_comp is not None:
                        current_comp.grid_width = gw
                        current_comp.grid_height = gh
                continue

            # Check for explicit pin side declarations:
            # Examples:
            #   left: 3V3, EN, GPIO36, GND
            #   right: GND, GPIO23, GPIO22
            #   top: VIN, VOUT
            #   bottom: GND
            #   left_pins: ...
            #   pins.left: ...
            side_match = re.match(r"^(?:pins\.)?(left|right|top|bottom)(?:_pins)?\s*[:=]\s*(.*)$", line, re.IGNORECASE)
            if side_match:
                if current_comp is None:
                    raise ParseError(f"'{line}' defined without a preceding component declaration", line_idx, raw_line)
                side_str = side_match.group(1).lower()
                pins_content = side_match.group(2).strip()
                side_enum = {
                    "left": Side.LEFT,
                    "right": Side.RIGHT,
                    "top": Side.TOP,
                    "bottom": Side.BOTTOM,
                }[side_str]
                if pins_content:
                    self._parse_side_pins(current_comp, pins_content, side_enum, side_specified=True)
                continue

            # Check if line is empty "pins:" header (to be followed by indented left:, right:, etc.)
            if re.match(r"^pins\s*[:=]\s*$", line, re.IGNORECASE):
                if current_comp is None and current_part is None:
                    raise ParseError("'pins:' defined without a preceding component or part declaration", line_idx, raw_line)
                continue

            # Generic pins line: pins: 3V3, GND, GPIO21(SDA)
            pins_match = re.match(r"^pins\s*[:=]\s*(.+)$", line, re.IGNORECASE)
            if pins_match:
                if current_comp is None:
                    raise ParseError("'pins:' defined without a preceding component declaration", line_idx, raw_line)
                pins_text = pins_match.group(1).strip()
                self._parse_side_pins(current_comp, pins_text, Side.LEFT, side_specified=False)
                continue

            # Check for kind / symbol / standard declaration:
            # Examples:
            #   kind: resistor
            #   symbol: capacitor
            #   standard: switch
            #   standard: true
            kind_match = re.match(r"^(?:kind|symbol|standard)\s*[:=]\s*(.+)$", line, re.IGNORECASE)
            if kind_match:
                if current_comp is None:
                    raise ParseError(f"'{line}' defined without a preceding component declaration", line_idx, raw_line)
                k_val = kind_match.group(1).strip()
                current_comp.set_kind(k_val)
                continue

            # Check for value declaration:
            # Examples:
            #   value: 2kOhm
            #   val: 100nF
            val_match = re.match(r"^(?:value|val)\s*[:=]\s*(.+)$", line, re.IGNORECASE)
            if val_match:
                if current_comp is None:
                    raise ParseError(f"'{line}' defined without a preceding component declaration", line_idx, raw_line)
                current_comp.value = val_match.group(1).strip()
                continue

            # Check for pin coordinate declaration: "3V3: 0, 2" or "GPIO21(SDA): 8, 5, right"
            coord_pin_match = re.match(r"^([^:\s][^:]*?)\s*:\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([a-zA-Z]+))?$", line)
            if coord_pin_match:
                p_raw = coord_pin_match.group(1).strip()
                if p_raw.lower() not in ("pins", "image", "image_rect", "img_rect", "size", "kind", "symbol", "standard", "value", "val", "net", "left", "right", "top", "bottom"):
                    gx = int(coord_pin_match.group(2))
                    gy = int(coord_pin_match.group(3))
                    side_str = coord_pin_match.group(4).lower() if coord_pin_match.group(4) else None
                    side_enum = {"left": Side.LEFT, "right": Side.RIGHT, "top": Side.TOP, "bottom": Side.BOTTOM}.get(side_str) if side_str else None

                    lbl_m = re.match(r"^([^\(\)]+)(?:\((.*)\))?$", p_raw)
                    if lbl_m and lbl_m.group(2):
                        p_name = lbl_m.group(1).strip()
                        p_label = lbl_m.group(2).strip()
                    else:
                        p_name = p_raw
                        p_label = ""

                    if current_part is not None:
                        current_part.pins[p_raw] = (gx, gy, side_enum)
                        continue
                    elif current_comp is not None:
                        current_comp.add_pin(p_name, label=p_label, grid_x=gx, grid_y=gy, side=side_enum or Side.LEFT, side_specified=bool(side_enum))
                        continue
                    else:
                        raise ParseError(f"'{line}' defined without a preceding component or part declaration", line_idx, raw_line)

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
            # Also supports inline attributes e.g. "SpeedPullUp [kind=resistor, value=2kOhm]:" or "SpeedPullUp: 2kOhm [resistor]"
            comp_match = re.match(r"^\[?\s*([A-Za-z0-9_\-]+)\s*(?:\[([^\]]*)\])?\s*(?::\s*([^\]]*))?\]?$", line)
            if comp_match:
                comp_id = comp_match.group(1).strip()
                bracket_attrs = comp_match.group(2)
                type_name = (comp_match.group(3) or "").strip().rstrip("]")
                current_part = None
                current_comp = self.schematic.get_or_create_component(comp_id, type_name)
                if type_name and type_name in self.schematic.parts:
                    self._apply_part_template(current_comp, self.schematic.parts[type_name])
                if bracket_attrs:
                    self._parse_inline_comp_attrs(current_comp, bracket_attrs)
                if "[" in type_name and type_name.endswith("]"):
                    b_start = type_name.rfind("[")
                    inline_b = type_name[b_start + 1:-1]
                    type_name = type_name[:b_start].strip()
                    current_comp.type_name = type_name
                    self._parse_inline_comp_attrs(current_comp, inline_b)
                current_comp.detect_standard_type()
                continue

            # If line has dots, perhaps user forgot the arrow, e.g. "ESP32.GND SDCard.GND"
            raise ParseError(
                f"Unrecognized syntax: '{line}'. Expected component declaration ('Name: Type') or connection ('A.Pin -> B.Pin')",
                line_num=line_idx,
                line_content=raw_line,
            )

    def _parse_inline_comp_attrs(self, comp: Component, attrs_str: str) -> None:
        """Parses inline component attributes e.g. 'kind=resistor, value=2kOhm' or 'resistor, 2kOhm'."""
        parts = [p.strip() for p in attrs_str.split(",") if p.strip()]
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                k = k.strip().lower()
                v = v.strip().strip("\"'")
                if k in ("kind", "symbol", "standard"):
                    comp.set_kind(v)
                elif k in ("value", "val"):
                    comp.value = v
            else:
                norm = normalize_kind(p)
                if norm:
                    comp.set_kind(norm)
                elif not comp.value:
                    comp.value = p.strip("\"'")

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
        auto_named = False
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
                auto_named = True

        # Group or merge into Net
        existing_net = next((n for n in self.schematic.nets if n.name == net_name), None)
        if existing_net is None:
            existing_net = Net(name=net_name, auto_named=auto_named)
            self.schematic.nets.append(existing_net)

        target_comp_ids = [t[0] for t in targets]
        existing_net.add_connection(src_comp, src_pin, peer_comp_ids=target_comp_ids)
        for t_comp, t_pin, _ in targets:
            existing_net.add_connection(t_comp, t_pin, peer_comp_ids=[src_comp])

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

        auto_named = not net_name
        if not net_name:
            net_name = f"{parsed_pins[0][0]}_{parsed_pins[0][1]}"

        net = next((n for n in self.schematic.nets if n.name == net_name), None)
        if net is None:
            net = Net(name=net_name, auto_named=auto_named)
            self.schematic.nets.append(net)

        for c_name, p_name in parsed_pins:
            peers = [other[0] for other in parsed_pins if other[0] != c_name]
            net.add_connection(c_name, p_name, peer_comp_ids=peers)


def parse_schematic(text: str) -> Schematic:
    parser = SchematicParser()
    return parser.parse(text)
