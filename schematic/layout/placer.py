"""2D Placement Engine for electrical schematics."""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Point, Rect, Schematic
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

GRID_X = 40.0
GRID_Y = 40.0
MIN_MARGIN = 80.0
CHANNEL_GAP_X = 150.0
CHANNEL_GAP_Y = 100.0


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

        # 1. Analyze connectivity graph
        adj, degrees = self._build_adjacency()

        # Identify hub (component with highest degree/pins)
        hub_comp = max(comps, key=lambda c: (degrees.get(c.id, 0), len(c.pins)))

        # 2. Assign initial 2D topological positions
        self._initial_topological_placement(comps, hub_comp, adj)

        # 3. Size components and allocate initial pin sides
        allocate_pins_and_dimensions(self.schematic)

        # 4. Spring-electrical relaxation with channel clearance
        self._force_directed_refinement(comps, adj, hub_comp)

        # 5. Overlap removal & channel enforcement
        self._resolve_overlaps(comps)

        # 6. Snap to grid
        for comp in comps:
            comp.x = round(comp.x / GRID_X) * GRID_X
            comp.y = round(comp.y / GRID_Y) * GRID_Y

        # 7. Finalize pin coordinates with exact component bounds
        allocate_pins_and_dimensions(self.schematic)

        # 8. Shift everything to positive canvas and calculate canvas dimensions
        self._finalize_bounds()

    def _build_adjacency(self) -> Tuple[Dict[str, Dict[str, int]], Dict[str, int]]:
        adj: Dict[str, Dict[str, int]] = {c.id: {} for c in self.schematic.components.values()}
        degrees: Dict[str, int] = {c.id: 0 for c in self.schematic.components.values()}

        for net in self.schematic.nets:
            cids = list({conn.component_id for conn in net.connections if conn.component_id in adj})
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    c1, c2 = cids[i], cids[j]
                    adj[c1][c2] = adj[c1].get(c2, 0) + 1
                    adj[c2][c1] = adj[c2].get(c1, 0) + 1
                    degrees[c1] += 1
                    degrees[c2] += 1

        return adj, degrees

    def _initial_topological_placement(
        self,
        comps: List[Component],
        hub: Component,
        adj: Dict[str, Dict[str, int]],
    ) -> None:
        """Arranges components in 2D space around the hub."""
        # Find bus-sharing components (e.g. sensors sharing I2C / SPI nets)
        bus_groups: Dict[str, List[str]] = {}
        for net in self.schematic.nets:
            cids = list({conn.component_id for conn in net.connections if conn.component_id != hub.id})
            if len(cids) >= 2:
                for cid in cids:
                    bus_groups.setdefault(cid, []).append(net.name)

        peripherals = [c for c in comps if c.id != hub.id]

        # Sort peripherals: group those with shared buses together
        peripherals.sort(key=lambda c: (len(bus_groups.get(c.id, [])), adj[hub.id].get(c.id, 0)), reverse=True)

        if len(peripherals) == 1:
            # Simple 2-component schematic: Hub on left, Peripheral on right
            hub.x = 100.0
            hub.y = 120.0
            peripherals[0].x = 450.0
            peripherals[0].y = 120.0
            return

        # Multi-component 2D arrangement:
        # Place Hub at (350, 300)
        hub.x = 350.0
        hub.y = 300.0

        # Divide peripherals into 2D regions:
        # 1. Right column: primary sensors & bus peripherals (e.g. I2C, SPI)
        # 2. Bottom row: connectors, actuators, secondary devices
        # 3. Top row: power supply, regulators, battery
        # 4. Left column: input devices, antennas
        right_stack: List[Component] = []
        bottom_stack: List[Component] = []
        top_stack: List[Component] = []
        left_stack: List[Component] = []

        for p in peripherals:
            p_id = p.id.upper()
            # Power regulators / sources
            if any(term in p_id for term in ["PWR", "POWER", "REG", "BAT", "VREG", "AMS1117", "LM"]):
                top_stack.append(p)
            # Input / USB / RF
            elif any(term in p_id for term in ["USB", "UART", "ANT", "BUTTON", "KEY", "IN"]):
                left_stack.append(p)
            # Buses and peripherals
            elif len(right_stack) <= 3:
                right_stack.append(p)
            elif len(bottom_stack) <= 2:
                bottom_stack.append(p)
            else:
                right_stack.append(p)

        # Position right stack (column on right of MCU)
        start_y = 100.0
        for i, comp in enumerate(right_stack):
            comp.x = 750.0
            comp.y = start_y + i * 180.0

        # Position top stack (above MCU)
        for i, comp in enumerate(top_stack):
            comp.x = 150.0 + i * 240.0
            comp.y = 80.0

        # Position left stack (to the left of MCU)
        for i, comp in enumerate(left_stack):
            comp.x = 80.0
            comp.y = 280.0 + i * 180.0

        # Position bottom stack (below MCU)
        for i, comp in enumerate(bottom_stack):
            comp.x = 350.0 + i * 240.0
            comp.y = 600.0

    def _force_directed_refinement(
        self,
        comps: List[Component],
        adj: Dict[str, Dict[str, int]],
        hub: Component,
        iterations: int = 40,
    ) -> None:
        """Applies force-directed relaxation to optimize 2D spacing and minimize wire tension."""
        dt = 0.4
        for _ in range(iterations):
            forces: Dict[str, Point] = {c.id: Point(0.0, 0.0) for c in comps}

            # 1. Repulsion between all pairs
            for i in range(len(comps)):
                c1 = comps[i]
                for j in range(i + 1, len(comps)):
                    c2 = comps[j]
                    dx = c2.center.x - c1.center.x
                    dy = c2.center.y - c1.center.y
                    dist = math.hypot(dx, dy)
                    if dist < 1.0:
                        dist = 1.0
                        dx = 1.0

                    # Desired minimum distance based on component sizes + channel gaps
                    min_dist = (c1.width + c2.width) / 2.0 + CHANNEL_GAP_X
                    if dist < min_dist:
                        rep = 50000.0 / (dist * dist + 100.0)
                        fx = (dx / dist) * rep
                        fy = (dy / dist) * rep
                        forces[c1.id] = Point(forces[c1.id].x - fx, forces[c1.id].y - fy)
                        forces[c2.id] = Point(forces[c2.id].x + fx, forces[c2.id].y + fy)

            # 2. Attraction along connected nets
            for c1_id, peers in adj.items():
                c1 = next(c for c in comps if c.id == c1_id)
                for c2_id, weight in peers.items():
                    c2 = next(c for c in comps if c.id == c2_id)
                    dx = c2.center.x - c1.center.x
                    dy = c2.center.y - c1.center.y
                    dist = math.hypot(dx, dy)
                    if dist > 0.0:
                        attr = 0.03 * weight * dist
                        fx = (dx / dist) * attr
                        fy = (dy / dist) * attr
                        forces[c1.id] = Point(forces[c1.id].x + fx, forces[c1.id].y + fy)

            # Apply forces (keep hub relatively anchored to stabilize layout)
            for comp in comps:
                if comp.id == hub.id:
                    # Hub moves slowly
                    comp.x += forces[comp.id].x * dt * 0.2
                    comp.y += forces[comp.id].y * dt * 0.2
                else:
                    comp.x += forces[comp.id].x * dt
                    comp.y += forces[comp.id].y * dt

    def _resolve_overlaps(self, comps: List[Component]) -> None:
        """Ensures non-overlapping components with required routing channel margins."""
        for _ in range(25):
            has_overlap = False
            for i in range(len(comps)):
                c1 = comps[i]
                for j in range(i + 1, len(comps)):
                    c2 = comps[j]

                    r1 = Rect(c1.x, c1.y, c1.width, c1.height).expanded(CHANNEL_GAP_X / 2.5)
                    r2 = Rect(c2.x, c2.y, c2.width, c2.height).expanded(CHANNEL_GAP_X / 2.5)

                    if r1.intersects(r2):
                        has_overlap = True
                        dx = c2.center.x - c1.center.x
                        dy = c2.center.y - c1.center.y
                        if abs(dx) < 1.0 and abs(dy) < 1.0:
                            dx = 10.0
                            dy = 10.0

                        # Push apart
                        overlap_x = (r1.width / 2.0 + r2.width / 2.0) - abs(dx)
                        overlap_y = (r1.height / 2.0 + r2.height / 2.0) - abs(dy)

                        if overlap_x < overlap_y:
                            shift = (overlap_x + 10.0) / 2.0
                            sgn = 1.0 if dx >= 0 else -1.0
                            c2.x += shift * sgn
                            c1.x -= shift * sgn
                        else:
                            shift = (overlap_y + 10.0) / 2.0
                            sgn = 1.0 if dy >= 0 else -1.0
                            c2.y += shift * sgn
                            c1.y -= shift * sgn

            if not has_overlap:
                break

    def _finalize_bounds(self) -> None:
        """Normalizes coordinates so that minimum (x, y) has a clean margin and sets total schematic dimensions."""
        comps = list(self.schematic.components.values())
        if not comps:
            return

        min_x = min(c.x - 30.0 for c in comps)
        min_y = min(c.y - 30.0 for c in comps)

        shift_x = MIN_MARGIN - min_x
        shift_y = MIN_MARGIN - min_y

        for comp in comps:
            comp.x += shift_x
            comp.y += shift_y
            # Recompute pin positions with shifted component
            for pin in comp.pins.values():
                pin.lead_start = Point(pin.lead_start.x + shift_x, pin.lead_start.y + shift_y)
                pin.position = Point(pin.position.x + shift_x, pin.position.y + shift_y)

        max_x = max(c.x + c.width + 50.0 for c in comps)
        max_y = max(c.y + c.height + 50.0 for c in comps)

        self.schematic.width = max(max_x + MIN_MARGIN, 800.0)
        self.schematic.height = max(max_y + MIN_MARGIN, 600.0)


def place_schematic(schematic: Schematic) -> None:
    placer = SchematicPlacer(schematic)
    placer.place()
