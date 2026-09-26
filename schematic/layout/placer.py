"""2D Grid & Corridor Placement Engine for electrical schematics.

Arranges components in clean topological columns and rows with guaranteed
inter-column and inter-row routing corridors so wires never need to cross components.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Point, Rect, Schematic
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

GRID_SIZE = 20.0
CORRIDOR_X = 180.0
ROW_GAP = 60.0
TOP_CORRIDOR_H = 100.0
BOTTOM_CORRIDOR_H = 100.0
MARGIN_LEFT = 80.0


class SchematicPlacer:
    def __init__(self, schematic: Schematic):
        self.schematic = schematic

    def place(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            self.schematic.width = 600.0
            self.schematic.height = 400.0
            return

        if len(comps) == 1:
            comps[0].x = 100.0
            comps[0].y = 100.0
            allocate_pins_and_dimensions(self.schematic)
            self._finalize_bounds()
            return

        # 1. First initial pass to get estimated widths/heights
        allocate_pins_and_dimensions(self.schematic)

        # 2. Identify Hub and classify components into columns
        col_0, col_1, col_2 = self._classify_columns(comps)

        # 3. Position components in clean 2D grid columns strictly on grid
        columns = [c for c in [col_0, col_1, col_2] if c]

        curr_x = round(MARGIN_LEFT / GRID_SIZE) * GRID_SIZE
        start_y = round((TOP_CORRIDOR_H + 40.0) / GRID_SIZE) * GRID_SIZE

        for col_idx, col_comps in enumerate(columns):
            max_w = max(c.width for c in col_comps)

            # Stack components vertically in this column
            curr_y = start_y
            for comp in col_comps:
                comp.x = float(round(curr_x / GRID_SIZE) * GRID_SIZE)
                comp.y = float(round(curr_y / GRID_SIZE) * GRID_SIZE)
                curr_y += comp.height + ROW_GAP

            # Next column starts after this column + corridor
            curr_x += max_w + CORRIDOR_X

        # 4. Finalize pin coordinates based on exact column positions
        allocate_pins_and_dimensions(self.schematic)

        # 5. Set canvas bounds snapped to grid
        self._finalize_bounds()

    def _classify_columns(
        self, comps: List[Component]
    ) -> Tuple[List[Component], List[Component], List[Component]]:
        """
        Classifies components into 3 standard schematic stages:
          Column 0: Power / Regulators / USB / Inputs
          Column 1: Main MCU / Controller Hub
          Column 2: Peripherals / Sensors / Displays / Actuators
        """
        if len(comps) == 2:
            return [comps[0]], [comps[1]], []

        # Find component with most pins/connections -> designated Hub
        degrees: Dict[str, int] = {c.id: len(c.pins) for c in comps}
        for net in self.schematic.nets:
            cids = list({conn.component_id for conn in net.connections if conn.component_id in degrees})
            for cid in cids:
                degrees[cid] += 1

        hub = max(comps, key=lambda c: (degrees.get(c.id, 0), len(c.pins)))

        col_0: List[Component] = []  # Left
        col_1: List[Component] = [hub]  # Center (Hub)
        col_2: List[Component] = []  # Right (Peripherals)

        for comp in comps:
            if comp.id == hub.id:
                continue

            cid_u = comp.id.upper()
            c_type_u = comp.type_name.upper()

            # Power sources, regulators, USB inputs
            is_power_src = any(term in cid_u or term in c_type_u for term in ["PWR", "POWER", "REG", "BAT", "AMS1117", "LM78", "VREG", "SUPPLY"])
            is_input = any(term in cid_u or term in c_type_u for term in ["USB", "ANT", "BUTTON", "JACK", "IN"])

            if is_power_src or is_input:
                col_0.append(comp)
            else:
                col_2.append(comp)

        # If col_0 is empty and col_2 is large, keep layout balanced
        if not col_0 and len(col_2) > 4:
            # Shift some peripherals to left column if they don't share bus
            col_0.append(col_2.pop())

        return col_0, col_1, col_2

    def _finalize_bounds(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            return

        max_x = max(c.x + c.width for c in comps)
        max_y = max(c.y + c.height for c in comps)

        self.schematic.width = max(max_x + CORRIDOR_X, 850.0)
        self.schematic.height = max(max_y + BOTTOM_CORRIDOR_H, 600.0)


def place_schematic(schematic: Schematic) -> None:
    placer = SchematicPlacer(schematic)
    placer.place()
