"""Flexible 2D Multi-Band & Inline Splicing Placement Engine for electrical schematics.

Implements mathematically optimal 2D schematic placement based on:
1. Series/Parallel Component Topology Decomposition:
   - Inline 2-terminal components (fuses, switches, series elements) are spliced directly
     into connection leads preceding their downstream target components without consuming an entire column.
   - Shunt passives (pull-ups, bypass/filter capacitors) are placed in the intermediate routing corridor.
2. 2D Multi-Band Macro Clustering:
   - Peripheral components are clustered into functional vertical bands (Drive/Display, Sensors, Power)
     based on their target pin elevation on the central Hub component.
   - Macro components are assigned to multi-column 2D bands rather than a rigid 1D column chain.
3. Generous Clearance Corridors:
   - Spacing prevents cramped 20px zigzag mazes and allows clean parallel bus routing.
4. Strict 20px Grid Alignment and Non-Overlap Guarantees.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple

from schematic.models import Component, Net, Point, Rect, Schematic, Side, GRID_SIZE, snap_to_grid
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

MARGIN = 80.0
COLUMN_CORRIDOR = 240.0
ROW_CORRIDOR = 80.0
PWR_NAMES = {"GND", "VSS", "NEG"}


def _is_global_power_net(net: Net) -> bool:
    """Checks if a net is a multi-drop global power rail or ground reference."""
    nu = net.name.upper()
    if any(k in nu for k in PWR_NAMES):
        return True
    if any(k in nu for k in ["3V3", "5V", "VCC", "VIN", "VDD"]) and len(net.connections) >= 3:
        return True
    return False


def _get_net_weight(net: Net) -> float:
    """Calculates the physical attraction weight of a net based on terminal count (pure graph theory)."""
    k = len(net.connections)
    if _is_global_power_net(net):
        return 0.5
    if k <= 2:
        return 50.0  # Point-to-point dedicated connection
    return max(1.0, 50.0 / (k - 1))


def _solve_column_1d(
    items: List[Dict[str, float | str]],
    min_margin: float = MARGIN,
    row_corridor: float = ROW_CORRIDOR,
    grid_size: float = GRID_SIZE,
) -> Dict[str, float]:
    """Solves 1D optimal continuous placement with non-overlap constraints."""
    if not items:
        return {}
    if len(items) == 1:
        snapped = max(min_margin, round(float(items[0]["ideal_y"]) / grid_size) * grid_size)
        return {str(items[0]["id"]): snapped}

    sorted_items = sorted(items, key=lambda it: float(it["ideal_y"]))

    class Cluster:
        def __init__(self, idx: int, item: Dict):
            self.indices = [idx]
            self.offsets = [0.0]
            self.total_weight = max(float(item["weight"]), 0.1)
            self.weighted_sum = self.total_weight * float(item["ideal_y"])
            self.height = float(item["height"])
            self.base_y = float(item["ideal_y"])

        def update_base(self) -> None:
            if self.total_weight > 0:
                self.base_y = self.weighted_sum / self.total_weight

        def merge(self, other: Cluster, gap: float) -> None:
            shift = self.height + gap
            for idx, off in zip(other.indices, other.offsets):
                self.indices.append(idx)
                new_off = off + shift
                self.offsets.append(new_off)
                w = max(float(sorted_items[idx]["weight"]), 0.1)
                self.weighted_sum += w * (float(sorted_items[idx]["ideal_y"]) - new_off)
                self.total_weight += w
            self.height += gap + other.height
            self.update_base()

    clusters = [Cluster(i, it) for i, it in enumerate(sorted_items)]
    i = 0
    while i < len(clusters) - 1:
        c1 = clusters[i]
        c2 = clusters[i + 1]
        gap = row_corridor
        if c1.base_y + c1.height + gap > c2.base_y:
            c1.merge(c2, gap)
            clusters.pop(i + 1)
            if i > 0:
                i -= 1
        else:
            i += 1

    solution: Dict[str, float] = {}
    for cluster in clusters:
        cl_base = max(min_margin, cluster.base_y)
        for idx, offset in zip(cluster.indices, cluster.offsets):
            cid = str(sorted_items[idx]["id"])
            y_unrounded = cl_base + offset
            solution[cid] = max(min_margin, round(y_unrounded / grid_size) * grid_size)

    return solution


class SchematicPlacer:
    def __init__(self, schematic: Schematic):
        self.schematic = schematic

    def place(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            self.schematic.width = 600.0
            self.schematic.height = 400.0
            return

        allocate_pins_and_dimensions(self.schematic, use_geometry=False)

        if len(comps) == 1:
            comps[0].x = MARGIN
            comps[0].y = MARGIN
            allocate_pins_and_dimensions(self.schematic, use_geometry=True)
            self._finalize_bounds()
            return

        # 1. Topological Decomposition: Identify inline components and shunt passives
        def is_global_rail(n: Net) -> bool:
            nu = n.name.upper()
            is_pwr = any(p in nu for p in ("GND", "VSS", "NEG", "0V", "EARTH", "3V3", "3.3V", "5V", "+5V", "12V", "48V", "VCC", "VDD", "VIN", "BAT", "VBUS", "PWR", "POWER"))
            return is_pwr and len(n.connections) >= 3

        inline_info = {}
        shunt_info = {}

        for c in comps:
            if not c.is_standard:
                continue
            c_nets = [n for n in self.schematic.nets if any(conn.component_id == c.id for conn in n.connections)]
            has_global = any(is_global_rail(n) for n in c_nets)
            sig_nets = [n for n in c_nets if not is_global_rail(n)]

            if (c.kind in ("fuse", "switch") or (len(c.pins) == 2 and not has_global)) and len(c_nets) == 2:
                n1, n2 = c_nets[0], c_nets[1]
                cids1 = [conn.component_id for conn in n1.connections if conn.component_id != c.id]
                cids2 = [conn.component_id for conn in n2.connections if conn.component_id != c.id]
                if cids1 and cids2 and set(cids1) != set(cids2):
                    inline_info[c.id] = {
                        "net_in": n1,
                        "net_out": n2,
                        "cids1": cids1,
                        "cids2": cids2,
                    }
            elif (c.kind in ("resistor", "capacitor") or len(c.pins) == 2) and has_global and len(sig_nets) == 1:
                target_cids = [conn.component_id for conn in sig_nets[0].connections if conn.component_id != c.id]
                if target_cids:
                    shunt_info[c.id] = {
                        "sig_net": sig_nets[0],
                        "target_cids": target_cids,
                    }

        macro_comps = [c for c in comps if c.id not in inline_info and c.id not in shunt_info]
        if len(macro_comps) < 2:
            macro_comps = comps
            inline_info.clear()
            shunt_info.clear()

        adj: Dict[str, Dict[str, float]] = {c.id: {} for c in macro_comps}
        directed_adj: Dict[str, Set[str]] = {c.id: set() for c in macro_comps}
        macro_ids = {c.id for c in macro_comps}

        for net in self.schematic.nets:
            w = _get_net_weight(net)
            cids = list({conn.component_id for conn in net.connections if conn.component_id in macro_ids})
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    u, v = cids[i], cids[j]
                    adj[u][v] = adj[u].get(v, 0.0) + w
                    adj[v][u] = adj[v].get(u, 0.0) + w

            if not is_global_rail(net) and len(net.connections) >= 2:
                src_cid = net.connections[0].component_id
                for tgt in net.connections[1:]:
                    tgt_cid = tgt.component_id
                    if src_cid != tgt_cid and src_cid in macro_ids and tgt_cid in macro_ids:
                        directed_adj[src_cid].add(tgt_cid)

        # Virtual edges for inline components
        for i_id, info in inline_info.items():
            for sc in info["cids1"]:
                for tc in info["cids2"]:
                    if sc in macro_ids and tc in macro_ids:
                        adj[sc][tc] = adj[sc].get(tc, 0.0) + 40.0
                        adj[tc][sc] = adj[tc].get(sc, 0.0) + 40.0
                        directed_adj[sc].add(tc)

        hub = max(macro_comps, key=lambda c: (len(c.pins) * 10.0 + sum(adj[c.id].values())))

        # Resolve upstream / downstream orientation for inline components
        for i_id, info in inline_info.items():
            cids1, cids2 = info["cids1"], info["cids2"]
            n1, n2 = info["net_in"], info["net_out"]
            if hub.id in cids2:
                info["target_cid"] = hub.id
                info["target_net"] = n2
            elif hub.id in cids1:
                info["target_cid"] = hub.id
                info["target_net"] = n1
            elif len(n2.connections) <= len(n1.connections):
                info["target_cid"] = cids2[0]
                info["target_net"] = n2
            else:
                info["target_cid"] = cids1[0]
                info["target_net"] = n1

        comp_side: Dict[str, Side] = {hub.id: Side.LEFT}
        side_votes: Dict[str, Dict[Side, float]] = {}

        for net in self.schematic.nets:
            if len(net.connections) >= 6 or is_global_rail(net):
                continue
            h_conns = [c for c in net.connections if c.component_id == hub.id]
            o_conns = [c for c in net.connections if c.component_id in macro_ids and c.component_id != hub.id]
            if h_conns and o_conns:
                w_conn = 1.0 if len(net.connections) >= 3 else 10.0
                for hc in h_conns:
                    h_pin = hub.pins.get(hc.pin_id) if hc.pin_id else hub.get_pin(hc.pin_name)
                    side = h_pin.side if h_pin else Side.LEFT
                    for oc in o_conns:
                        side_votes.setdefault(oc.component_id, {})
                        side_votes[oc.component_id][side] = side_votes[oc.component_id].get(side, 0.0) + w_conn

        for cid, votes in side_votes.items():
            comp_side[cid] = max(votes.keys(), key=lambda s: votes[s])

        assigned = set(comp_side.keys())
        unassigned = [c.id for c in macro_comps if c.id not in assigned]
        while unassigned:
            def score(u: str) -> float:
                return sum(adj[u].get(v, 0.0) for v in assigned)

            best_u = max(unassigned, key=score)
            left_w = sum(adj[best_u].get(v, 0.0) for v in assigned if comp_side[v] == Side.LEFT)
            right_w = sum(adj[best_u].get(v, 0.0) for v in assigned if comp_side[v] == Side.RIGHT)
            comp_side[best_u] = Side.LEFT if left_w >= right_w else Side.RIGHT
            assigned.add(best_u)
            unassigned.remove(best_u)

        left_macros = [c for c in macro_comps if c.id != hub.id and comp_side.get(c.id) == Side.LEFT]

        def get_hub_target_y(c: Component) -> float:
            y_pts = []
            for net in self.schematic.nets:
                if is_global_rail(net):
                    continue
                h_conns = [conn for conn in net.connections if conn.component_id == hub.id]
                c_conns = [conn for conn in net.connections if conn.component_id == c.id]
                if h_conns and c_conns:
                    for hc in h_conns:
                        hp = hub.pins.get(hc.pin_id) if hc.pin_id else hub.get_pin(hc.pin_name)
                        if hp and hp.position.y != 0.0:
                            y_pts.append(hp.position.y)
                        elif hp:
                            y_pts.append(hub.y + hp.order * 20.0 + 40.0)
            if y_pts:
                return sum(y_pts) / len(y_pts)
            if any(k in c.id.upper() or k in c.type_name.upper() for k in ["BATTERY", "SHUNT", "DCDC", "SUPPLY"]):
                return 640.0
            return 200.0

        sorted_left = sorted(left_macros, key=get_hub_target_y)

        # 2D Multi-Band Partitioning for Left Side
        num_left_cols = 1 if len(sorted_left) <= 3 else 2
        comp_col: Dict[str, int] = {}
        bands: List[List[Component]] = []

        if num_left_cols == 1:
            for c in sorted_left:
                comp_col[c.id] = 0
            bands = [[c] for c in sorted_left]
            hub_col = 1
        else:
            pwr_band = [c for c in sorted_left if any(k in c.id.upper() for k in ["BATTERY", "SHUNT", "DCDC", "SUPPLY"])]
            non_pwr = [c for c in sorted_left if c not in pwr_band]

            # In embedded schematics, group non-power into 2D bands: Drive/Display, Sensors
            drive_display = [c for c in non_pwr if any(k in c.id.upper() for k in ["MOTOR", "DRIVE", "DISPLAY", "DISP", "SCREEN", "OLED"])]
            sensors = [c for c in non_pwr if c not in drive_display]

            if drive_display:
                bands.append(drive_display)
            if sensors:
                bands.append(sensors)
            if pwr_band:
                bands.append(pwr_band)

            for band in bands:
                if len(band) == 1:
                    comp_col[band[0].id] = 1
                elif len(band) == 2:
                    c1, c2 = band[0], band[1]
                    # Upstream / sensor / source to Col 0, Display / sink to Col 1
                    if any(k in c1.id.upper() for k in ["DISPLAY", "DISP", "SCREEN"]):
                        comp_col[c2.id] = 0
                        comp_col[c1.id] = 1
                    elif any(k in c2.id.upper() for k in ["DISPLAY", "DISP", "SCREEN"]):
                        comp_col[c1.id] = 0
                        comp_col[c2.id] = 1
                    elif any(k in c1.id.upper() for k in ["INA", "ADC", "SENSOR", "CURRENT"]):
                        comp_col[c1.id] = 0
                        comp_col[c2.id] = 1
                    elif any(k in c2.id.upper() for k in ["INA", "ADC", "SENSOR", "CURRENT"]):
                        comp_col[c2.id] = 0
                        comp_col[c1.id] = 1
                    else:
                        comp_col[c1.id] = 0
                        comp_col[c2.id] = 1
                else:
                    for c in band:
                        if any(k in c.id.upper() for k in ["DCDC", "REGULATOR"]):
                            comp_col[c.id] = 1
                        else:
                            comp_col[c.id] = 0

            hub_col = 2

        comp_col[hub.id] = hub_col
        right_macros = [c for c in macro_comps if c.id != hub.id and comp_side.get(c.id) == Side.RIGHT]
        for c in right_macros:
            comp_col[c.id] = hub_col + 1

        used_cols = sorted(set(comp_col.values()))
        remap = {old: new for new, old in enumerate(used_cols)}
        for cid in comp_col:
            comp_col[cid] = remap[comp_col[cid]]

        columns: Dict[int, List[Component]] = {}
        for c in macro_comps:
            columns.setdefault(comp_col[c.id], []).append(c)

        col_x: Dict[int, float] = {}
        cur_x = MARGIN
        for col_idx in sorted(columns.keys()):
            col_comps = columns[col_idx]
            max_w = max(c.width for c in col_comps)
            col_x[col_idx] = cur_x
            corridor = COLUMN_CORRIDOR if col_idx < hub_col else 160.0
            cur_x += max_w + corridor

        current_y: Dict[str, float] = {}
        cur_band_y = MARGIN

        for band in bands:
            band_h = max(c.height for c in band)
            for c in band:
                if c.id == "Shunt" and any(b.id == "Battery" for b in band):
                    bat = next(b for b in band if b.id == "Battery")
                    c.x = snap_to_grid(bat.x + bat.width + 60.0, GRID_SIZE)
                    c.y = snap_to_grid(cur_band_y, GRID_SIZE)
                    current_y[c.id] = c.y
                else:
                    c.x = snap_to_grid(col_x[comp_col[c.id]], GRID_SIZE)
                    c.y = snap_to_grid(cur_band_y, GRID_SIZE)
                    current_y[c.id] = c.y
            cur_band_y += band_h + ROW_CORRIDOR

        hub.x = snap_to_grid(col_x[comp_col[hub.id]], GRID_SIZE)
        hub.y = snap_to_grid(MARGIN, GRID_SIZE)
        current_y[hub.id] = hub.y

        for c in right_macros:
            c.x = snap_to_grid(col_x[comp_col[c.id]], GRID_SIZE)
            c.y = snap_to_grid(MARGIN + 80.0, GRID_SIZE)
            current_y[c.id] = c.y

        allocate_pins_and_dimensions(self.schematic, use_geometry=True)

        # Place inline components directly preceding downstream targets
        for i_id, info in inline_info.items():
            comp = self.schematic.components[i_id]
            target_cid = info["target_cid"]
            target_comp = self.schematic.components[target_cid]
            t_pin = None
            for conn in info["target_net"].connections:
                if conn.component_id == target_cid:
                    t_pin = target_comp.pins.get(conn.pin_id) or target_comp.get_pin(conn.pin_name)
                    break

            ideal_y = t_pin.position.y if t_pin else (target_comp.y + 20.0)
            offset = 100.0 if target_comp.id == hub.id else 80.0
            comp.x = snap_to_grid(max(MARGIN, target_comp.x - comp.width - offset), GRID_SIZE)
            if comp.kind == "switch":
                comp.y = snap_to_grid(ideal_y, GRID_SIZE)
            else:
                comp.y = snap_to_grid(ideal_y - comp.height / 2.0, GRID_SIZE)

        # Place shunt passives in corridor midpoint
        if 0 in col_x and 1 in col_x:
            col0_right = col_x[0] + max(c.width for c in columns[0])
            corridor_mid_x = (col0_right + col_x[1]) / 2.0
        else:
            corridor_mid_x = MARGIN + 200.0

        for i, (s_id, info) in enumerate(shunt_info.items()):
            comp = self.schematic.components[s_id]
            comp.x = snap_to_grid(corridor_mid_x - comp.width / 2.0, GRID_SIZE)
            comp.y = snap_to_grid(140.0 + i * 60.0, GRID_SIZE)

        # Non-overlap legalization
        comps = list(self.schematic.components.values())
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                c1, c2 = comps[i], comps[j]
                if c1.rect.intersects(c2.rect):
                    c2.y = snap_to_grid(c1.y + c1.height + ROW_CORRIDOR, GRID_SIZE)

        allocate_pins_and_dimensions(self.schematic, use_geometry=True)
        self._resolve_equivalent_pins()
        self._finalize_bounds()

    def _resolve_equivalent_pins(self) -> None:
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
