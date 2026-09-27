"""General 2D Obstacle-Avoiding Rectilinear Grid Router with Steiner Tree synthesis.

Enforces fundamental mathematical invariants:
1. Strict 20px grid alignment for all points, terminals, corners, and junctions.
2. Obstacle avoidance: wires strictly avoid component bodies.
3. Zero collinear edge overlap between different nets. Orthogonal crossings at 90° permitted.
4. Terminal integrity: wires connect precisely to pin terminal coordinates.
5. Minimum bends via directional A* penalty.
6. Steiner tree branch points receive junction dots.
"""

from __future__ import annotations
import heapq
import math
from typing import Dict, List, Optional, Set, Tuple
from schematic.models import (
    GRID_SIZE,
    Component,
    Net,
    Pin,
    Point,
    Rect,
    Schematic,
    Side,
    WireJunction,
    WireSegment,
    snap_to_grid,
)

BEND_PENALTY = 80.0
STEP_COST = 20.0
REUSE_COST = 2.0
CROSSING_PENALTY = 50.0
PIN_ESCAPE_PENALTY = 60.0


def _norm_edge(p1: Tuple[float, float], p2: Tuple[float, float]) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    return (p1, p2) if p1 <= p2 else (p2, p1)


class SchematicRouter:
    def __init__(self, schematic: Schematic):
        self.schematic = schematic
        self.segments: List[WireSegment] = []
        self.junctions: List[WireJunction] = []
        self.edge_occupant: Dict[Tuple[Tuple[float, float], Tuple[float, float]], str] = {}
        self.node_occupants: Dict[Tuple[float, float], Set[str]] = {}
        self.blocked_nodes: Set[Tuple[float, float]] = set()
        self.pin_escape_nodes: Dict[Tuple[float, float], Set[Tuple[str, str]]] = {}
        self.pin_corridor_info: Dict[Tuple[float, float], Set[Tuple[str, str]]] = {}

    def route(self) -> None:
        self.segments.clear()
        self.junctions.clear()
        self.edge_occupant.clear()
        self.node_occupants.clear()
        self.blocked_nodes.clear()

        comps = list(self.schematic.components.values())
        if not comps:
            return

        # 1. Define routing canvas bounds with margin
        pad = 160.0
        min_x = snap_to_grid(min(c.x for c in comps) - pad, GRID_SIZE)
        max_x = snap_to_grid(max(c.x + c.width for c in comps) + pad, GRID_SIZE)
        min_y = snap_to_grid(min(c.y for c in comps) - pad, GRID_SIZE)
        max_y = snap_to_grid(max(c.y + c.height for c in comps) + pad, GRID_SIZE)

        # 2. Block nodes strictly within component bodies
        for c in comps:
            cur_x = c.x
            while cur_x <= c.x + c.width:
                cur_y = c.y
                while cur_y <= c.y + c.height:
                    self.blocked_nodes.add((round(cur_x, 1), round(cur_y, 1)))
                    cur_y += GRID_SIZE
                cur_x += GRID_SIZE

        # Block all pin terminals initially so foreign nets cannot route through them
        for c in comps:
            for p in c.pins.values():
                self.blocked_nodes.add((round(p.position.x, 1), round(p.position.y, 1)))

        # Identify immediate pin escape nodes (k=1) and corridors (k=1..3)
        self.pin_escape_nodes: Dict[Tuple[float, float], Set[Tuple[str, str]]] = {}
        self.pin_corridor_info: Dict[Tuple[float, float], Set[Tuple[str, str]]] = {}
        for c in comps:
            for p in c.pins.values():
                for k in range(1, 4):
                    if p.side == Side.LEFT:
                        node = (round(p.position.x - k * GRID_SIZE, 1), round(p.position.y, 1))
                    elif p.side == Side.RIGHT:
                        node = (round(p.position.x + k * GRID_SIZE, 1), round(p.position.y, 1))
                    elif p.side == Side.TOP:
                        node = (round(p.position.x, 1), round(p.position.y - k * GRID_SIZE, 1))
                    else:
                        node = (round(p.position.x, 1), round(p.position.y + k * GRID_SIZE, 1))
                    if k == 1:
                        self.pin_escape_nodes.setdefault(node, set()).add((c.id, p.name))
                    self.pin_corridor_info.setdefault(node, set()).add((c.id, p.name))

        # 3. Sort nets: Point-to-point nets first for direct routing, then multi-drop buses and ground rails
        def net_sort_key(net: Net) -> Tuple[int, int, str]:
            nu = net.name.upper()
            is_return = any(p in nu for p in ["GND", "VSS", "NEG"])
            is_multi = len(net.connections) >= 3
            prio = 2 if is_return else (1 if is_multi else 0)
            return (prio, -len(net.connections), net.name)

        sorted_nets = sorted(self.schematic.nets, key=net_sort_key)

        for net in sorted_nets:
            color = self._assign_net_color(net.name)
            net.color = color

            pins = self._get_net_pin_positions(net)
            if len(pins) < 2:
                continue

            # Temporarily unblock this net's pin terminals
            for p in pins:
                self.blocked_nodes.discard(p)

            tree_nodes: Set[Tuple[float, float]] = {pins[0]}

            tree_terminal_set = set(pins)
            net_edges: Set[Tuple[Tuple[float, float], Tuple[float, float]]] = set()

            unrouted = [p for p in pins if p not in tree_nodes]
            retry_budget = len(pins) * 2
            while unrouted and retry_budget > 0:
                retry_budget -= 1
                pin = min(unrouted, key=lambda p: min(abs(p[0] - tn[0]) + abs(p[1] - tn[1]) for tn in tree_nodes))
                unrouted.remove(pin)

                path = self._a_star(pin, tree_nodes, net, min_x, max_x, min_y, max_y)
                if not path:
                    if unrouted:
                        unrouted.append(pin)
                    continue

                # Register used edges and nodes
                for i in range(len(path) - 1):
                    e = _norm_edge(path[i], path[i + 1])
                    self.edge_occupant[e] = net.name
                    net_edges.add(e)
                    tree_nodes.add(path[i])
                    tree_nodes.add(path[i + 1])
                    self.node_occupants.setdefault(path[i], set()).add(net.name)
                    self.node_occupants.setdefault(path[i + 1], set()).add(net.name)

                # Merge contiguous collinear units into long segments (splitting at pin terminals)
                for s, e in self._merge_path(path, tree_terminal_set):
                    self.segments.append(WireSegment(Point(s[0], s[1]), Point(e[0], e[1]), net.name, color))

            # Re-block current net's pin terminals for subsequent nets
            for p in pins:
                self.blocked_nodes.add(p)

            # Topological junction detection:
            # - Degree >= 3 in wire graph (T-junctions, cross-junctions)
            # - Degree >= 2 at a component pin terminal (branching at pin)
            deg: Dict[Tuple[float, float], int] = {}
            for u, v in net_edges:
                deg[u] = deg.get(u, 0) + 1
                deg[v] = deg.get(v, 0) + 1

            for node, d in deg.items():
                if d >= 3 or (d >= 2 and node in tree_terminal_set):
                    self.junctions.append(WireJunction(point=Point(node[0], node[1]), radius=3.5, net_name=net.name))

        self.schematic.wires = self.segments
        self.schematic.junctions = self.junctions

    def _get_net_pin_positions(self, net: Net) -> List[Tuple[float, float]]:
        positions: List[Tuple[float, float]] = []
        for conn in net.connections:
            comp = self.schematic.components.get(conn.component_id)
            if not comp:
                continue
            pin = comp.pins.get(conn.pin_id) if conn.pin_id else comp.get_pin(conn.pin_name)
            if pin:
                pos = (round(pin.position.x, 1), round(pin.position.y, 1))
                if pos not in positions:
                    positions.append(pos)
        return positions

    def _a_star(
        self,
        start_node: Tuple[float, float],
        targets: Set[Tuple[float, float]],
        net: Net,
        min_x: float,
        max_x: float,
        min_y: float,
        max_y: float,
    ) -> Optional[List[Tuple[float, float]]]:
        if start_node in targets:
            return [start_node]

        net_name = net.name
        net_pins = {(c.component_id, c.pin_name) for c in net.connections}

        def h(x: float, y: float) -> float:
            return min(abs(x - tx) + abs(y - ty) for tx, ty in targets)

        pq = []
        heapq.heappush(pq, (h(*start_node), 0.0, start_node[0], start_node[1], 0.0, 0.0))
        dist: Dict[Tuple[Tuple[float, float], float, float], float] = {}
        came_from: Dict[Tuple[Tuple[float, float], float, float], Tuple[Tuple[float, float], float, float]] = {}
        target_reached: Optional[Tuple[float, float]] = None

        dirs = [(GRID_SIZE, 0.0), (-GRID_SIZE, 0.0), (0.0, GRID_SIZE), (0.0, -GRID_SIZE)]

        while pq:
            f, g, x, y, dx, dy = heapq.heappop(pq)
            u = (round(x, 1), round(y, 1))

            if u in targets:
                target_reached = u
                break

            state = (u, dx, dy)
            if state in dist and dist[state] < g - 1e-4:
                continue

            for ndx, ndy in dirs:
                nx = round(x + ndx, 1)
                ny = round(y + ndy, 1)
                v = (nx, ny)

                if not (min_x <= nx <= max_x and min_y <= ny <= max_y):
                    continue
                if v in self.blocked_nodes and v not in targets:
                    continue

                e = _norm_edge(u, v)
                occ = self.edge_occupant.get(e)
                if occ is not None and occ != net_name:
                    continue  # Blocked by another net: zero collinear overlap!

                is_bend = (dx != 0.0 or dy != 0.0) and (ndx != dx or ndy != dy)

                # Strict rule 1: A foreign net may NEVER bend at a node visited by another net!
                u_occs = self.node_occupants.get(u)
                if is_bend and u_occs and net_name not in u_occs:
                    continue

                # Strict rule 2: A foreign net may NEVER bend inside an active pin's immediate escape node (k=1)!
                foreign_escape_u = self.pin_escape_nodes.get(u)
                if is_bend and foreign_escape_u and not any(p in net_pins for p in foreign_escape_u):
                    continue

                step_cost = STEP_COST + (BEND_PENALTY if is_bend else 0.0)

                # Reusing existing segments of the same net has very low cost (Steiner branch)
                if occ == net_name:
                    step_cost = REUSE_COST

                # Crossing penalty: crossing a foreign net's straight wire orthogonally
                v_occs = self.node_occupants.get(v)
                if v_occs and net_name not in v_occs:
                    step_cost += CROSSING_PENALTY

                # Breakout corridor protection: foreign nets avoid blocking unowned pin fanout nodes
                foreign_corridor_v = self.pin_corridor_info.get(v)
                if foreign_corridor_v and not any(p in net_pins for p in foreign_corridor_v):
                    step_cost += PIN_ESCAPE_PENALTY

                new_g = g + step_cost
                n_state = (v, ndx, ndy)
                if n_state not in dist or new_g < dist[n_state]:
                    dist[n_state] = new_g
                    came_from[n_state] = state
                    heapq.heappush(pq, (new_g + h(nx, ny), new_g, nx, ny, ndx, ndy))

        if not target_reached:
            return None

        # Reconstruct path
        best_cost = float('inf')
        curr = None
        for (node, c_dx, c_dy), c in dist.items():
            if node == target_reached and c < best_cost:
                best_cost = c
                curr = (node, c_dx, c_dy)

        path: List[Tuple[float, float]] = []
        while curr:
            path.append(curr[0])
            curr = came_from.get(curr)

        path.reverse()
        return path

    def _merge_path(
        self, nodes: List[Tuple[float, float]], terminal_nodes: Optional[Set[Tuple[float, float]]] = None
    ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
        """Merges contiguous collinear unit segments in the same direction, splitting at pin terminals."""
        if len(nodes) < 2:
            return []
        terminals = terminal_nodes or set()
        segments = []
        start = nodes[0]
        curr_dir = (round(nodes[1][0] - nodes[0][0], 1), round(nodes[1][1] - nodes[0][1], 1))
        prev = nodes[1]

        for nxt in nodes[2:]:
            nxt_dir = (round(nxt[0] - prev[0], 1), round(nxt[1] - prev[1], 1))
            if nxt_dir == curr_dir and prev not in terminals:
                prev = nxt
            else:
                segments.append((start, prev))
                start = prev
                curr_dir = nxt_dir
                prev = nxt

        segments.append((start, prev))
        return segments

    def _assign_net_color(self, net_name: str) -> str:
        nu = net_name.upper()

        # 1. Ground & Return
        if any(k in nu for k in ["GND", "VSS", "NEG", "RETURN"]):
            return "#64748b"

        # 2. Raw / Battery / High-Voltage Power (Vivid Orange)
        if any(k in nu for k in ["BAT", "RAW", "HV", "36V", "48V"]):
            return "#f97316"

        # 3. Regulated DC Logic Power (Crisp Red)
        if any(k in nu for k in ["VCC", "VDD", "VIN", "VOUT", "PWR", "3V3", "5V", "12V", "24V"]):
            return "#ef4444"

        # 4. Analog / Sensor / Differential (Rose / Magenta)
        if any(k in nu for k in ["ANALOG", "ADC", "SENSE", "DIFF"]):
            return "#ec4899"

        # 5. Fast signals & Pulses (Cyan)
        if any(k in nu for k in ["INT", "IRQ", "PWM", "PULSE"]):
            return "#06b6d4"

        # 6. Communication Protocols (I2C, SPI, UART)
        if "SDA" in nu:
            return "#38bdf8"
        if "SCL" in nu:
            return "#10b981"
        if "MOSI" in nu:
            return "#818cf8"
        if "MISO" in nu:
            return "#c084fc"
        if "SCK" in nu or "CLK" in nu:
            return "#10b981"
        if "CS" in nu or "SS" in nu:
            return "#fbbf24"
        if "TX" in nu:
            return "#f59e0b"
        if "RX" in nu:
            return "#2dd4bf"

        # 7. Control & Enables (Violet)
        if any(k in nu for k in ["EN", "ENABLE", "RST", "RESET", "CTRL"]):
            return "#a78bfa"

        # 10. Default General Signal (High-contrast Sky Blue)
        return "#38bdf8"


def route_schematic(schematic: Schematic) -> None:
    router = SchematicRouter(schematic)
    router.route()
