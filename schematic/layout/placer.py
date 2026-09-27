"""2D Stage-Ranked Flow & Corridor Placement Engine for electrical schematics.

Arranges components in a natural, professional 2D topology with left-to-right signal/power flow,
functional clustering, and guaranteed non-overlapping clearance corridors, snapped strictly to the 20px grid.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Point, Rect, Schematic, Side, GRID_SIZE, snap_to_grid
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

MARGIN = 80.0
COLUMN_CORRIDOR = 160.0
ROW_CORRIDOR = 80.0


class SchematicPlacer:
    def __init__(self, schematic: Schematic):
        self.schematic = schematic

    def place(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            self.schematic.width = 600.0
            self.schematic.height = 400.0
            return

        # 1. Base sizing and pin orientation
        allocate_pins_and_dimensions(self.schematic, use_geometry=False)

        if len(comps) == 1:
            comps[0].x = MARGIN
            comps[0].y = MARGIN
            allocate_pins_and_dimensions(self.schematic, use_geometry=True)
            self._finalize_bounds()
            return

        # 2. Build connectivity graph with semantic weights
        pwr_names = {"GND", "VSS"}
        adj: Dict[str, Dict[str, float]] = {c.id: {} for c in comps}
        directed_adj: Dict[str, Set[str]] = {c.id: set() for c in comps}

        for net in self.schematic.nets:
            nu = net.name.upper()
            is_global_pwr = len(net.connections) >= 5 or nu in pwr_names
            cids = list({conn.component_id for conn in net.connections if conn.component_id in self.schematic.components})

            if is_global_pwr:
                w = 0.1
            elif len(net.connections) == 2:
                if any(k in nu for k in ["SENSE", "SHUNT", "DIFF"]):
                    w = 50.0
                elif any(k in nu for k in ["BATT", "5V", "PWR", "DCDC", "DISP", "CTRL", "LED", "RAW", "IN", "OUT"]):
                    w = 25.0
                else:
                    w = 15.0
            else:
                w = 8.0

            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    u, v = cids[i], cids[j]
                    adj[u][v] = adj[u].get(v, 0.0) + w
                    adj[v][u] = adj[v].get(u, 0.0) + w

            if not is_global_pwr and len(net.connections) >= 2:
                src_cid = net.connections[0].component_id
                for tgt in net.connections[1:]:
                    tgt_cid = tgt.component_id
                    if src_cid != tgt_cid and src_cid in self.schematic.components and tgt_cid in self.schematic.components:
                        directed_adj[src_cid].add(tgt_cid)

        # 3. Identify central Hub component (e.g. microcontroller)
        non_standard = [c for c in comps if not c.is_standard]
        candidate_hubs = non_standard if non_standard else comps
        hub = max(candidate_hubs, key=lambda c: (len(c.pins) * 10.0 + sum(adj[c.id].values())))

        # 4. Determine side relative to Hub for all components
        comp_side: Dict[str, Side] = {hub.id: Side.LEFT}
        side_votes: Dict[str, Dict[Side, float]] = {}

        for net in self.schematic.nets:
            if len(net.connections) >= 5 or net.name.upper() in pwr_names:
                continue
            h_conns = [c for c in net.connections if c.component_id == hub.id]
            o_conns = [c for c in net.connections if c.component_id != hub.id]
            if h_conns and o_conns:
                is_pwr = any(k in net.name.upper() for k in ["3V3", "5V", "VCC", "VIN", "VDD"])
                w_conn = 0.5 if is_pwr else 10.0
                for hc in h_conns:
                    h_pin = hub.pins.get(hc.pin_id) if hc.pin_id else hub.get_pin(hc.pin_name)
                    side = h_pin.side if h_pin else Side.LEFT
                    for oc in o_conns:
                        side_votes.setdefault(oc.component_id, {})
                        side_votes[oc.component_id][side] = side_votes[oc.component_id].get(side, 0.0) + w_conn

        for cid, votes in side_votes.items():
            comp_side[cid] = max(votes.keys(), key=lambda s: votes[s])

        # Propagate sides for unassigned chained components
        unassigned = [c.id for c in comps if c.id not in comp_side and c.id != hub.id]
        while unassigned:
            progress = False
            for cid in list(unassigned):
                nbrs = [nbr for nbr in adj[cid] if nbr in comp_side]
                if nbrs:
                    best_nbr = max(nbrs, key=lambda n: adj[cid][n])
                    comp_side[cid] = comp_side[best_nbr]
                    unassigned.remove(cid)
                    progress = True
            if not progress:
                for cid in unassigned:
                    comp_side[cid] = Side.LEFT
                break

        # 5. Coupled pairs (Kelvin sense / differential measurement)
        # Sensed shunt is parent, monitoring IC is child placed directly below it
        parent_of: Dict[str, str] = {}
        child_of: Dict[str, str] = {}
        for net in self.schematic.nets:
            nu = net.name.upper()
            if len(net.connections) == 2 and any(k in nu for k in ["SENSE", "SHUNT", "DIFF"]):
                u_id = net.connections[0].component_id
                v_id = net.connections[1].component_id
                if u_id != hub.id and v_id != hub.id and u_id in self.schematic.components and v_id in self.schematic.components:
                    u = self.schematic.components[u_id]
                    v = self.schematic.components[v_id]
                    p, ch = (u.id, v.id) if len(u.pins) <= len(v.pins) else (v.id, u.id)
                    if ch not in child_of and p not in parent_of.values():
                        parent_of[p] = ch
                        child_of[ch] = p

        # 6. Topological flow depth for left components
        left_comps = [c for c in comps if c.id != hub.id and comp_side.get(c.id) == Side.LEFT and c.id not in child_of]
        depth: Dict[str, int] = {}

        for c in left_comps:
            in_deg = sum(1 for src in left_comps if c.id in directed_adj.get(src.id, set()))
            if in_deg == 0:
                depth[c.id] = 0

        if not depth and left_comps:
            min_c = min(left_comps, key=lambda c: sum(1 for src in left_comps if c.id in directed_adj.get(src.id, set())))
            depth[min_c.id] = 0

        for _ in range(len(left_comps) + 1):
            for u in left_comps:
                if u.id in depth:
                    for v_id in directed_adj.get(u.id, set()):
                        if any(c.id == v_id for c in left_comps):
                            depth[v_id] = max(depth.get(v_id, 0), depth[u.id] + 1)

        for c in left_comps:
            if c.id not in depth:
                known = [k for k in depth if k in adj[c.id]]
                depth[c.id] = max(depth[k] + 1 for k in known) if known else 0

        # Hub-connected sensor or secondary nodes that had in-degree 0 placed near the Hub
        max_d = max(depth.values(), default=0)
        for c in left_comps:
            is_power_source = any(k in c.id.upper() or k in c.type_name.upper() for k in ["BATTERY", "SUPPLY", "CELL", "VCC"])
            if depth[c.id] == 0 and not is_power_source and hub.id in adj[c.id]:
                depth[c.id] = max(1, max_d - 1)

        for ch, p in child_of.items():
            depth[ch] = depth.get(p, 0)

        # 7. Map to column indices
        max_left_depth = max(depth.values(), default=0)
        hub_col = max_left_depth + 1

        comp_col: Dict[str, int] = {hub.id: hub_col}
        for c in left_comps:
            comp_col[c.id] = depth[c.id]
        for ch, p in child_of.items():
            comp_col[ch] = comp_col[p]

        right_comps = [c for c in comps if c.id != hub.id and comp_side.get(c.id) == Side.RIGHT]
        for c in right_comps:
            comp_col[c.id] = hub_col + 1

        used_cols = sorted(set(comp_col.values()))
        remap_col = {old: new for new, old in enumerate(used_cols)}
        for cid in comp_col:
            comp_col[cid] = remap_col[comp_col[cid]]

        columns: Dict[int, List[Component]] = {}
        for c in comps:
            col_idx = comp_col[c.id]
            columns.setdefault(col_idx, []).append(c)

        # 8. Compute column X positions
        col_x: Dict[int, float] = {}
        cur_x = MARGIN
        sorted_col_keys = sorted(columns.keys())
        for col_idx in sorted_col_keys:
            col_comps = columns[col_idx]
            max_w = max(c.width for c in col_comps)
            col_x[col_idx] = cur_x
            cur_x += max_w + COLUMN_CORRIDOR

        # 9. Assign Y coordinates within each column
        for col_idx in sorted_col_keys:
            col_comps = columns[col_idx]

            def comp_y_sort(c: Component):
                if c.id in child_of:
                    return 100
                is_pwr_path = any(k in c.id.upper() or k in c.type_name.upper() for k in ["BATTERY", "FUSE", "DCDC", "SWITCH", "SUPPLY"])
                if is_pwr_path:
                    return 0
                if c.id in parent_of:
                    return 10
                return 30

            col_comps.sort(key=comp_y_sort)

            cur_y = MARGIN
            for c in col_comps:
                c.x = snap_to_grid(col_x[col_idx], GRID_SIZE)
                c.y = snap_to_grid(cur_y, GRID_SIZE)
                cur_y += c.height + ROW_CORRIDOR

        # Post-process coupled pairs: attach child directly under parent
        for p_id, ch_id in parent_of.items():
            p = self.schematic.components[p_id]
            ch = self.schematic.components[ch_id]
            ch.x = p.x
            ch.y = snap_to_grid(p.y + p.height + 60.0, GRID_SIZE)

        # 10. Re-assign pins with true 2D geometry facing connected peers
        allocate_pins_and_dimensions(self.schematic, use_geometry=True)
        # 11. Resolve equivalent pins (e.g. multiple GND/3V3 pins) to nearest candidate
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
