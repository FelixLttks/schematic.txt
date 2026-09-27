"""Port-Aware Hierarchical Placement (PA-HP) Engine for electrical schematics.

Implements mathematically optimal 2D schematic placement based on:
1. Stage-Ranked DAG Flow Layering (X-axis column assignment with ALAP sink clustering).
2. Port-Aware Two-Sided Barycentric Sweeps (iterative quadratic wirelength minimization).
3. 1D Optimal Isotonic Grid Legalization via Cluster Merging (Pool Adjacent Violators Algorithm).
4. Strict 20px grid alignment and guaranteed non-overlapping clearance corridors.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import Component, Net, Point, Rect, Schematic, Side, GRID_SIZE, snap_to_grid
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

MARGIN = 80.0
COLUMN_CORRIDOR = 160.0
ROW_CORRIDOR = 60.0
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
    """
    Solves 1D optimal continuous placement with non-overlap constraints:
        min sum w_i * (y_i - ideal_y_i)^2
        s.t. y_{i+1} >= y_i + height_i + row_corridor
             y_1 >= min_margin
             y_i in Lambda (20px grid aligned)
    via Cluster Merging (Isotonic Regression / Pool Adjacent Violators) followed by discrete grid snapping.
    """
    if not items:
        return {}
    if len(items) == 1:
        snapped = max(min_margin, round(float(items[0]["ideal_y"]) / grid_size) * grid_size)
        return {str(items[0]["id"]): snapped}

    # Sort items primarily by ideal_y
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

    # Pool Adjacent Violators: merge overlapping clusters
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

    # Enforce minimum canvas margin
    for c in clusters:
        if c.base_y < min_margin:
            c.base_y = min_margin

    # Re-check boundary collisions
    i = 0
    while i < len(clusters) - 1:
        c1 = clusters[i]
        c2 = clusters[i + 1]
        gap = row_corridor
        if c1.base_y + c1.height + gap > c2.base_y:
            c2.base_y = c1.base_y + c1.height + gap
        i += 1

    # Extract coordinates snapped strictly to 20px grid without overlapping
    res: Dict[str, float] = {}
    last_bottom = min_margin - row_corridor
    for c in clusters:
        for idx, off in zip(c.indices, c.offsets):
            it = sorted_items[idx]
            ideal = c.base_y + off
            snapped = round(ideal / grid_size) * grid_size
            min_allowed = last_bottom + row_corridor
            min_allowed_snap = round(min_allowed / grid_size) * grid_size
            if min_allowed_snap < min_allowed:
                min_allowed_snap += grid_size
            actual_y = max(snapped, min_allowed_snap)
            res[str(it["id"])] = actual_y
            last_bottom = actual_y + float(it["height"])

    return res


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
        adj: Dict[str, Dict[str, float]] = {c.id: {} for c in comps}
        directed_adj: Dict[str, Set[str]] = {c.id: set() for c in comps}

        for net in self.schematic.nets:
            w = _get_net_weight(net)
            cids = list({conn.component_id for conn in net.connections if conn.component_id in self.schematic.components})
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    u, v = cids[i], cids[j]
                    adj[u][v] = adj[u].get(v, 0.0) + w
                    adj[v][u] = adj[v].get(u, 0.0) + w

            if net.name.upper() not in PWR_NAMES and len(net.connections) >= 2:
                src_cid = net.connections[0].component_id
                for tgt in net.connections[1:]:
                    tgt_cid = tgt.component_id
                    if src_cid != tgt_cid and src_cid in self.schematic.components and tgt_cid in self.schematic.components:
                        directed_adj[src_cid].add(tgt_cid)

        # 3. Identify central Hub component by graph centrality (pin count and connection weight)
        hub = max(comps, key=lambda c: (len(c.pins) * 10.0 + sum(adj[c.id].values())))

        # 4. Determine side relative to Hub for all components via Maximum Adjacency Ordering (MAO)
        comp_side: Dict[str, Side] = {hub.id: Side.LEFT}
        side_votes: Dict[str, Dict[Side, float]] = {}

        for net in self.schematic.nets:
            if len(net.connections) >= 6 or net.name.upper() in PWR_NAMES:
                continue
            h_conns = [c for c in net.connections if c.component_id == hub.id]
            o_conns = [c for c in net.connections if c.component_id != hub.id]
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

        # Maximum Adjacency Ordering: assign remaining components based on strongest connection to assigned set
        assigned = set(comp_side.keys())
        unassigned = [c.id for c in comps if c.id not in assigned]
        while unassigned:
            def score(u: str) -> float:
                return sum(adj[u].get(v, 0.0) for v in assigned)

            best_u = max(unassigned, key=score)
            left_w = sum(adj[best_u].get(v, 0.0) for v in assigned if comp_side[v] == Side.LEFT)
            right_w = sum(adj[best_u].get(v, 0.0) for v in assigned if comp_side[v] == Side.RIGHT)
            comp_side[best_u] = Side.LEFT if left_w >= right_w else Side.RIGHT
            assigned.add(best_u)
            unassigned.remove(best_u)

        # 5. Topological flow depth for left components (DAG Longest Path)
        left_comps = [c for c in comps if c.id != hub.id and comp_side.get(c.id) == Side.LEFT]
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

        max_d = max(depth.values(), default=0)
        hub_col = max_d + 1
        left_comp_ids = {c.id for c in left_comps}

        # 6. Graph-Theoretic ALAP + Layer Balancing (Gansner et al. / Coffman-Graham)
        # Compute topological ALAP upper bound
        alap = {c.id: max_d for c in left_comps}
        for _ in range(len(left_comps) + 1):
            for u in left_comps:
                out_succs = [v_id for v_id in directed_adj.get(u.id, set()) if v_id in left_comp_ids]
                if out_succs:
                    alap[u.id] = min(alap[u.id], min(alap[s] - 1 for s in out_succs))

        # Assign rigid nodes first (where asap == alap)
        layer_assign: Dict[str, int] = {}
        for c in left_comps:
            if depth[c.id] == alap[c.id]:
                layer_assign[c.id] = depth[c.id]

        # Place flexible nodes to minimize total weighted edge span with layer capacity balancing
        flex_comps = [c for c in left_comps if c.id not in layer_assign]
        flex_comps.sort(key=lambda c: -sum(adj[c.id].values()))

        alpha = 30.0  # Layer capacity penalty per component to prevent overcrowding
        for c in flex_comps:
            best_L = depth[c.id]
            best_cost = float('inf')
            for L in range(depth[c.id], alap[c.id] + 1):
                edge_cost = 0.0
                for peer_id, w in adj[c.id].items():
                    if peer_id == hub.id:
                        peer_L = hub_col
                    elif peer_id in layer_assign:
                        peer_L = layer_assign[peer_id]
                    elif peer_id in depth:
                        peer_L = (depth[peer_id] + alap[peer_id]) / 2.0
                    else:
                        continue
                    edge_cost += w * abs(L - peer_L)

                col_count = sum(1 for cid, cl in layer_assign.items() if cl == L)
                cost = edge_cost + alpha * col_count
                if cost < best_cost:
                    best_cost = cost
                    best_L = L
            layer_assign[c.id] = best_L

        # Map to column indices
        max_left_depth = max(layer_assign.values(), default=0)
        hub_col = max_left_depth + 1

        comp_col: Dict[str, int] = {hub.id: hub_col}
        for c in left_comps:
            comp_col[c.id] = layer_assign[c.id]

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

        # 7. Compute column X positions
        col_x: Dict[int, float] = {}
        cur_x = MARGIN
        sorted_col_keys = sorted(columns.keys())
        for col_idx in sorted_col_keys:
            col_comps = columns[col_idx]
            max_w = max(c.width for c in col_comps)
            col_x[col_idx] = cur_x
            cur_x += max_w + COLUMN_CORRIDOR

        # 8. Port-Aware Two-Sided Barycentric Sweeps + 1D Optimal Grid Legalization
        current_y: Dict[str, float] = {}
        for col_idx in sorted_col_keys:
            col_comps = columns[col_idx]
            for i, c in enumerate(col_comps):
                init_y = MARGIN + i * (c.height + ROW_CORRIDOR)
                c.y = snap_to_grid(init_y, GRID_SIZE)
                c.x = snap_to_grid(col_x[col_idx], GRID_SIZE)
                current_y[c.id] = c.y

        # Anchor Hub MCU at MARGIN
        current_y[hub.id] = MARGIN
        hub.y = MARGIN

        # 4 Iterative sweeps (alternating forward/backward) for global convergence
        for sweep in range(4):
            allocate_pins_and_dimensions(self.schematic, use_geometry=True)
            col_order = sorted_col_keys if (sweep % 2 == 0) else list(reversed(sorted_col_keys))
            for col_idx in col_order:
                col_comps = columns[col_idx]
                if not col_comps:
                    continue
                if len(col_comps) == 1 and col_comps[0].id == hub.id:
                    current_y[hub.id] = MARGIN
                    hub.y = MARGIN
                    continue

                items: List[Dict[str, float | str]] = []
                for c in col_comps:
                    if c.id == hub.id:
                        ideal_y = MARGIN
                        w = 1000.0
                    else:
                        ideal_y, w = self._calculate_component_ideal_y(c, current_y, comp_col)

                    items.append({
                        "id": c.id,
                        "ideal_y": ideal_y,
                        "height": c.height,
                        "weight": w,
                    })

                placed_y = _solve_column_1d(items, min_margin=MARGIN, row_corridor=ROW_CORRIDOR, grid_size=GRID_SIZE)
                for c in col_comps:
                    new_y = placed_y[c.id]
                    c.y = new_y
                    current_y[c.id] = new_y

        # Final application of coordinates
        for c in comps:
            c.x = snap_to_grid(col_x[comp_col[c.id]], GRID_SIZE)
            c.y = snap_to_grid(current_y[c.id], GRID_SIZE)

        # 9. Re-assign pins with true 2D geometry facing connected peers
        allocate_pins_and_dimensions(self.schematic, use_geometry=True)
        # 10. Resolve equivalent pins to nearest candidate
        self._resolve_equivalent_pins()
        self._finalize_bounds()

    def _calculate_component_ideal_y(
        self,
        comp: Component,
        current_y: Dict[str, float],
        comp_col: Dict[str, int],
    ) -> Tuple[float, float]:
        """
        Calculates the port-aware weighted target Y coordinate for comp
        based on the current positions of its connected peer pins in other columns.
        """
        my_col = comp_col[comp.id]
        comp_nets = [net for net in self.schematic.nets if any(c.component_id == comp.id for c in net.connections)]
        signal_nets = [net for net in comp_nets if not _is_global_power_net(net)]

        has_inter_col_signal = False
        for net in signal_nets:
            for peer in net.connections:
                if peer.component_id != comp.id and comp_col.get(peer.component_id) != my_col:
                    has_inter_col_signal = True
                    break
            if has_inter_col_signal:
                break

        active_nets = signal_nets if has_inter_col_signal else comp_nets

        num = 0.0
        den = 0.0
        cy_offset = comp.height / 2.0

        for p in comp.pins.values():
            p_off_y = (p.position.y - comp.y) if p.position.y != 0.0 else cy_offset
            for net in active_nets:
                if not any(c.component_id == comp.id and (c.pin_name == p.name or c.pin_id == p.id) for c in net.connections):
                    continue
                w = _get_net_weight(net)
                for peer in net.connections:
                    if peer.component_id == comp.id:
                        continue
                    peer_comp = self.schematic.components.get(peer.component_id)
                    if not peer_comp or peer_comp.id not in current_y:
                        continue
                    if comp_col.get(peer_comp.id) == my_col:
                        continue

                    peer_pin = peer_comp.pins.get(peer.pin_id) if peer.pin_id else peer_comp.get_pin(peer.pin_name)
                    peer_off_y = (
                        (peer_pin.position.y - peer_comp.y)
                        if peer_pin and peer_pin.position.y != 0.0
                        else (peer_comp.height / 2.0)
                    )
                    peer_abs_y = current_y[peer_comp.id] + peer_off_y
                    target_top_y = peer_abs_y - p_off_y
                    num += w * target_top_y
                    den += w

        if den > 0:
            return num / den, den
        return MARGIN, 1.0

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
