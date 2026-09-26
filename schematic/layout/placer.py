"""2D Force-Directed & Corridor Placement Engine for electrical schematics.

Arranges components in a natural, unconstrained 2D topology (hub-and-spoke, clusters, mesh)
with guaranteed non-overlapping clearance corridors, snapped strictly to the 20px grid.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Point, Rect, Schematic, GRID_SIZE, snap_to_grid
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

CORRIDOR_CLEARANCE = 120.0
MARGIN = 100.0


class SchematicPlacer:
    def __init__(self, schematic: Schematic):
        self.schematic = schematic

    def place(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            self.schematic.width = 600.0
            self.schematic.height = 400.0
            return

        # 1. Initial pin allocation to get base bounding boxes
        allocate_pins_and_dimensions(self.schematic, use_geometry=False)

        if len(comps) == 1:
            comps[0].x = MARGIN
            comps[0].y = MARGIN
            allocate_pins_and_dimensions(self.schematic, use_geometry=True)
            self._finalize_bounds()
            return

        # 2. Build connectivity graph
        adj: Dict[str, Dict[str, float]] = {c.id: {} for c in comps}
        for net in self.schematic.nets:
            cids = list({conn.component_id for conn in net.connections if conn.component_id in adj})
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    u, v = cids[i], cids[j]
                    adj[u][v] = adj[u].get(v, 0.0) + 1.0
                    adj[v][u] = adj[v].get(u, 0.0) + 1.0

        # Central hub (highest degree / pin count)
        hub = max(comps, key=lambda c: (sum(adj[c.id].values()), len(c.pins)))
        pos: Dict[str, List[float]] = {hub.id: [0.0, 0.0]}
        peers = [c for c in comps if c.id != hub.id]

        if len(peers) == 1:
            pos[peers[0].id] = [280.0, 0.0]
        else:
            for idx, c in enumerate(peers):
                angle = 2.0 * math.pi * idx / len(peers)
                pos[c.id] = [320.0 * math.cos(angle), 320.0 * math.sin(angle)]

        # 3. Force-directed relaxation
        target_dist = 280.0
        for _ in range(60):
            forces: Dict[str, List[float]] = {c.id: [0.0, 0.0] for c in comps}

            # Attractive spring forces
            for u in comps:
                for v_id, weight in adj[u.id].items():
                    dx = pos[v_id][0] - pos[u.id][0]
                    dy = pos[v_id][1] - pos[u.id][1]
                    dist = math.hypot(dx, dy) or 1.0
                    f = weight * (dist - target_dist) * 0.05
                    forces[u.id][0] += f * (dx / dist)
                    forces[u.id][1] += f * (dy / dist)

            # Repulsive forces between all components
            for i in range(len(comps)):
                for j in range(i + 1, len(comps)):
                    u, v = comps[i], comps[j]
                    dx = pos[v.id][0] - pos[u.id][0]
                    dy = pos[v.id][1] - pos[u.id][1]
                    dist = math.hypot(dx, dy) or 1.0
                    if dist < 450.0:
                        rep = 25000.0 / (dist * dist)
                        forces[u.id][0] -= rep * (dx / dist)
                        forces[u.id][1] -= rep * (dy / dist)
                        forces[v.id][0] += rep * (dx / dist)
                        forces[v.id][1] += rep * (dy / dist)

            for c in comps:
                pos[c.id][0] += forces[c.id][0]
                pos[c.id][1] += forces[c.id][1]

        # 4. VPSC separation passes to guarantee clear corridors between blocks
        for _ in range(60):
            moved = False
            for i in range(len(comps)):
                for j in range(i + 1, len(comps)):
                    u, v = comps[i], comps[j]
                    dx = pos[v.id][0] - pos[u.id][0]
                    dy = pos[v.id][1] - pos[u.id][1]
                    min_dx = (u.width + v.width) / 2.0 + CORRIDOR_CLEARANCE
                    min_dy = (u.height + v.height) / 2.0 + CORRIDOR_CLEARANCE
                    ox = min_dx - abs(dx)
                    oy = min_dy - abs(dy)
                    if ox > 0 and oy > 0:
                        moved = True
                        if ox < oy:
                            s = 1.0 if dx >= 0 else -1.0
                            pos[u.id][0] -= (ox / 2.0) * s
                            pos[v.id][0] += (ox / 2.0) * s
                        else:
                            s = 1.0 if dy >= 0 else -1.0
                            pos[u.id][1] -= (oy / 2.0) * s
                            pos[v.id][1] += (oy / 2.0) * s
            if not moved:
                break

        # 5. Normalize origin and snap to 20px grid
        min_px = min(pos[c.id][0] for c in comps)
        min_py = min(pos[c.id][1] for c in comps)
        for c in comps:
            c.x = snap_to_grid(pos[c.id][0] - min_px + MARGIN, GRID_SIZE)
            c.y = snap_to_grid(pos[c.id][1] - min_py + MARGIN, GRID_SIZE)

        # 6. Re-assign pins with true 2D geometry facing connected peers
        allocate_pins_and_dimensions(self.schematic, use_geometry=True)
        self._finalize_bounds()

    def _finalize_bounds(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            return
        max_x = max(c.x + c.width for c in comps)
        max_y = max(c.y + c.height for c in comps)
        self.schematic.width = max(max_x + MARGIN, 850.0)
        self.schematic.height = max(max_y + MARGIN, 600.0)


def place_schematic(schematic: Schematic) -> None:
    placer = SchematicPlacer(schematic)
    placer.place()

