"""SVG renderer for placed and routed schematics.

Layers (bottom to top): background grid, wires, junction dots, components,
net labels.  Wire colours encode the net's semantic class (ground grey,
supplies red, other nets a stable hue each) and are resolved per theme from
``schematic_txt.colors``.

Standard parts (resistor, capacitor, ...) are drawn once in local
coordinates and rotated for vertical placement; their name and value labels
always stay upright.

Part images are embedded as ``data:`` URIs when a ``base_dir`` is given, so
the SVG is self-contained (GitHub, for one, does not load images referenced
from an SVG).
"""

from __future__ import annotations

import base64
import html
import math
import mimetypes
from pathlib import Path

from schematic_txt.colors import net_color
from schematic_txt.errors import ImageError
from schematic_txt.layout.shapes import is_vertical_symbol, mosfet_roles
from schematic_txt.models import Component, Pin, Schematic, Side

THEMES = ("light", "dark")
FONT_SANS = "system-ui, -apple-system, sans-serif"
FONT_MONO = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"


class _Theme:
    def __init__(self, dark: bool):
        if dark:
            self.bg = "#0f172a"
            self.grid = "#1e293b"
            self.comp_bg = "#1e293b"
            self.comp_border = "#334155"
            self.header_bg = "#0f172a"
            self.text_title = "#f1f5f9"
            self.text_subtitle = "#94a3b8"
            self.text_pin = "#cbd5e1"
            self.lead = "#64748b"
            self.terminal = "#94a3b8"
            self.junction_stroke = "#0f172a"
            self.symbol = "#cbd5e1"
            self.box_stroke = "#94a3b8"
            self.value = "#38bdf8"
            self.label_halo = "#0f172a"
        else:
            self.bg = "#f8fafc"
            self.grid = "#e2e8f0"
            self.comp_bg = "#ffffff"
            self.comp_border = "#cbd5e1"
            self.header_bg = "#f1f5f9"
            self.text_title = "#0f172a"
            self.text_subtitle = "#64748b"
            self.text_pin = "#334155"
            self.lead = "#94a3b8"
            self.terminal = "#64748b"
            self.junction_stroke = "#ffffff"
            self.symbol = "#334155"
            self.box_stroke = "#475569"
            self.value = "#0284c7"
            self.label_halo = "#f8fafc"


class SvgRenderer:
    def __init__(self, schematic: Schematic, theme: str = "light", base_dir: Path | None = None):
        if theme not in THEMES:
            raise ValueError(f"unknown theme {theme!r}; expected one of {', '.join(THEMES)}")
        self.schematic = schematic
        self.dark_theme = theme == "dark"
        self.base_dir = base_dir
        self.t = _Theme(self.dark_theme)
        self._colors: dict[str, str] = {}
        for net in schematic.nets:
            self._colors[net.name] = net_color(net.name, self.dark_theme)

    def color_of(self, net_name: str) -> str:
        return self._colors.get(net_name) or net_color(net_name, self.dark_theme)

    # ------------------------------------------------------------------ frame
    def _bounds(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for c in self.schematic.components.values():
            xs += [c.x - 40.0, c.x + c.width + 40.0]
            ys += [c.y - 30.0, c.y + c.height + 30.0]
            for p in c.pins.values():
                xs.append(p.position.x)
                ys.append(p.position.y)
        for w in self.schematic.wires:
            xs += [w.start.x, w.end.x]
            ys += [w.start.y, w.end.y]
        for lab in self.schematic.labels:
            xs += [lab.x, lab.x + lab.width]
            ys += [lab.y - 12.0, lab.y]
        if not xs:
            return 0.0, 0.0, 600.0, 450.0
        pad = 40.0
        min_x = math.floor((min(xs) - pad) / 20.0) * 20.0
        min_y = math.floor((min(ys) - pad) / 20.0) * 20.0
        max_x = math.ceil((max(xs) + pad) / 20.0) * 20.0
        max_y = math.ceil((max(ys) + pad) / 20.0) * 20.0
        w, h = max_x - min_x, max_y - min_y
        # Small drawings keep a minimum canvas so the viewer does not over-zoom.
        if w < 600.0:
            min_x -= math.floor((600.0 - w) / 40.0) * 20.0
            w = 600.0
        if h < 450.0:
            min_y -= math.floor((450.0 - h) / 40.0) * 20.0
            h = 450.0
        return min_x, min_y, w, h

    def render(self) -> str:
        t = self.t
        min_x, min_y, vb_w, vb_h = self._bounds()
        svg: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{vb_w:.0f}" height="{vb_h:.0f}" '
            f'viewBox="{min_x:.1f} {min_y:.1f} {vb_w:.1f} {vb_h:.1f}" '
            f'class="schematic-canvas" '
            f'shape-rendering="geometricPrecision" text-rendering="geometricPrecision">',
            "<defs>",
            '<pattern id="schematic-grid" width="20" height="20" patternUnits="userSpaceOnUse">',
            f'<circle cx="0" cy="0" r="1.3" fill="{t.grid}" />',
            "</pattern>",
            '<filter id="comp-shadow" x="-10%" y="-10%" width="120%" height="125%">',
            '  <feGaussianBlur in="SourceAlpha" stdDeviation="2.5" />',
            '  <feOffset dx="0" dy="2" result="offsetblur" />',
            '  <feComponentTransfer><feFuncA type="linear" slope="0.2" /></feComponentTransfer>',
            '  <feMerge><feMergeNode /><feMergeNode in="SourceGraphic" /></feMerge>',
            "</filter>",
            "</defs>",
        ]
        bg_pad = 50000.0
        for fill in (t.bg, "url(#schematic-grid)"):
            svg.append(
                f'<rect x="{min_x - bg_pad:.1f}" y="{min_y - bg_pad:.1f}" '
                f'width="{vb_w + 2 * bg_pad:.1f}" height="{vb_h + 2 * bg_pad:.1f}" fill="{fill}" />'
            )

        svg.append('<g id="schematic-wires" stroke-linecap="round" stroke-linejoin="round">')
        for wire in self.schematic.wires:
            svg.append(
                f'<line x1="{wire.start.x:.1f}" y1="{wire.start.y:.1f}" '
                f'x2="{wire.end.x:.1f}" y2="{wire.end.y:.1f}" '
                f'stroke="{self.color_of(wire.net_name)}" stroke-width="2.2" data-net="{html.escape(wire.net_name)}" />'
            )
        svg.append("</g>")

        svg.append('<g id="schematic-junctions">')
        for junc in self.schematic.junctions:
            svg.append(
                f'<circle cx="{junc.point.x:.1f}" cy="{junc.point.y:.1f}" r="{junc.radius:.1f}" '
                f'fill="{self.color_of(junc.net_name)}" stroke="{t.junction_stroke}" stroke-width="1.5" />'
            )
        svg.append("</g>")

        svg.append('<g id="schematic-components">')
        for comp in self.schematic.components.values():
            if comp.image:
                svg.append(self._render_custom_component(comp))
            elif comp.is_standard:
                svg.append(self._render_standard_component(comp))
            else:
                svg.append(self._render_component(comp))
        svg.append("</g>")

        if self.schematic.labels:
            svg.append('<g id="schematic-labels">')
            for lab in self.schematic.labels:
                svg.append(
                    f'<text x="{lab.x:.1f}" y="{lab.y:.1f}" font-family="{FONT_MONO}" font-size="9" '
                    f'font-weight="600" fill="{self.color_of(lab.net_name)}" stroke="{t.label_halo}" '
                    f'stroke-width="3" paint-order="stroke" class="net-label">{html.escape(lab.text)}</text>'
                )
            svg.append("</g>")

        svg.append("</svg>")
        return "\n".join(svg)

    # -------------------------------------------------------- standard parts
    def _symbol_body(self, kind: str, comp: Component, length: float) -> list[str]:
        """Horizontal symbol in local coordinates: centre (0, 0), leads from
        -length/2 to +length/2 along x."""
        t = self.t
        s, bg, bs = t.symbol, t.comp_bg, t.box_stroke
        half = length / 2.0
        out: list[str] = []

        def lead(x1: float, x2: float) -> None:
            out.append(f'<line x1="{x1:.1f}" y1="0" x2="{x2:.1f}" y2="0" stroke="{s}" stroke-width="1.8" />')

        if kind in ("resistor", "shunt"):
            lead(-half, -20.0)
            lead(20.0, half)
            out.append(
                f'<rect x="-20" y="-8" width="40" height="16" rx="2" ry="2" fill="{bg}" stroke="{bs}" stroke-width="1.8" />'
            )
        elif kind == "capacitor":
            lead(-half, -4.0)
            lead(4.0, half)
            for x in (-4.0, 4.0):
                out.append(
                    f'<line x1="{x:.1f}" y1="-12" x2="{x:.1f}" y2="12" stroke="{s}" stroke-width="2.4" stroke-linecap="round" />'
                )
        elif kind == "fuse":
            lead(-half, half)
            out.append(
                f'<rect x="-20" y="-8" width="40" height="16" rx="1" ry="1" fill="{bg}" fill-opacity="0.4" stroke="{bs}" stroke-width="1.8" />'
            )
        elif kind in ("diode", "led"):
            lead(-half, -10.0)
            lead(8.0, half)
            out.append(f'<polygon points="-10,-9 -10,9 8,0" fill="{bg}" stroke="{s}" stroke-width="1.8" />')
            out.append(
                f'<line x1="8" y1="-9" x2="8" y2="9" stroke="{s}" stroke-width="2.0" stroke-linecap="round" />'
            )
            if kind == "led":
                for x0 in (-2.0, 4.0):
                    out.append(
                        f'<line x1="{x0:.1f}" y1="-10" x2="{x0 + 6:.1f}" y2="-16" stroke="{s}" stroke-width="1.4" />'
                    )
                    out.append(
                        f'<polygon points="{x0 + 6:.1f},-16 {x0 + 3:.1f},-14.5 {x0 + 4.5:.1f},-13" fill="{s}" />'
                    )
        elif kind == "lamp":
            # IEC 60617: circle with a cross
            lead(-half, -10.0)
            lead(10.0, half)
            out.append(f'<circle cx="0" cy="0" r="10" fill="{bg}" stroke="{s}" stroke-width="1.8" />')
            for y in (-7.1, 7.1):
                out.append(
                    f'<line x1="-7.1" y1="{y:.1f}" x2="7.1" y2="{-y:.1f}" stroke="{s}" stroke-width="1.6" />'
                )
        elif kind == "inductor":
            lead(-half, -18.0)
            lead(18.0, half)
            out.append(
                f'<path d="M -18,0 a 6,6 0 0,1 12,0 a 6,6 0 0,1 12,0 a 6,6 0 0,1 12,0" fill="none" stroke="{s}" stroke-width="1.8" />'
            )
        elif kind == "switch":
            lead(-half, -10.0)
            lead(12.0, half)
            out.append(f'<circle cx="-10" cy="0" r="2.4" fill="{s}" />')
            out.append(f'<circle cx="12" cy="0" r="2.2" fill="none" stroke="{s}" stroke-width="1.6" />')
            out.append(
                f'<line x1="-10" y1="0" x2="10" y2="-10" stroke="{s}" stroke-width="2.2" stroke-linecap="round" />'
            )
        else:
            lead(-half, half)
        return out

    def _render_standard_component(self, comp: Component) -> str:
        t = self.t
        k = comp.kind.lower() if comp.kind else "resistor"
        parts = [
            f'<g id="comp-{html.escape(comp.id)}" class="component-standard component-{k}" transform="translate(0, 0)">'
        ]
        escaped_id = html.escape(comp.id)
        escaped_val = html.escape(comp.value) if comp.value else ""
        cx = comp.x + comp.width / 2.0
        cy = comp.y + comp.height / 2.0
        # Orientation follows the pins (a vertical capacitor is 40x40).
        vertical = is_vertical_symbol(comp)

        if k == "switch" and len(comp.pins) == 3:
            parts.extend(self._render_spdt(comp))
        elif k == "shunt" and len(comp.pins) == 4:
            parts.extend(self._render_shunt(comp))
        elif k in ("nmos", "pmos") and len(comp.pins) == 3:
            parts.extend(self._render_mosfet(comp))
        else:
            if k in ("diode", "led") and len(comp.pins) == 2:
                # Direct vector-driven rotation from Anode to Cathode:
                # Symbol body is drawn pointing from -half (Anode) to +half (Cathode) along +X.
                pins_list = list(comp.pins.values())
                k_pin = next((p for p in pins_list if p.name.upper() in ("K", "CATHODE", "-", "2")), None)
                a_pin = next((p for p in pins_list if p.name.upper() in ("A", "ANODE", "+", "1")), None)
                if k_pin is None and a_pin is not None:
                    k_pin = next(p for p in pins_list if p != a_pin)
                elif a_pin is None and k_pin is not None:
                    a_pin = next(p for p in pins_list if p != k_pin)
                elif a_pin is None and k_pin is None:
                    a_pin, k_pin = (
                        (pins_list[0], pins_list[1])
                        if pins_list[0].position.x <= pins_list[1].position.x
                        else (pins_list[1], pins_list[0])
                    )

                dx = k_pin.position.x - a_pin.position.x
                dy = k_pin.position.y - a_pin.position.y
                angle = round(math.degrees(math.atan2(dy, dx)))
                length = math.hypot(dx, dy) or (comp.height if vertical else comp.width)
                parts.append(f'<g transform="translate({cx:.1f}, {cy:.1f}) rotate({angle})">')
            else:
                length = comp.height if vertical else comp.width
                rotate = " rotate(90)" if vertical else ""
                parts.append(f'<g transform="translate({cx:.1f}, {cy:.1f}){rotate}">')
            parts.extend(self._symbol_body(k, comp, length))
            parts.append("</g>")
            if k == "lamp":
                parts.extend(self._plus_mark(comp, vertical))
            if vertical:
                tx = cx + 14.0
                parts.append(
                    f'<text x="{tx:.1f}" y="{cy - 3.0:.1f}" font-family="{FONT_SANS}" font-size="10" font-weight="700" fill="{t.text_title}">{escaped_id}</text>'
                )
                if escaped_val:
                    parts.append(
                        f'<text x="{tx:.1f}" y="{cy + 10.0:.1f}" font-family="{FONT_MONO}" font-size="9" font-weight="600" fill="{t.value}">{escaped_val}</text>'
                    )
            else:
                parts.append(
                    f'<text x="{cx:.1f}" y="{comp.y + 5.0:.1f}" text-anchor="middle" font-family="{FONT_SANS}" font-size="10" font-weight="700" fill="{t.text_title}">{escaped_id}</text>'
                )
                if escaped_val:
                    parts.append(
                        f'<text x="{cx:.1f}" y="{cy + 17.0:.1f}" text-anchor="middle" font-family="{FONT_MONO}" font-size="9" font-weight="600" fill="{t.value}">{escaped_val}</text>'
                    )

        for pin in comp.pins.values():
            parts.append(
                f'<circle cx="{pin.position.x:.1f}" cy="{pin.position.y:.1f}" r="2.2" fill="{t.terminal}" />'
            )
        parts.append("</g>")
        return "\n".join(parts)

    def _first_pin(self, comp: Component) -> Pin | None:
        pins = sorted(comp.pins.values(), key=lambda p: (p.position.x, p.position.y))
        return pins[0] if pins else None

    def _render_spdt(self, comp: Component) -> list[str]:
        t = self.t
        s = t.symbol
        out: list[str] = []
        escaped_id = html.escape(comp.id)
        cx = comp.x + comp.width / 2.0
        comp.y + comp.height / 2.0
        out.append(
            f'<text x="{cx:.1f}" y="{comp.y - 6.0:.1f}" text-anchor="middle" font-family="{FONT_SANS}" font-size="10" font-weight="700" fill="{t.text_title}">{escaped_id}</text>'
        )
        if comp.value:
            out.append(
                f'<text x="{cx:.1f}" y="{comp.y + comp.height + 15.0:.1f}" text-anchor="middle" font-family="{FONT_MONO}" font-size="9" font-weight="600" fill="{t.value}">{html.escape(comp.value)}</text>'
            )
        left = [p for p in comp.pins.values() if p.side == Side.LEFT]
        right = [p for p in comp.pins.values() if p.side == Side.RIGHT]
        common_left = len(left) == 1
        commons = left if common_left else right
        throws = sorted(right if common_left else left, key=lambda p: p.position.y)
        if not commons or len(throws) < 2:
            return out
        com = commons[0]
        pivot_x = com.position.x + (18.0 if common_left else -18.0)
        out.append(
            f'<line x1="{com.position.x:.1f}" y1="{com.position.y:.1f}" x2="{pivot_x:.1f}" y2="{com.position.y:.1f}" stroke="{s}" stroke-width="1.8" />'
        )
        out.append(f'<circle cx="{pivot_x:.1f}" cy="{com.position.y:.1f}" r="2.4" fill="{s}" />')
        for th in throws:
            contact_x = th.position.x + (-18.0 if common_left else 18.0)
            out.append(
                f'<circle cx="{contact_x:.1f}" cy="{th.position.y:.1f}" r="2.2" fill="none" stroke="{s}" stroke-width="1.6" />'
            )
            out.append(
                f'<line x1="{contact_x + (2.2 if common_left else -2.2):.1f}" y1="{th.position.y:.1f}" x2="{th.position.x:.1f}" y2="{th.position.y:.1f}" stroke="{s}" stroke-width="1.8" />'
            )
        first = throws[0]
        tip_x = first.position.x + (-20.0 if common_left else 20.0)
        out.append(
            f'<line x1="{pivot_x:.1f}" y1="{com.position.y:.1f}" x2="{tip_x:.1f}" y2="{first.position.y + 2.5:.1f}" stroke="{s}" stroke-width="2.2" stroke-linecap="round" />'
        )
        for p in comp.pins.values():
            if p.display_name:
                anchor = "start" if p.side == Side.LEFT else "end"
                tx = p.position.x + (4.0 if p.side == Side.LEFT else -4.0)
                ty = (
                    p.position.y - 5.0
                    if p is com
                    else (p.position.y - 4.0 if p is first else p.position.y + 10.0)
                )
                out.append(
                    f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" font-family="{FONT_MONO}" font-size="8" font-weight="600" fill="{t.value}">{html.escape(p.display_name)}</text>'
                )
        return out

    def _plus_mark(self, comp: Component, vertical: bool) -> list[str]:
        """A '+' at the plus pin of a polarised two-pin part (a 5 V LED lamp)."""
        plus = next(
            (p for p in comp.pins.values() if p.name.upper() in ("+", "V+", "VCC", "VIN", "POS")), None
        )
        if plus is None:
            return []
        cx = comp.x + comp.width / 2.0
        cy = comp.y + comp.height / 2.0
        if vertical:
            x, y, anchor = cx - 5.0, plus.position.y + math.copysign(14.0, cy - plus.position.y) + 3.5, "end"
        else:
            x, y, anchor = plus.position.x + math.copysign(9.0, cx - plus.position.x), cy - 4.0, "middle"
        return [
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="{FONT_MONO}" font-size="10" '
            f'font-weight="700" fill="{self.t.symbol}">+</text>'
        ]

    def _render_mosfet(self, comp: Component) -> list[str]:
        """Enhancement-mode MOSFET: gate plate, broken channel, source tied to
        the body; the body arrow points in for N-channel, out for P-channel."""
        t = self.t
        s = t.symbol
        out: list[str] = []
        gate, drain, source = mosfet_roles(comp)
        x = drain.position.x  # drain / source leads
        ym = gate.position.y
        sgn = 1.0 if x > gate.position.x else -1.0
        plate, ch = x - sgn * 22.0, x - sgn * 16.0
        yd = ym + math.copysign(14.0, drain.position.y - ym)
        ys = ym + math.copysign(14.0, source.position.y - ym)

        def line(x1: float, y1: float, x2: float, y2: float, width: float = 1.8) -> None:
            out.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{s}" stroke-width="{width}" />'
            )

        line(gate.position.x, ym, plate, ym)
        line(plate, ym - 16.0, plate, ym + 16.0, 2.2)
        for y in (yd, ym, ys):
            line(ch, y - 5.0, ch, y + 5.0, 2.4)
        line(ch, yd, x, yd)
        line(x, yd, x, drain.position.y)
        line(ch, ys, x, ys)
        line(x, ym, x, source.position.y)
        line(ch, ym, x, ym)
        tip, back = (ch, ch + sgn * 8.0) if comp.kind == "nmos" else (x - sgn * 2.0, x - sgn * 10.0)
        out.append(
            f'<polygon points="{tip:.1f},{ym:.1f} {back:.1f},{ym - 4.0:.1f} {back:.1f},{ym + 4.0:.1f}" fill="{s}" />'
        )
        tx, anchor = (x + sgn * 10.0, "start" if sgn > 0 else "end")
        out.append(
            f'<text x="{tx:.1f}" y="{ym - 3.0:.1f}" text-anchor="{anchor}" font-family="{FONT_SANS}" font-size="10" font-weight="700" fill="{t.text_title}">{html.escape(comp.id)}</text>'
        )
        if comp.value:
            out.append(
                f'<text x="{tx:.1f}" y="{ym + 10.0:.1f}" text-anchor="{anchor}" font-family="{FONT_MONO}" font-size="9" font-weight="600" fill="{t.value}">{html.escape(comp.value)}</text>'
            )
        return out

    def _render_shunt(self, comp: Component) -> list[str]:
        """Resistor on the current path, sense taps branching off its ends."""
        t = self.t
        s, bg, bs = t.symbol, t.comp_bg, t.box_stroke
        out: list[str] = []
        current = sorted(
            (p for p in comp.pins.values() if p.side in (Side.LEFT, Side.RIGHT)), key=lambda p: p.position.x
        )
        sense = [p for p in comp.pins.values() if p.side in (Side.TOP, Side.BOTTOM)]
        if len(current) != 2 or len(sense) != 2:
            return out
        x0, x1 = current[0].position.x, current[1].position.x
        y = current[0].position.y
        cx = (x0 + x1) / 2.0
        out.append(
            f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{cx - 20.0:.1f}" y2="{y:.1f}" stroke="{s}" stroke-width="1.8" />'
        )
        out.append(
            f'<line x1="{cx + 20.0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" stroke="{s}" stroke-width="1.8" />'
        )
        out.append(
            f'<rect x="{cx - 20.0:.1f}" y="{y - 8.0:.1f}" width="40" height="16" rx="2" ry="2" fill="{bg}" stroke="{bs}" stroke-width="1.8" />'
        )
        for p in sense:
            out.append(
                f'<line x1="{p.position.x:.1f}" y1="{y:.1f}" x2="{p.position.x:.1f}" y2="{p.position.y:.1f}" stroke="{s}" stroke-width="1.8" />'
            )
            out.append(f'<circle cx="{p.position.x:.1f}" cy="{y:.1f}" r="2.4" fill="{s}" />')
        up = sense[0].position.y < y
        name_y, val_y = (y + 21.0, y + 32.0) if up else (y - 27.0, y - 14.0)
        out.append(
            f'<text x="{cx:.1f}" y="{name_y:.1f}" text-anchor="middle" font-family="{FONT_SANS}" font-size="10" font-weight="700" fill="{t.text_title}">{html.escape(comp.id)}</text>'
        )
        if comp.value:
            out.append(
                f'<text x="{cx:.1f}" y="{val_y:.1f}" text-anchor="middle" font-family="{FONT_MONO}" font-size="9" font-weight="600" fill="{t.value}">{html.escape(comp.value)}</text>'
            )
        return out

    # ------------------------------------------------------------- IC boxes
    def _render_component(self, comp: Component) -> str:
        t = self.t
        parts = [f'<g id="comp-{html.escape(comp.id)}" class="component-box" transform="translate(0, 0)">']
        parts.append(
            f'<rect x="{comp.x:.1f}" y="{comp.y:.1f}" width="{comp.width:.1f}" height="{comp.height:.1f}" '
            f'rx="4" ry="4" fill="{t.comp_bg}" stroke="{t.comp_border}" stroke-width="1.6" filter="url(#comp-shadow)" />'
        )
        header_h = 40.0
        parts.append(
            f'<path d="M {comp.x:.1f},{comp.y + 4.0:.1f} a 4,4 0 0,1 4,-4 l {comp.width - 8.0:.1f},0 a 4,4 0 0,1 4,4 '
            f'l 0,{header_h - 4.0:.1f} l {-comp.width:.1f},0 z" fill="{t.header_bg}" />'
        )
        parts.append(
            f'<line x1="{comp.x:.1f}" y1="{comp.y + header_h:.1f}" x2="{comp.x + comp.width:.1f}" '
            f'y2="{comp.y + header_h:.1f}" stroke="{t.comp_border}" stroke-width="1.2" />'
        )
        parts.append(
            f'<text x="{comp.x + 10.0:.1f}" y="{comp.y + 18.0:.1f}" font-family="{FONT_SANS}" font-size="11" '
            f'font-weight="700" fill="{t.text_title}">{html.escape(comp.id)}</text>'
        )
        if comp.type_name:
            parts.append(
                f'<text x="{comp.x + 10.0:.1f}" y="{comp.y + 31.0:.1f}" font-family="{FONT_SANS}" font-size="9" '
                f'font-weight="500" fill="{t.text_subtitle}">{html.escape(comp.type_name)}</text>'
            )
        for pin in comp.pins.values():
            parts.append(self._render_pin(pin))
        parts.append("</g>")
        return "\n".join(parts)

    # -------------------------------------------------------- custom image parts
    def _render_custom_component(self, comp: Component) -> str:
        t = self.t
        parts = [f'<g id="comp-{html.escape(comp.id)}" class="component-custom" transform="translate(0, 0)">']
        # xlink:href only: browsers read it, Inkscape 1.2 ignores a plain href,
        # and writing both would embed the image twice
        img_href = html.escape(self._image_href(comp.image.strip()))
        rotation = comp.rotation % 360
        if comp.image_rect is not None:
            ix, iy, iw, ih = comp.image_rect
        elif rotation in (90, 270):
            ix = iy = 0.0
            iw, ih = comp.height, comp.width
        else:
            ix = iy = 0.0
            iw, ih = comp.width, comp.height
        if rotation in (90, 180, 270):
            # The image definition stays in the unrotated local frame; rotate
            # it around the component centre onto the rotated footprint.
            cx = comp.x + comp.width / 2.0
            cy = comp.y + comp.height / 2.0
            if rotation == 180:
                origin_x, origin_y = comp.x, comp.y
            else:
                origin_x = comp.x + (comp.width - comp.height) / 2.0
                origin_y = comp.y + (comp.height - comp.width) / 2.0
            angle = {90: 90, 180: 180, 270: -90}[rotation]
            parts.append(f'<g transform="rotate({angle} {cx:.1f} {cy:.1f})">')
            parts.append(
                f'<image xlink:href="{img_href}" x="{origin_x + ix:.1f}" y="{origin_y + iy:.1f}" '
                f'width="{iw:.1f}" height="{ih:.1f}" preserveAspectRatio="none" filter="url(#comp-shadow)" />'
            )
            parts.append("</g>")
        else:
            parts.append(
                f'<image xlink:href="{img_href}" x="{comp.x + ix:.1f}" y="{comp.y + iy:.1f}" '
                f'width="{iw:.1f}" height="{ih:.1f}" preserveAspectRatio="none" filter="url(#comp-shadow)" />'
            )
        # Outline boundary
        parts.append(
            f'<rect x="{comp.x:.1f}" y="{comp.y:.1f}" width="{comp.width:.1f}" height="{comp.height:.1f}" '
            f'rx="4" ry="4" fill="none" stroke="{t.comp_border}" stroke-width="1.2" />'
        )
        # Component Title / ID Badge
        parts.append(
            f'<text x="{comp.x + 6.0:.1f}" y="{comp.y - 6.0:.1f}" font-family="{FONT_SANS}" font-size="10" '
            f'font-weight="700" fill="{t.text_title}" stroke="{t.label_halo}" stroke-width="3" paint-order="stroke">{html.escape(comp.id)}</text>'
        )
        # Pins
        for pin in comp.pins.values():
            parts.append(self._render_custom_pin(pin))
        parts.append("</g>")
        return "\n".join(parts)

    def _render_custom_pin(self, pin: Pin) -> str:
        t = self.t
        parts = []
        # 1. Lead line from pin pad on graphic to terminal
        parts.append(
            f'<line x1="{pin.lead_start.x:.1f}" y1="{pin.lead_start.y:.1f}" '
            f'x2="{pin.position.x:.1f}" y2="{pin.position.y:.1f}" stroke="{t.lead}" stroke-width="1.8" />'
        )
        # 2. Prominent circle pad on the component graphic (at lead_start)
        parts.append(
            f'<circle cx="{pin.lead_start.x:.1f}" cy="{pin.lead_start.y:.1f}" r="4.5" '
            f'fill="{t.comp_bg}" stroke="{t.value}" stroke-width="2.0" />'
        )
        parts.append(
            f'<circle cx="{pin.lead_start.x:.1f}" cy="{pin.lead_start.y:.1f}" r="2.0" fill="{t.value}" />'
        )
        # 3. Connection terminal dot (at pin.position)
        parts.append(
            f'<circle cx="{pin.position.x:.1f}" cy="{pin.position.y:.1f}" r="2.2" fill="{t.terminal}" />'
        )
        # 4. Pin tag: runs along the pin's own wire, just outside the terminal
        #    (the layout reserves exactly this zone, see layout.shapes).
        if pin.display_name:
            x, y = pin.position.x, pin.position.y
            if pin.side == Side.LEFT:
                tx, ty, anchor, rot = x - 4.0, y - 3.5, "end", ""
            elif pin.side == Side.RIGHT:
                tx, ty, anchor, rot = x + 4.0, y - 3.5, "start", ""
            elif pin.side == Side.TOP:
                tx, ty, anchor = x + 10.0, y - 3.0, "start"
                rot = f' transform="rotate(-90 {tx:.1f} {ty:.1f})"'
            else:
                tx, ty, anchor = x + 3.0, y + 3.0, "start"
                rot = f' transform="rotate(90 {tx:.1f} {ty:.1f})"'
            parts.append(
                f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}"{rot} font-family="{FONT_MONO}" '
                f'font-size="8" font-weight="600" fill="{t.text_pin}" stroke="{t.label_halo}" stroke-width="3" paint-order="stroke">'
                f"{html.escape(pin.display_name)}</text>"
            )
        return "\n".join(parts)

    def _render_pin(self, pin: Pin) -> str:
        t = self.t
        parts = [
            f'<line x1="{pin.lead_start.x:.1f}" y1="{pin.lead_start.y:.1f}" '
            f'x2="{pin.position.x:.1f}" y2="{pin.position.y:.1f}" stroke="{t.lead}" stroke-width="1.6" />',
            f'<circle cx="{pin.position.x:.1f}" cy="{pin.position.y:.1f}" r="2.2" fill="{t.terminal}" />',
        ]
        if pin.side == Side.LEFT:
            tx, ty, anchor = pin.lead_start.x + 8.0, pin.lead_start.y + 3.5, "start"
        elif pin.side == Side.RIGHT:
            tx, ty, anchor = pin.lead_start.x - 8.0, pin.lead_start.y + 3.5, "end"
        elif pin.side == Side.TOP:
            tx, ty, anchor = pin.lead_start.x, pin.lead_start.y + 14.0, "middle"
        else:
            tx, ty, anchor = pin.lead_start.x, pin.lead_start.y - 8.0, "middle"
        parts.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" font-family="{FONT_MONO}" '
            f'font-size="10" font-weight="500" fill="{t.text_pin}">{html.escape(pin.display_name)}</text>'
        )
        return "\n".join(parts)

    def _image_href(self, ref: str) -> str:
        """``ref`` as a data URI (read from ``base_dir``), or unchanged for
        URLs and when no ``base_dir`` is set."""
        if self.base_dir is None or ref.startswith(("http://", "https://", "data:")):
            return ref
        path = self.base_dir / ref
        mime = mimetypes.guess_type(path.name)[0]
        if mime is None or not mime.startswith("image/"):
            raise ImageError(f"Part image {ref!r} is not a known image type")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ImageError(f"Cannot read part image {ref!r} (looked in {self.base_dir})") from exc
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def render_svg(schematic: Schematic, theme: str = "light", base_dir: str | Path | None = None) -> str:
    """Draws a laid-out schematic as a standalone SVG document.

    ``theme`` is ``"light"`` or ``"dark"``.  With ``base_dir``, part images
    are read relative to it and embedded; without, their paths are kept as
    plain references.
    """
    return SvgRenderer(schematic, theme, Path(base_dir) if base_dir is not None else None).render()
