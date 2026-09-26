"""Professional SVG renderer for 2D electronic schematics."""

from __future__ import annotations
import html
import math
from typing import List, Optional
from schematic.models import Component, Pin, Point, Schematic, Side, WireJunction, WireSegment


class SvgRenderer:
    def __init__(self, schematic: Schematic, dark_theme: bool = True):
        self.schematic = schematic
        self.dark_theme = dark_theme

    def render(self) -> str:
        # Theme palette
        if self.dark_theme:
            bg_color = "#0f172a"          # Deep slate background
            grid_color = "#1e293b"        # Subtle grid dots
            comp_bg = "#1e293b"           # Component card fill
            comp_border = "#334155"       # Component border
            comp_header_bg = "#0f172a"    # Component title banner
            text_title = "#f1f5f9"        # Component title text
            text_subtitle = "#94a3b8"     # Component subtitle
            text_pin = "#cbd5e1"          # Pin label text
            pin_lead_color = "#64748b"    # Pin stub line
            terminal_circle = "#94a3b8"   # Pin terminal dot
            junction_stroke = "#0f172a"   # Junction circle outline
        else:
            bg_color = "#f8fafc"
            grid_color = "#e2e8f0"
            comp_bg = "#ffffff"
            comp_border = "#cbd5e1"
            comp_header_bg = "#f1f5f9"
            text_title = "#0f172a"
            text_subtitle = "#64748b"
            text_pin = "#334155"
            pin_lead_color = "#94a3b8"
            terminal_circle = "#64748b"
            junction_stroke = "#ffffff"

        pad = 60.0
        raw_min_x = min([c.x - 50.0 for c in self.schematic.components.values()] + [w.start.x for w in self.schematic.wires] + [0.0]) - pad
        raw_min_y = min([c.y - 50.0 for c in self.schematic.components.values()] + [w.start.y for w in self.schematic.wires] + [0.0]) - pad
        raw_max_x = max([c.x + c.width + 50.0 for c in self.schematic.components.values()] + [w.end.x for w in self.schematic.wires] + [self.schematic.width]) + pad
        raw_max_y = max([c.y + c.height + 50.0 for c in self.schematic.components.values()] + [w.end.y for w in self.schematic.wires] + [self.schematic.height]) + pad

        min_x = math.floor(raw_min_x / 20.0) * 20.0
        min_y = math.floor(raw_min_y / 20.0) * 20.0
        max_x = math.ceil(raw_max_x / 20.0) * 20.0
        max_y = math.ceil(raw_max_y / 20.0) * 20.0

        vb_w = max(max_x - min_x, 600.0)
        vb_h = max(max_y - min_y, 450.0)

        svg = []
        svg.append(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{min_x:.1f} {min_y:.1f} {vb_w:.1f} {vb_h:.1f}" '
            f'class="schematic-canvas" '
            f'shape-rendering="geometricPrecision" text-rendering="geometricPrecision">'
        )
        svg.append('<defs>')
        # Dot grid pattern strictly aligned to coordinate (0, 0)
        svg.append(f'<pattern id="schematic-grid" width="20" height="20" patternUnits="userSpaceOnUse">')
        svg.append(f'<circle cx="0" cy="0" r="1.3" fill="{grid_color}" />')
        svg.append('</pattern>')
        # Drop shadow for components
        svg.append('<filter id="comp-shadow" x="-5%" y="-5%" width="110%" height="115%">')
        svg.append('<feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.25"/>')
        svg.append('</filter>')
        svg.append('</defs>')

        # Background grid
        bg_pad = 50000.0
        svg.append(f'<rect x="{min_x - bg_pad:.1f}" y="{min_y - bg_pad:.1f}" width="{vb_w + 2 * bg_pad:.1f}" height="{vb_h + 2 * bg_pad:.1f}" fill="{bg_color}" />')
        svg.append(f'<rect x="{min_x - bg_pad:.1f}" y="{min_y - bg_pad:.1f}" width="{vb_w + 2 * bg_pad:.1f}" height="{vb_h + 2 * bg_pad:.1f}" fill="url(#schematic-grid)" />')

        # Layer 1: Wires
        svg.append('<g id="schematic-wires" stroke-linecap="round" stroke-linejoin="round">')
        for wire in self.schematic.wires:
            w_color = wire.color or ("#38bdf8" if self.dark_theme else "#0284c7")
            svg.append(
                f'<line x1="{wire.start.x:.1f}" y1="{wire.start.y:.1f}" '
                f'x2="{wire.end.x:.1f}" y2="{wire.end.y:.1f}" '
                f'stroke="{w_color}" stroke-width="2.2" />'
            )
        svg.append('</g>')

        # Layer 2: Junction Dots
        svg.append('<g id="schematic-junctions">')
        for junc in self.schematic.junctions:
            j_color = self._get_net_color(junc.net_name)
            svg.append(
                f'<circle cx="{junc.point.x:.1f}" cy="{junc.point.y:.1f}" r="{junc.radius:.1f}" '
                f'fill="{j_color}" stroke="{junction_stroke}" stroke-width="1.5" />'
            )
        svg.append('</g>')

        # Layer 3: Components
        svg.append('<g id="schematic-components">')
        for comp in self.schematic.components.values():
            svg.append(self._render_component(comp, comp_bg, comp_border, comp_header_bg, text_title, text_subtitle, text_pin, pin_lead_color, terminal_circle))
        svg.append('</g>')

        svg.append('</svg>')
        return "\n".join(svg)

    def _render_component(
        self,
        comp: Component,
        comp_bg: str,
        comp_border: str,
        header_bg: str,
        text_title: str,
        text_subtitle: str,
        text_pin: str,
        pin_lead_color: str,
        terminal_circle: str,
    ) -> str:
        parts = []
        parts.append(f'<g id="comp-{comp.id}" class="component-box" transform="translate(0, 0)">')

        # Main component card
        parts.append(
            f'<rect x="{comp.x:.1f}" y="{comp.y:.1f}" width="{comp.width:.1f}" height="{comp.height:.1f}" '
            f'rx="4" ry="4" fill="{comp_bg}" stroke="{comp_border}" stroke-width="1.6" filter="url(#comp-shadow)" />'
        )

        # Header banner (40px = 2 grid units)
        header_h = 40.0
        parts.append(
            f'<path d="M {comp.x:.1f},{comp.y + 4.0:.1f} a 4,4 0 0,1 4,-4 l {comp.width - 8.0:.1f},0 a 4,4 0 0,1 4,4 l 0,{header_h - 4.0:.1f} l {-comp.width:.1f},0 z" '
            f'fill="{header_bg}" />'
        )
        parts.append(
            f'<line x1="{comp.x:.1f}" y1="{comp.y + header_h:.1f}" x2="{comp.x + comp.width:.1f}" y2="{comp.y + header_h:.1f}" '
            f'stroke="{comp_border}" stroke-width="1.2" />'
        )

        # Title text
        escaped_id = html.escape(comp.id)
        parts.append(
            f'<text x="{comp.x + 10.0:.1f}" y="{comp.y + 18.0:.1f}" '
            f'font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="700" fill="{text_title}">'
            f'{escaped_id}</text>'
        )

        # Subtitle / Type text
        if comp.type_name:
            escaped_type = html.escape(comp.type_name)
            parts.append(
                f'<text x="{comp.x + 10.0:.1f}" y="{comp.y + 31.0:.1f}" '
                f'font-family="system-ui, -apple-system, sans-serif" font-size="9" font-weight="500" fill="{text_subtitle}">'
                f'{escaped_type}</text>'
            )

        # Pins
        for pin in comp.pins.values():
            parts.append(self._render_pin(pin, pin_lead_color, terminal_circle, text_pin))

        parts.append('</g>')
        return "\n".join(parts)

    def _render_pin(self, pin: Pin, lead_color: str, terminal_color: str, text_color: str) -> str:
        parts = []
        # Pin stub line from component border to terminal position
        parts.append(
            f'<line x1="{pin.lead_start.x:.1f}" y1="{pin.lead_start.y:.1f}" '
            f'x2="{pin.position.x:.1f}" y2="{pin.position.y:.1f}" '
            f'stroke="{lead_color}" stroke-width="1.6" />'
        )
        # Terminal circle
        parts.append(
            f'<circle cx="{pin.position.x:.1f}" cy="{pin.position.y:.1f}" r="2.2" '
            f'fill="{terminal_color}" />'
        )

        # Pin label text inside component
        disp_name = html.escape(pin.display_name)
        if pin.side == Side.LEFT:
            tx = pin.lead_start.x + 8.0
            ty = pin.lead_start.y + 3.5
            anchor = "start"
        elif pin.side == Side.RIGHT:
            tx = pin.lead_start.x - 8.0
            ty = pin.lead_start.y + 3.5
            anchor = "end"
        elif pin.side == Side.TOP:
            tx = pin.lead_start.x
            ty = pin.lead_start.y + 14.0
            anchor = "middle"
        else:  # BOTTOM
            tx = pin.lead_start.x
            ty = pin.lead_start.y - 8.0
            anchor = "middle"

        parts.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" '
            f'font-size="10" font-weight="500" fill="{text_color}">'
            f'{disp_name}</text>'
        )

        return "\n".join(parts)

    def _get_net_color(self, net_name: str) -> str:
        name_u = net_name.upper()

        if any(k in name_u for k in ["GND", "VSS", "NEG", "BATT-"]):
            return "#64748b" if self.dark_theme else "#475569"

        if any(k in name_u for k in ["BATT_POS", "BATT_VCC", "DCDC_IN", "36V", "48V", "BAT_IN", "LOAD_OUT", "RAW", "HV"]):
            return "#f97316" if self.dark_theme else "#ea580c"

        if any(k in name_u for k in ["3V3", "5V", "VCC", "VIN", "VDD", "VOUT", "PWR_IN", "PWR_OUT", "DISP_VCC"]):
            return "#ef4444" if self.dark_theme else "#dc2626"

        if any(k in name_u for k in ["SHUNT", "SENSE", "ANALOG", "ADC"]):
            return "#ec4899" if self.dark_theme else "#db2777"

        if any(k in name_u for k in ["SPEED", "INT", "IRQ", "PWM", "PULSE"]):
            return "#06b6d4" if self.dark_theme else "#0891b2"

        if "SDA" in name_u:
            return "#38bdf8" if self.dark_theme else "#0284c7"
        if "SCL" in name_u:
            return "#10b981" if self.dark_theme else "#059669"

        if "MOSI" in name_u:
            return "#818cf8" if self.dark_theme else "#4f46e5"
        if "MISO" in name_u:
            return "#c084fc" if self.dark_theme else "#9333ea"
        if "SCK" in name_u or "CLK" in name_u:
            return "#10b981" if self.dark_theme else "#059669"
        if "CS" in name_u or "SS" in name_u:
            return "#fbbf24" if self.dark_theme else "#d97706"

        if "TX" in name_u:
            return "#f59e0b" if self.dark_theme else "#d97706"
        if "RX" in name_u:
            return "#2dd4bf" if self.dark_theme else "#0d9488"

        if any(k in name_u for k in ["KEY", "EN", "RST", "RESET", "CTRL"]):
            return "#a78bfa" if self.dark_theme else "#7c3aed"

        return "#38bdf8" if self.dark_theme else "#0284c7"


def render_schematic_to_svg(schematic: Schematic, dark_theme: bool = True) -> str:
    renderer = SvgRenderer(schematic, dark_theme=dark_theme)
    return renderer.render()
