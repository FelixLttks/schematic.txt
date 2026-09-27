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
        # Universal drop shadow for components (SVG 1.1 / Inkscape compatible)
        svg.append('<filter id="comp-shadow" x="-10%" y="-10%" width="120%" height="125%">')
        svg.append('  <feGaussianBlur in="SourceAlpha" stdDeviation="2.5" />')
        svg.append('  <feOffset dx="0" dy="2" result="offsetblur" />')
        svg.append('  <feComponentTransfer><feFuncA type="linear" slope="0.2" /></feComponentTransfer>')
        svg.append('  <feMerge><feMergeNode /><feMergeNode in="SourceGraphic" /></feMerge>')
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
            if comp.is_standard:
                svg.append(self._render_standard_component(comp, comp_bg, comp_border, text_title, pin_lead_color, terminal_circle))
            else:
                svg.append(self._render_component(comp, comp_bg, comp_border, comp_header_bg, text_title, text_subtitle, text_pin, pin_lead_color, terminal_circle))
        svg.append('</g>')

        svg.append('</svg>')
        return "\n".join(svg)

    def _render_standard_component(
        self,
        comp: Component,
        comp_bg: str,
        comp_border: str,
        text_title: str,
        pin_lead_color: str,
        terminal_circle: str,
    ) -> str:
        parts = []
        k = comp.kind.lower() if comp.kind else "resistor"
        parts.append(f'<g id="comp-{comp.id}" class="component-standard component-{k}" transform="translate(0, 0)">')

        symbol_stroke = "#cbd5e1" if self.dark_theme else "#334155"
        box_stroke = "#94a3b8" if self.dark_theme else "#475569"
        text_val_color = "#38bdf8" if self.dark_theme else "#0284c7"

        escaped_id = html.escape(comp.id)
        escaped_val = html.escape(comp.value) if comp.value else ""

        is_vertical = comp.height > comp.width
        cx = comp.x + comp.width / 2.0
        cy = comp.y + comp.height / 2.0

        if k == "resistor":
            if is_vertical:
                parts.append(f'<line x1="{cx:.1f}" y1="{comp.y:.1f}" x2="{cx:.1f}" y2="{cy - 20.0:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<line x1="{cx:.1f}" y1="{cy + 20.0:.1f}" x2="{cx:.1f}" y2="{comp.y + comp.height:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<rect x="{cx - 8.0:.1f}" y="{cy - 20.0:.1f}" width="16.0" height="40.0" rx="2" ry="2" fill="{comp_bg}" stroke="{box_stroke}" stroke-width="1.8" />')
                parts.append(f'<text x="{cx + 12.0:.1f}" y="{cy - 4.0:.1f}" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
                if escaped_val:
                    parts.append(f'<text x="{cx + 12.0:.1f}" y="{cy + 10.0:.1f}" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')
            else:
                parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{cx - 20.0:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<line x1="{cx + 20.0:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<rect x="{cx - 20.0:.1f}" y="{cy - 8.0:.1f}" width="40.0" height="16.0" rx="2" ry="2" fill="{comp_bg}" stroke="{box_stroke}" stroke-width="1.8" />')
                parts.append(f'<text x="{cx:.1f}" y="{comp.y + 7.0:.1f}" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
                if escaped_val:
                    if len(escaped_val) <= 6:
                        parts.append(f'<text x="{cx:.1f}" y="{cy + 3.2:.1f}" text-anchor="middle" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')
                    else:
                        parts.append(f'<text x="{cx:.1f}" y="{comp.y + 36.0:.1f}" text-anchor="middle" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')

        elif k == "capacitor":
            if is_vertical:
                parts.append(f'<line x1="{cx:.1f}" y1="{comp.y:.1f}" x2="{cx:.1f}" y2="{cy - 4.0:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<line x1="{cx:.1f}" y1="{cy + 4.0:.1f}" x2="{cx:.1f}" y2="{comp.y + comp.height:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<line x1="{cx - 12.0:.1f}" y1="{cy - 4.0:.1f}" x2="{cx + 12.0:.1f}" y2="{cy - 4.0:.1f}" stroke="{symbol_stroke}" stroke-width="2.4" stroke-linecap="round" />')
                parts.append(f'<line x1="{cx - 12.0:.1f}" y1="{cy + 4.0:.1f}" x2="{cx + 12.0:.1f}" y2="{cy + 4.0:.1f}" stroke="{symbol_stroke}" stroke-width="2.4" stroke-linecap="round" />')
                parts.append(f'<text x="{cx + 16.0:.1f}" y="{cy - 3.0:.1f}" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
                if escaped_val:
                    parts.append(f'<text x="{cx + 16.0:.1f}" y="{cy + 10.0:.1f}" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')
            else:
                parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{cx - 4.0:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<line x1="{cx + 4.0:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<line x1="{cx - 4.0:.1f}" y1="{cy - 12.0:.1f}" x2="{cx - 4.0:.1f}" y2="{cy + 12.0:.1f}" stroke="{symbol_stroke}" stroke-width="2.4" stroke-linecap="round" />')
                parts.append(f'<line x1="{cx + 4.0:.1f}" y1="{cy - 12.0:.1f}" x2="{cx + 4.0:.1f}" y2="{cy + 12.0:.1f}" stroke="{symbol_stroke}" stroke-width="2.4" stroke-linecap="round" />')
                parts.append(f'<text x="{cx:.1f}" y="{comp.y + 5.0:.1f}" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
                if escaped_val:
                    parts.append(f'<text x="{cx:.1f}" y="{comp.y + 36.0:.1f}" text-anchor="middle" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')

        elif k == "switch":
            parts.append(f'<text x="{cx:.1f}" y="{comp.y - 6.0:.1f}" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
            if escaped_val:
                parts.append(f'<text x="{cx:.1f}" y="{comp.y + comp.height + 15.0:.1f}" text-anchor="middle" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')

            lp = [p for p in comp.pins.values() if p.side == Side.LEFT]
            rp = [p for p in comp.pins.values() if p.side == Side.RIGHT]

            if len(comp.pins) == 3:
                if len(lp) == 1 and len(rp) >= 2:
                    # Common on Left, Throws on Right
                    parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{comp.x + 18.0:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<circle cx="{comp.x + 18.0:.1f}" cy="{cy:.1f}" r="2.4" fill="{symbol_stroke}" />')
                    parts.append(f'<circle cx="{comp.x + 42.0:.1f}" cy="{comp.y:.1f}" r="2.2" fill="none" stroke="{symbol_stroke}" stroke-width="1.6" />')
                    parts.append(f'<line x1="{comp.x + 44.2:.1f}" y1="{comp.y:.1f}" x2="{comp.x + comp.width:.1f}" y2="{comp.y:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<circle cx="{comp.x + 42.0:.1f}" cy="{comp.y + comp.height:.1f}" r="2.2" fill="none" stroke="{symbol_stroke}" stroke-width="1.6" />')
                    parts.append(f'<line x1="{comp.x + 44.2:.1f}" y1="{comp.y + comp.height:.1f}" x2="{comp.x + comp.width:.1f}" y2="{comp.y + comp.height:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<line x1="{comp.x + 18.0:.1f}" y1="{cy:.1f}" x2="{comp.x + 40.0:.1f}" y2="{comp.y + 2.5:.1f}" stroke="{symbol_stroke}" stroke-width="2.2" stroke-linecap="round" />')
                    if lp[0].display_name:
                        parts.append(f'<text x="{comp.x + 4.0:.1f}" y="{cy - 5.0:.1f}" text-anchor="start" font-family="ui-monospace, monospace" font-size="8" font-weight="600" fill="{text_val_color}">{html.escape(lp[0].display_name)}</text>')
                    if rp[0].display_name:
                        parts.append(f'<text x="{comp.x + comp.width - 4.0:.1f}" y="{comp.y - 4.0:.1f}" text-anchor="end" font-family="ui-monospace, monospace" font-size="8" font-weight="600" fill="{text_val_color}">{html.escape(rp[0].display_name)}</text>')
                    if len(rp) > 1 and rp[1].display_name:
                        parts.append(f'<text x="{comp.x + comp.width - 4.0:.1f}" y="{comp.y + comp.height + 10.0:.1f}" text-anchor="end" font-family="ui-monospace, monospace" font-size="8" font-weight="600" fill="{text_val_color}">{html.escape(rp[1].display_name)}</text>')
                elif len(rp) == 1 and len(lp) >= 2:
                    # Common on Right, Throws on Left
                    parts.append(f'<line x1="{comp.x + comp.width - 18.0:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<circle cx="{comp.x + comp.width - 18.0:.1f}" cy="{cy:.1f}" r="2.4" fill="{symbol_stroke}" />')
                    parts.append(f'<circle cx="{comp.x + 18.0:.1f}" cy="{comp.y:.1f}" r="2.2" fill="none" stroke="{symbol_stroke}" stroke-width="1.6" />')
                    parts.append(f'<line x1="{comp.x:.1f}" y1="{comp.y:.1f}" x2="{comp.x + 15.8:.1f}" y2="{comp.y:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<circle cx="{comp.x + 18.0:.1f}" cy="{comp.y + comp.height:.1f}" r="2.2" fill="none" stroke="{symbol_stroke}" stroke-width="1.6" />')
                    parts.append(f'<line x1="{comp.x:.1f}" y1="{comp.y + comp.height:.1f}" x2="{comp.x + 15.8:.1f}" y2="{comp.y + comp.height:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<line x1="{comp.x + comp.width - 18.0:.1f}" y1="{cy:.1f}" x2="{comp.x + 20.0:.1f}" y2="{comp.y + 2.5:.1f}" stroke="{symbol_stroke}" stroke-width="2.2" stroke-linecap="round" />')
                    if rp[0].display_name:
                        parts.append(f'<text x="{comp.x + comp.width - 4.0:.1f}" y="{cy - 5.0:.1f}" text-anchor="end" font-family="ui-monospace, monospace" font-size="8" font-weight="600" fill="{text_val_color}">{html.escape(rp[0].display_name)}</text>')
                    if lp[0].display_name:
                        parts.append(f'<text x="{comp.x + 4.0:.1f}" y="{comp.y - 4.0:.1f}" text-anchor="start" font-family="ui-monospace, monospace" font-size="8" font-weight="600" fill="{text_val_color}">{html.escape(lp[0].display_name)}</text>')
                    if len(lp) > 1 and lp[1].display_name:
                        parts.append(f'<text x="{comp.x + 4.0:.1f}" y="{comp.y + comp.height + 10.0:.1f}" text-anchor="start" font-family="ui-monospace, monospace" font-size="8" font-weight="600" fill="{text_val_color}">{html.escape(lp[1].display_name)}</text>')
                else:
                    parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{comp.x + 18.0:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<circle cx="{comp.x + 18.0:.1f}" cy="{cy:.1f}" r="2.4" fill="{symbol_stroke}" />')
                    parts.append(f'<circle cx="{comp.x + 42.0:.1f}" cy="{cy:.1f}" r="2.2" fill="none" stroke="{symbol_stroke}" stroke-width="1.6" />')
                    parts.append(f'<line x1="{comp.x + 44.2:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                    parts.append(f'<line x1="{comp.x + 18.0:.1f}" y1="{cy:.1f}" x2="{comp.x + 39.0:.1f}" y2="{cy - 10.0:.1f}" stroke="{symbol_stroke}" stroke-width="2.2" stroke-linecap="round" />')
            else:
                parts.append(f'<circle cx="{comp.x + 20.0:.1f}" cy="{cy:.1f}" r="2.4" fill="{symbol_stroke}" />')
                parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{comp.x + 20.0:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<circle cx="{comp.x + 42.0:.1f}" cy="{cy:.1f}" r="2.2" fill="none" stroke="{symbol_stroke}" stroke-width="1.6" />')
                parts.append(f'<line x1="{comp.x + 44.2:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
                parts.append(f'<line x1="{comp.x + 20.0:.1f}" y1="{cy:.1f}" x2="{comp.x + 39.0:.1f}" y2="{cy - 11.0:.1f}" stroke="{symbol_stroke}" stroke-width="2.2" stroke-linecap="round" />')

        elif k == "fuse":
            parts.append(f'<rect x="{cx - 20.0:.1f}" y="{cy - 8.0:.1f}" width="40.0" height="16.0" rx="1" ry="1" fill="{comp_bg}" stroke="{box_stroke}" stroke-width="1.8" />')
            parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
            parts.append(f'<text x="{cx:.1f}" y="{comp.y + 7.0:.1f}" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
            if escaped_val:
                parts.append(f'<text x="{cx:.1f}" y="{comp.y + 36.0:.1f}" text-anchor="middle" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')

        elif k in ("diode", "led"):
            parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{cx - 10.0:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
            parts.append(f'<line x1="{cx + 8.0:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
            parts.append(f'<polygon points="{cx - 10.0:.1f},{cy - 9.0:.1f} {cx - 10.0:.1f},{cy + 9.0:.1f} {cx + 8.0:.1f},{cy:.1f}" fill="{comp_bg}" stroke="{symbol_stroke}" stroke-width="1.8" />')
            parts.append(f'<line x1="{cx + 8.0:.1f}" y1="{cy - 9.0:.1f}" x2="{cx + 8.0:.1f}" y2="{cy + 9.0:.1f}" stroke="{symbol_stroke}" stroke-width="2.0" stroke-linecap="round" />')
            if k == "led":
                parts.append(f'<line x1="{cx - 2.0:.1f}" y1="{cy - 10.0:.1f}" x2="{cx + 4.0:.1f}" y2="{cy - 16.0:.1f}" stroke="{symbol_stroke}" stroke-width="1.4" />')
                parts.append(f'<polygon points="{cx + 4.0:.1f},{cy - 16.0:.1f} {cx + 1.0:.1f},{cy - 14.5:.1f} {cx + 2.5:.1f},{cy - 13.0:.1f}" fill="{symbol_stroke}" />')
                parts.append(f'<line x1="{cx + 4.0:.1f}" y1="{cy - 10.0:.1f}" x2="{cx + 10.0:.1f}" y2="{cy - 16.0:.1f}" stroke="{symbol_stroke}" stroke-width="1.4" />')
                parts.append(f'<polygon points="{cx + 10.0:.1f},{cy - 16.0:.1f} {cx + 7.0:.1f},{cy - 14.5:.1f} {cx + 8.5:.1f},{cy - 13.0:.1f}" fill="{symbol_stroke}" />')
            parts.append(f'<text x="{cx:.1f}" y="{comp.y + 6.0:.1f}" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
            if escaped_val:
                parts.append(f'<text x="{cx:.1f}" y="{comp.y + 36.0:.1f}" text-anchor="middle" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')

        elif k == "inductor":
            parts.append(f'<line x1="{comp.x:.1f}" y1="{cy:.1f}" x2="{cx - 18.0:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
            parts.append(f'<line x1="{cx + 18.0:.1f}" y1="{cy:.1f}" x2="{comp.x + comp.width:.1f}" y2="{cy:.1f}" stroke="{symbol_stroke}" stroke-width="1.8" />')
            parts.append(f'<path d="M {cx - 18.0:.1f},{cy:.1f} a 6,6 0 0,1 12,0 a 6,6 0 0,1 12,0 a 6,6 0 0,1 12,0" fill="none" stroke="{symbol_stroke}" stroke-width="1.8" />')
            parts.append(f'<text x="{cx:.1f}" y="{comp.y + 6.0:.1f}" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="700" fill="{text_title}">{escaped_id}</text>')
            if escaped_val:
                parts.append(f'<text x="{cx:.1f}" y="{comp.y + 36.0:.1f}" text-anchor="middle" font-family="ui-monospace, monospace" font-size="9" font-weight="600" fill="{text_val_color}">{escaped_val}</text>')

        # Pin terminals circles
        for pin in comp.pins.values():
            parts.append(f'<circle cx="{pin.position.x:.1f}" cy="{pin.position.y:.1f}" r="2.2" fill="{terminal_circle}" />')

        parts.append('</g>')
        return "\n".join(parts)

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

        # 1. Ground & Return
        if any(k in name_u for k in ["GND", "VSS", "NEG", "RETURN"]):
            return "#64748b" if self.dark_theme else "#475569"

        # 2. Raw / Battery / High-Voltage Power
        if any(k in name_u for k in ["BAT", "RAW", "HV", "36V", "48V"]):
            return "#f97316" if self.dark_theme else "#ea580c"

        # 3. Regulated DC Logic Power
        if any(k in name_u for k in ["VCC", "VDD", "VIN", "VOUT", "PWR", "3V3", "5V", "12V", "24V"]):
            return "#ef4444" if self.dark_theme else "#dc2626"

        # 4. Analog / Sensor / Differential
        if any(k in name_u for k in ["ANALOG", "ADC", "SENSE", "DIFF"]):
            return "#ec4899" if self.dark_theme else "#db2777"

        # 5. Fast signals & Pulses
        if any(k in name_u for k in ["INT", "IRQ", "PWM", "PULSE"]):
            return "#06b6d4" if self.dark_theme else "#0891b2"

        # 6. Communication Protocols (I2C, SPI, UART)
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

        # 7. Control & Enables
        if any(k in name_u for k in ["EN", "ENABLE", "RST", "RESET", "CTRL"]):
            return "#a78bfa" if self.dark_theme else "#7c3aed"

        return "#38bdf8" if self.dark_theme else "#0284c7"


def render_schematic_to_svg(schematic: Schematic, dark_theme: bool = True) -> str:
    renderer = SvgRenderer(schematic, dark_theme=dark_theme)
    return renderer.render()
