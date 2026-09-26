"""2D Force-Directed & Corridor Placement Engine for electrical schematics.

Arranges components in a natural, unconstrained 2D topology (hub-and-spoke, clusters, mesh)
with guaranteed non-overlapping clearance corridors, snapped strictly to the 20px grid.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Point, Rect, Schematic, Side, GRID_SIZE, snap_to_grid
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

CORRIDOR_CLEARANCE = 180.0
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

        # 2. Build connectivity graph and directional side affinity
        adj: Dict[str, Dict[str, float]] = {c.id: {} for c in comps}
        dir_aff: Dict[Tuple[str, str], List[float]] = {}
        pwr_terms = ["GND", "VSS", "3V3", "5V", "VCC", "VIN", "VDD", "BATT-", "NEG"]

        for net in self.schematic.nets:
            nu = net.name.upper()
            is_pwr = len(net.connections) >= 4 or any(term in nu for term in pwr_terms)
            w = 0.05 if is_pwr else 8.0
            cids = list({conn.component_id for conn in net.connections if conn.component_id in adj})
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    u, v = cids[i], cids[j]
                    adj[u][v] = adj[u].get(v, 0.0) + w
                    adj[v][u] = adj[v].get(u, 0.0) + w

            if not is_pwr:
                for conn1 in net.connections:
                    c1 = self.schematic.components.get(conn1.component_id)
                    p1 = c1.pins.get(conn1.pin_id) if conn1.pin_id else c1.get_pin(conn1.pin_name) if c1 else None
                    for conn2 in net.connections:
                        if conn2.component_id == conn1.component_id:
                            continue
                        c2 = self.schematic.components.get(conn2.component_id)
                        p2 = c2.pins.get(conn2.pin_id) if conn2.pin_id else c2.get_pin(conn2.pin_name) if c2 else None
                        pair = (c1.id, c2.id)
                        vec = dir_aff.setdefault(pair, [0.0, 0.0])
                        if p1 and p1.side_specified:
                            if p1.side == Side.RIGHT: vec[0] += 1.0
                            elif p1.side == Side.LEFT: vec[0] -= 1.0
                            elif p1.side == Side.BOTTOM: vec[1] += 1.0
                            elif p1.side == Side.TOP: vec[1] -= 1.0
                        if p2 and p2.side_specified:
                            if p2.side == Side.LEFT: vec[0] += 1.0
                            elif p2.side == Side.RIGHT: vec[0] -= 1.0
                            elif p2.side == Side.TOP: vec[1] += 1.0
                            elif p2.side == Side.BOTTOM: vec[1] -= 1.0

        # Central hub (highest degree / pin count)
        hub = max(comps, key=lambda c: (sum(adj[c.id].values()), len(c.pins)))
        pos: Dict[str, List[float]] = {hub.id: [0.0, 0.0]}

        # Smart initial seeding: place direct peers in their pin-facing directions
        right_peers = []
        left_peers = []
        top_peers = []
        bottom_peers = []
        for c in comps:
            if c.id == hub.id:
                continue
            aff = dir_aff.get((hub.id, c.id), [0.0, 0.0])
            if aff[0] > 0.5:
                right_peers.append(c.id)
            elif aff[0] < -0.5:
                left_peers.append(c.id)
            elif aff[1] > 0.5:
                bottom_peers.append(c.id)
            elif aff[1] < -0.5:
                top_peers.append(c.id)
            else:
                left_peers.append(c.id)

        for idx, cid in enumerate(right_peers):
            pos[cid] = [360.0, (idx - len(right_peers) / 2.0 + 0.5) * 160.0]
        for idx, cid in enumerate(left_peers):
            pos[cid] = [-360.0, (idx - len(left_peers) / 2.0 + 0.5) * 160.0]
        for idx, cid in enumerate(top_peers):
            pos[cid] = [(idx - len(top_peers) / 2.0 + 0.5) * 160.0, -320.0]
        for idx, cid in enumerate(bottom_peers):
            pos[cid] = [(idx - len(bottom_peers) / 2.0 + 0.5) * 160.0, 320.0]

        # Seed chained components (not directly connected to hub) relative to their best peer
        unconnected_to_hub = [c for c in comps if c.id != hub.id and hub.id not in adj[c.id]]
        for c in unconnected_to_hub:
            best_peer = max(adj[c.id].keys(), key=lambda p: adj[c.id][p], default=None)
            if best_peer and best_peer in pos:
                aff = dir_aff.get((best_peer, c.id), [0.0, 0.0])
                ox = aff[0] * 200.0 if aff[0] != 0 else -200.0
                oy = aff[1] * 180.0 if aff[1] != 0 else 0.0
                pos[c.id] = [pos[best_peer][0] + ox, pos[best_peer][1] + oy]

        # 3. Force-directed relaxation with Fruchterman-Reingold cooling
        target_dist = 280.0
        temp = 25.0
        dt = 0.94

        for it in range(100):
            forces: Dict[str, List[float]] = {c.id: [0.0, 0.0] for c in comps}

            # Attractive spring forces
            for u in comps:
                for v_id, weight in adj[u.id].items():
                    dx = pos[v_id][0] - pos[u.id][0]
                    dy = pos[v_id][1] - pos[u.id][1]
                    dist = math.hypot(dx, dy) or 1.0
                    f = weight * (dist - target_dist) / target_dist
                    forces[u.id][0] += f * (dx / dist)
                    forces[u.id][1] += f * (dy / dist)

            # Directional side affinity constraint forces
            for (u_id, v_id), aff in dir_aff.items():
                ax, ay = aff
                dx = pos[v_id][0] - pos[u_id][0]
                dy = pos[v_id][1] - pos[u_id][1]
                if ax > 0 and dx < 220.0:
                    forces[v_id][0] += 6.0
                    forces[u_id][0] -= 6.0
                elif ax < 0 and dx > -220.0:
                    forces[v_id][0] -= 6.0
                    forces[u_id][0] += 6.0
                if ay > 0 and dy < 180.0:
                    forces[v_id][1] += 6.0
                    forces[u_id][1] -= 6.0
                elif ay < 0 and dy > -180.0:
                    forces[v_id][1] -= 6.0
                    forces[u_id][1] += 6.0
                if abs(ax) > 0 and ay == 0.0:
                    dy_align = pos[v_id][1] - pos[u_id][1]
                    forces[v_id][1] -= 0.05 * dy_align
                    forces[u_id][1] += 0.05 * dy_align

            # Repulsive forces between all components
            for i in range(len(comps)):
                for j in range(i + 1, len(comps)):
                    u, v = comps[i], comps[j]
                    dx = pos[v.id][0] - pos[u.id][0]
                    dy = pos[v.id][1] - pos[u.id][1]
                    dist = math.hypot(dx, dy) or 1.0
                    if dist < 420.0:
                        rep = 20000.0 / (dist * dist)
                        forces[u.id][0] -= rep * (dx / dist)
                        forces[u.id][1] -= rep * (dy / dist)
                        forces[v.id][0] += rep * (dx / dist)
                        forces[v.id][1] += rep * (dy / dist)

            for c in comps:
                mag = math.hypot(forces[c.id][0], forces[c.id][1]) or 1.0
                step = min(mag, temp)
                pos[c.id][0] += (forces[c.id][0] / mag) * step
                pos[c.id][1] += (forces[c.id][1] / mag) * step

            temp *= dt

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
        # 7. Resolve equivalent pins (e.g. multiple GND/3V3 pins) to nearest candidate
        self._resolve_equivalent_pins()
        self._finalize_bounds()

    def _resolve_equivalent_pins(self) -> None:
        """
        For components with multiple equivalent pins sharing the same base name (e.g. multiple GND pins),
        resolves each NetConnection to the best and nearest candidate pin based on 2D peer geometry.
        """
        for net in self.schematic.nets:
            for conn in net.connections:
                comp = self.schematic.components.get(conn.component_id)
                if not comp:
                    continue
                matching = comp.get_pins_by_name(conn.pin_name)
                if len(matching) <= 1:
                    if matching:
                        conn.pin_id = matching[0].id
                    continue

                # Multiple candidate pins found on comp (e.g. left GND vs right GND)
                peer_pts: List[Point] = []
                for pid in conn.peer_component_ids:
                    p_comp = self.schematic.components.get(pid)
                    if p_comp and p_comp.id != comp.id:
                        peer_pts.append(p_comp.center)

                if not peer_pts:
                    for c in net.connections:
                        if c.component_id != comp.id:
                            p_comp = self.schematic.components.get(c.component_id)
                            if p_comp:
                                peer_pts.append(p_comp.center)

                if peer_pts:
                    best_pin = min(
                        matching,
                        key=lambda p: min(p.position.manhattan_to(peer_pt) for peer_pt in peer_pts),
                    )
                    conn.pin_id = best_pin.id
                else:
                    conn.pin_id = matching[0].id

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

