"""Topology driven placement with routing feedback.

The placer treats placement and routing as one optimization problem.  A graph
embedding supplies several legal starting layouts, series parts are inserted
into graph edges, and a discrete coordinate search compares candidates by the
actual routes produced by :mod:`schematic.layout.router`.
"""

from __future__ import annotations

import math
import random
from typing import Dict, Iterable, List, Optional, Set, Tuple

from schematic.models import Component, Net, Point, Schematic, Side, GRID_SIZE, snap_to_grid
from schematic.layout.pin_allocator import allocate_pins_and_dimensions

MARGIN = 100.0
COLUMN_GAP = 240.0
ROW_GAP = 100.0
INLINE_GAP = GRID_SIZE


def _net_is_distribution(net: Net) -> bool:
    """Recognize shared rails, while also treating large fanouts structurally."""
    name = net.name.upper()
    power_tokens = ("GND", "VSS", "NEG", "3V3", "3.3V", "5V", "VCC", "VDD", "VIN", "VBUS", "BAT", "POWER")
    return len(net.connections) >= 5 or (len(net.connections) >= 3 and any(token in name for token in power_tokens))


def _component_connections(schematic: Schematic, comp_id: str) -> List[Tuple[Net, object]]:
    return [
        (net, conn)
        for net in schematic.nets
        for conn in net.connections
        if conn.component_id == comp_id
    ]


def _unique_nets(nets: Iterable[Net]) -> List[Net]:
    result: List[Net] = []
    seen: Set[int] = set()
    for net in nets:
        marker = id(net)
        if marker not in seen:
            seen.add(marker)
            result.append(net)
    return result


def _connected_components(net: Net, excluding: str = "") -> List[str]:
    return list(dict.fromkeys(c.component_id for c in net.connections if c.component_id != excluding))


def _classify_small_parts(schematic: Schematic) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """Find edge-inserted and branch parts from graph degree, not component names."""
    series: Dict[str, dict] = {}
    shunts: Dict[str, dict] = {}
    for comp in schematic.components.values():
        if not comp.is_standard or len(comp.pins) < 2:
            continue
        per_pin: Dict[str, Net] = {}
        for net, conn in _component_connections(schematic, comp.id):
            pin_id = conn.pin_id or conn.pin_name
            per_pin.setdefault(pin_id, net)
        nets = _unique_nets(per_pin.values())
        active_pin_count = len(per_pin)
        if len(nets) != 2 or active_pin_count != 2:
            continue

        # Fuses and switches are graph edges even when one terminal is on a
        # shared rail. Other two-terminal parts on one shared and one signal
        # net are branches (for example a pull-up or filter capacitor).
        has_distribution = any(_net_is_distribution(net) for net in nets)
        edge_part = comp.kind in ("fuse", "switch") or not has_distribution
        a_peers = _connected_components(nets[0], comp.id)
        b_peers = _connected_components(nets[1], comp.id)
        if not a_peers or not b_peers:
            continue
        if edge_part and set(a_peers) != set(b_peers):
            series[comp.id] = {
                "nets": nets,
                "pins": per_pin,
                "peer_sets": (a_peers, b_peers),
            }
        elif not edge_part:
            signal_nets = [net for net in nets if not _net_is_distribution(net)]
            if len(signal_nets) == 1:
                peers = _connected_components(signal_nets[0], comp.id)
                if peers:
                    shunts[comp.id] = {"signal_net": signal_nets[0], "peers": peers}
    return series, shunts


def _choose_hub(comps: List[Component], schematic: Schematic, series: Dict[str, dict], shunts: Dict[str, dict]) -> Component:
    macro_ids = {c.id for c in comps if c.id not in series and c.id not in shunts}
    degree = {cid: 0.0 for cid in macro_ids}
    for net in schematic.nets:
        if _net_is_distribution(net):
            continue
        members = sorted({c.component_id for c in net.connections if c.component_id in macro_ids})
        if len(members) < 2:
            continue
        weight = 1.0 / max(1, len(members) - 1)
        for cid in members:
            degree[cid] += weight * (len(members) - 1)
    for info in series.values():
        a, b = info["peer_sets"]
        for x in a:
            for y in b:
                if x in degree and y in degree:
                    degree[x] += 0.5
                    degree[y] += 0.5
    return max((c for c in comps if c.id in macro_ids), key=lambda c: (len(c.pins) * 2 + degree[c.id]))


def _build_graph(
    schematic: Schematic,
    macro_ids: Set[str],
    series: Dict[str, dict],
) -> Dict[str, Dict[str, float]]:
    graph: Dict[str, Dict[str, float]] = {cid: {} for cid in sorted(macro_ids)}

    def add_edge(a: str, b: str, weight: float) -> None:
        if a == b or a not in graph or b not in graph:
            return
        graph[a][b] = graph[a].get(b, 0.0) + weight
        graph[b][a] = graph[b].get(a, 0.0) + weight

    for net in schematic.nets:
        if _net_is_distribution(net):
            continue
        members = sorted({c.component_id for c in net.connections if c.component_id in macro_ids})
        if len(members) < 2:
            continue
        base = 1.0 / max(1, len(members) - 1)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                add_edge(a, b, base)
    for info in series.values():
        left, right = info["peer_sets"]
        for a in left:
            for b in right:
                add_edge(a, b, 1.2 / max(1, len(left) * len(right)))
    return graph


def _resolve_equivalent_pins(schematic: Schematic) -> None:
    for net in schematic.nets:
        for conn in net.connections:
            comp = schematic.components.get(conn.component_id)
            if not comp:
                continue
            matching = comp.get_pins_by_name(conn.pin_name)
            if len(matching) <= 1:
                if matching:
                    conn.pin_id = matching[0].id
                continue
            peer_points = []
            for peer_id in conn.peer_component_ids:
                peer = schematic.components.get(peer_id)
                if peer and peer.id != comp.id:
                    peer_points.append(peer.center)
            if not peer_points:
                peer_points = [
                    schematic.components[c.component_id].center
                    for c in net.connections
                    if c.component_id != comp.id and c.component_id in schematic.components
                ]
            if peer_points:
                pin = min(matching, key=lambda p: min(p.position.manhattan_to(q) for q in peer_points))
            else:
                pin = matching[0]
            conn.pin_id = pin.id


def _pin_for_connection(schematic: Schematic, conn):
    comp = schematic.components.get(conn.component_id)
    if not comp:
        return None
    return comp.pins.get(conn.pin_id) if conn.pin_id else comp.get_pin(conn.pin_name)


def _direct_hub_targets(schematic: Schematic, hub: Component) -> Dict[str, List[float]]:
    targets: Dict[str, List[float]] = {}
    for net in schematic.nets:
        if _net_is_distribution(net):
            continue
        hub_conns = [c for c in net.connections if c.component_id == hub.id]
        other_ids = {c.component_id for c in net.connections if c.component_id != hub.id}
        for conn in hub_conns:
            pin = _pin_for_connection(schematic, conn)
            if not pin:
                continue
            for cid in other_ids:
                targets.setdefault(cid, []).append(pin.position.y)
    return targets


def _derive_levels_and_sides(
    schematic: Schematic,
    hub: Component,
    graph: Dict[str, Dict[str, float]],
) -> Tuple[Dict[str, int], Dict[str, Side]]:
    levels = {hub.id: 0}
    sides: Dict[str, Side] = {hub.id: Side.LEFT}
    direct_votes: Dict[str, Dict[Side, float]] = {}
    for net in schematic.nets:
        if _net_is_distribution(net):
            continue
        hub_conns = [c for c in net.connections if c.component_id == hub.id]
        peers = [c for c in net.connections if c.component_id != hub.id and c.component_id in graph]
        for hc in hub_conns:
            pin = _pin_for_connection(schematic, hc)
            side = pin.side if pin else Side.LEFT
            for conn in peers:
                direct_votes.setdefault(conn.component_id, {})
                direct_votes[conn.component_id][side] = direct_votes[conn.component_id].get(side, 0.0) + (1.0 / max(1, len(peers)))
    for cid, votes in direct_votes.items():
        sides[cid] = max(votes, key=votes.get)

    # Graph distance gives a topological depth. Ties are resolved by stronger
    # connections, and disconnected islands are attached to the nearest side.
    frontier = [hub.id]
    while frontier:
        u = frontier.pop(0)
        for v in sorted(graph.get(u, {})):
            if v not in levels:
                levels[v] = levels[u] + 1
                frontier.append(v)
    for cid in sorted(graph):
        if cid not in levels:
            levels[cid] = max(levels.values(), default=0) + 1

    pending = [cid for cid in sorted(graph) if cid != hub.id and cid not in sides]
    while pending:
        progressed = False
        for cid in pending[:]:
            neighbor_votes = {Side.LEFT: 0.0, Side.RIGHT: 0.0}
            for peer, weight in sorted(graph[cid].items()):
                if peer in sides:
                    neighbor_votes[sides[peer]] += weight
            if sum(neighbor_votes.values()) > 0:
                sides[cid] = max(neighbor_votes, key=neighbor_votes.get)
                pending.remove(cid)
                progressed = True
        if not progressed:
            cid = pending.pop(0)
            sides[cid] = Side.LEFT if sum(1 for s in sides.values() if s == Side.LEFT) <= sum(1 for s in sides.values() if s == Side.RIGHT) else Side.RIGHT
    return levels, sides


def _initial_layout(
    schematic: Schematic,
    hub: Component,
    graph: Dict[str, Dict[str, float]],
    series: Dict[str, dict],
    seed: int,
) -> None:
    rng = random.Random(seed)
    allocate_pins_and_dimensions(schematic, use_geometry=False)
    hub.x = MARGIN + 1200.0
    hub.y = MARGIN + 80.0
    allocate_pins_and_dimensions(schematic, use_geometry=True)

    levels, sides = _derive_levels_and_sides(schematic, hub, graph)
    hub_targets = _direct_hub_targets(schematic, hub)
    macro_ids = set(graph)
    macro_ids.discard(hub.id)

    # Establish a natural target row from hub pin heights; propagate it over
    # the graph for components that sit behind a pass-through component.
    ideal_y: Dict[str, float] = {}
    for cid in sorted(macro_ids):
        ys = hub_targets.get(cid, [])
        if ys:
            ideal_y[cid] = sum(ys) / len(ys)
    for _ in range(len(macro_ids) + 1):
        changed = False
        for cid in sorted(macro_ids):
            if cid in ideal_y:
                continue
            peers = [(ideal_y[p], w) for p, w in sorted(graph[cid].items()) if p in ideal_y]
            if peers:
                ideal_y[cid] = sum(y * w for y, w in peers) / sum(w for _, w in peers)
                changed = True
        if not changed:
            break
    for cid in sorted(macro_ids):
        ideal_y.setdefault(cid, hub.center.y + (levels[cid] % 3 - 1) * 160.0)

    # Allow two side-by-side lanes per graph depth to avoid a single tall
    # vertical stack while keeping nearby direct peers within reach.
    lane: Dict[str, int] = {}
    for side in (Side.LEFT, Side.RIGHT):
        for depth in sorted({levels[cid] for cid in macro_ids if sides[cid] == side}):
            members = [cid for cid in macro_ids if sides[cid] == side and levels[cid] == depth]
            members.sort(key=lambda cid: (-graph[cid].get(hub.id, 0.0), ideal_y[cid], cid))
            lane_ends: List[float] = []
            for cid in members:
                comp = schematic.components[cid]
                desired_top = ideal_y[cid] - comp.height / 2.0
                available = [i for i, end in enumerate(lane_ends) if desired_top >= end + ROW_GAP]
                if available:
                    chosen = min(available, key=lambda i: abs(lane_ends[i] - desired_top))
                elif len(lane_ends) < 2:
                    chosen = len(lane_ends)
                    lane_ends.append(-1e9)
                else:
                    chosen = min(range(2), key=lambda i: max(lane_ends[i] + ROW_GAP, desired_top) - desired_top)
                lane[cid] = chosen
                lane_ends[chosen] = max(lane_ends[chosen] + ROW_GAP, desired_top) + comp.height

    # Convert graph depth and collision lane into ordered columns on each side.
    slots: Dict[Tuple[Side, int, int], List[str]] = {}
    for cid in sorted(macro_ids):
        side = sides[cid]
        slot = (side, levels[cid], lane[cid])
        slots.setdefault(slot, []).append(cid)
    left_keys = sorted((k for k in slots if k[0] == Side.LEFT), key=lambda k: (k[1], k[2]))
    right_keys = sorted((k for k in slots if k[0] == Side.RIGHT), key=lambda k: (k[1], k[2]))
    left_width = sum(max(schematic.components[cid].width for cid in slots[key]) + COLUMN_GAP for key in left_keys)
    hub.x = snap_to_grid(MARGIN + left_width + 80.0)
    allocate_pins_and_dimensions(schematic, use_geometry=True)

    def set_side_columns(keys: List[Tuple[Side, int, int]], side: Side) -> None:
        cursor = hub.x if side == Side.LEFT else hub.x + hub.width
        for key in keys:
            width = max(schematic.components[cid].width for cid in slots[key])
            if side == Side.LEFT:
                cursor -= COLUMN_GAP + width
                x = cursor
            else:
                x = cursor + COLUMN_GAP
                cursor = x + width
            for cid in sorted(slots[key]):
                comp = schematic.components[cid]
                comp.x = snap_to_grid(max(MARGIN, x + rng.choice((-80.0, -40.0, 0.0, 40.0, 80.0)) if seed else x))

    set_side_columns(left_keys, Side.LEFT)
    set_side_columns(right_keys, Side.RIGHT)

    # Pack each column around its ideal peer height. Search both directions so
    # one collision does not push an entire column arbitrarily far downward.
    column_members: Dict[Tuple[Side, int, int], List[str]] = {}
    for cid in sorted(macro_ids):
        column_members.setdefault((sides[cid], levels[cid], lane[cid]), []).append(cid)
    for key, members in column_members.items():
        members.sort(key=lambda cid: (ideal_y[cid], cid))
        placed: List[Component] = []
        for cid in members:
            comp = schematic.components[cid]
            desired = ideal_y[cid] - comp.height / 2.0
            if seed:
                desired += rng.choice((-160.0, -80.0, 0.0, 80.0, 160.0))
            candidates = [snap_to_grid(max(MARGIN, desired + delta)) for radius in range(0, 2001, 20) for delta in ((0.0,) if radius == 0 else (-radius, radius))]
            valid = [y for y in candidates if all(not _rects_too_close(comp.x, y, comp.width, comp.height, p.x, p.y, p.width, p.height, ROW_GAP) for p in placed)]
            comp.y = min(valid, key=lambda y: (abs(y - desired), y)) if valid else snap_to_grid(max(MARGIN, desired))
            placed.append(comp)

    # Place graph-edge parts directly before the endpoint on the lower-degree
    # side. The part occupies a segment of that edge instead of a spare column.
    _refit_small_parts(schematic, series, schematic.components[hub.id])
    _legalize_overlaps(schematic, movable=macro_ids)
    # Refit edge parts after a macro component may have been shifted.
    _refit_small_parts(schematic, series, schematic.components[hub.id])


def _rects_too_close(ax: float, ay: float, aw: float, ah: float, bx: float, by: float, bw: float, bh: float, gap: float) -> bool:
    return not (ax + aw + gap <= bx or bx + bw + gap <= ax or ay + ah + gap <= by or by + bh + gap <= ay)


def _place_series_part(schematic: Schematic, cid: str, info: dict, hub: Component) -> None:
    comp = schematic.components[cid]
    candidate_targets: List[Tuple[int, Component, Net, object]] = []
    for net in info["nets"]:
        peers = [c for c in net.connections if c.component_id != cid and c.component_id in schematic.components]
        for conn in peers:
            target = schematic.components[conn.component_id]
            # The hub is the preferred end for switches feeding the hub. For
            # other series parts, prefer the net with fewer external endpoints.
            rank = (0 if target.id == hub.id and comp.kind == "switch" else 1, len(peers), target.center.manhattan_to(hub.center))
            candidate_targets.append((rank[0] * 100000 + rank[1] * 1000 + int(rank[2]), target, net, conn))
    if not candidate_targets:
        return
    _, target, target_net, target_conn = min(candidate_targets, key=lambda item: item[0])
    target_pin = _pin_for_connection(schematic, target_conn)
    if target_pin is None:
        return
    if comp.kind == "switch":
        source_net = next((net for net in info["nets"] if net is not target_net), None)
        source_pins = []
        if source_net:
            for conn in source_net.connections:
                if conn.component_id != cid:
                    pin = _pin_for_connection(schematic, conn)
                    if pin:
                        source_pins.append(pin)
        target_part_conn = next((c for c in target_net.connections if c.component_id == cid), None)
        target_part_pin = _pin_for_connection(schematic, target_part_conn) if target_part_conn else None
        if source_pins and target_part_pin:
            source_pin = min(source_pins, key=lambda pin: pin.position.manhattan_to(target_pin.position))
            pin_offset_y = target_part_pin.position.y - comp.y
            comp.x = snap_to_grid((source_pin.position.x + target_pin.position.x - comp.width) / 2.0)
            comp.y = snap_to_grid(target_pin.position.y - pin_offset_y)
            return
    comp.x = snap_to_grid(max(MARGIN, target.x - comp.width - INLINE_GAP))
    comp.y = snap_to_grid(target_pin.position.y - comp.height / 2.0)


def _refit_small_parts(schematic: Schematic, series: Dict[str, dict], hub: Component) -> None:
    """Refresh pin coordinates, then fit inline and branch parts to them."""
    allocate_pins_and_dimensions(schematic, use_geometry=True)
    for cid in series:
        _place_series_part(schematic, cid, series[cid], hub)
    _place_shunt_parts(schematic, series)
    # Standard parts can change pin sides as peers move. One refreshed pass
    # makes their own terminal positions agree with the new macro geometry.
    allocate_pins_and_dimensions(schematic, use_geometry=True)
    for cid in series:
        _place_series_part(schematic, cid, series[cid], hub)
    _place_shunt_parts(schematic, series)
    allocate_pins_and_dimensions(schematic, use_geometry=True)


def _place_shunt_parts(schematic: Schematic, series: Dict[str, dict]) -> None:
    shunts: Dict[str, Tuple[Net, List[str]]] = {}
    for comp in schematic.components.values():
        if not comp.is_standard or len(comp.pins) != 2 or comp.id in series:
            continue
        per_pin = [net for net, _ in _component_connections(schematic, comp.id)]
        nets = _unique_nets(per_pin)
        if len(nets) == 2 and any(_net_is_distribution(n) for n in nets):
            signal = next((n for n in nets if not _net_is_distribution(n)), None)
            if signal:
                peers = _connected_components(signal, comp.id)
                if peers:
                    shunts[comp.id] = (signal, peers)
    groups: Dict[str, List[Tuple[str, List[str]]]] = {}
    for cid, (net, peers) in shunts.items():
        groups.setdefault(net.name, []).append((cid, peers))

    # Branch devices hang off a shared signal corridor; placing them directly
    # on that corridor turns the main data path into a crowded obstacle field.
    # Put siblings together in a clear side lane instead, choosing the nearest
    # lane with enough room from the rest of the drawing.
    for net_name in sorted(groups):
        siblings = sorted(groups[net_name], key=lambda item: item[0])
        sibling_ids = {cid for cid, _ in siblings}
        anchor_ids = sorted({peer for _, peers in siblings for peer in peers if peer not in sibling_ids and peer not in series})
        if not anchor_ids:
            anchor_ids = sorted({peer for _, peers in siblings for peer in peers if peer not in sibling_ids})
        endpoints = [schematic.components[cid] for cid in anchor_ids if cid in schematic.components]
        if not endpoints:
            continue
        net = shunts[siblings[0][0]][0]
        pins = []
        for conn in net.connections:
            if conn.component_id in anchor_ids:
                pin = _pin_for_connection(schematic, conn)
                if pin:
                    pins.append(pin)
        center_x = sum(pin.position.x for pin in pins) / len(pins) if pins else sum(c.center.x for c in endpoints) / len(endpoints)
        center_y = sum(pin.position.y for pin in pins) / len(pins) if pins else sum(c.center.y for c in endpoints) / len(endpoints)
        span_x = max(c.center.x for c in endpoints) - min(c.center.x for c in endpoints)
        span_y = max(c.center.y for c in endpoints) - min(c.center.y for c in endpoints)
        horizontal = span_x >= span_y

        best_layout = None
        best_score = math.inf
        direct_links = _direct_link_counts(schematic)
        for lane in (-480.0, -320.0, -240.0, -160.0, 160.0, 240.0, 320.0, 480.0):
            placements = {}
            score = abs(lane) * 0.02
            invalid_lane = False
            for index, (cid, _) in enumerate(siblings):
                comp = schematic.components[cid]
                along = (index - (len(siblings) - 1) / 2.0) * 160.0
                if horizontal:
                    x = snap_to_grid(center_x - comp.width / 2.0 + along)
                    y = snap_to_grid(center_y - comp.height / 2.0 + lane)
                else:
                    x = snap_to_grid(center_x - comp.width / 2.0 + lane)
                    y = snap_to_grid(center_y - comp.height / 2.0 + along)
                if x < MARGIN or y < MARGIN:
                    invalid_lane = True
                    break
                placements[cid] = (x, y)
                for other in schematic.components.values():
                    if other.id in sibling_ids:
                        continue
                    probe = Component(id=comp.id, x=x, y=y, width=comp.width, height=comp.height)
                    if probe.rect.intersects(other.rect):
                        score += 100000.0
                    direct_count = direct_links.get(frozenset((comp.id, other.id)), 0)
                    preferred = 80.0 if direct_count == 1 else (280.0 if not comp.is_standard and not other.is_standard else 80.0)
                    deficit = max(0.0, preferred - _component_clearance(probe, other))
                    score += (0.10 if direct_count == 1 else 0.35) * deficit * deficit
            if invalid_lane:
                continue
            if score < best_score:
                best_score = score
                best_layout = placements
        if best_layout:
            for cid, (x, y) in best_layout.items():
                schematic.components[cid].x = x
                schematic.components[cid].y = y


def _legalize_overlaps(schematic: Schematic, movable: Set[str]) -> None:
    fixed = [c for c in schematic.components.values() if c.id not in movable and c.is_standard]
    ordered = [c for c in schematic.components.values() if c.id in movable]
    placed = fixed[:]
    for comp in ordered:
        if not any(comp.rect.intersects(other.rect) for other in placed):
            placed.append(comp)
            continue
        original = comp.y
        candidates = [snap_to_grid(original + d) for r in range(20, 1001, 20) for d in (-r, r)]
        valid = [y for y in candidates if y >= MARGIN and all(not comp.rect.__class__(comp.x, y, comp.width, comp.height).intersects(p.rect) for p in placed)]
        if valid:
            comp.y = min(valid, key=lambda y: (abs(y - original), y))
        placed.append(comp)


def _snapshot(schematic: Schematic) -> Dict[str, Tuple[float, float]]:
    return {cid: (c.x, c.y) for cid, c in schematic.components.items()}


def _restore(schematic: Schematic, positions: Dict[str, Tuple[float, float]]) -> None:
    for cid, (x, y) in positions.items():
        schematic.components[cid].x = x
        schematic.components[cid].y = y


def _component_clearance(a: Component, b: Component) -> float:
    """Shortest distance between component rectangles (zero when touching)."""
    dx = max(a.x - (b.x + b.width), b.x - (a.x + a.width), 0.0)
    dy = max(a.y - (b.y + b.height), b.y - (a.y + a.height), 0.0)
    return math.hypot(dx, dy)


def _series_neighbor_pairs(series: Dict[str, dict]) -> Set[frozenset[str]]:
    """Pairs intentionally kept close because a small part occupies their link."""
    pairs: Set[frozenset[str]] = set()
    for cid, info in series.items():
        for net in info["nets"]:
            for conn in net.connections:
                if conn.component_id != cid:
                    pairs.add(frozenset((cid, conn.component_id)))
    return pairs


def _direct_link_counts(schematic: Schematic) -> Dict[frozenset[str], int]:
    """Count dedicated two-endpoint links between component pairs."""
    counts: Dict[frozenset[str], int] = {}
    for net in schematic.nets:
        if len(net.connections) != 2 or _net_is_distribution(net):
            continue
        a, b = (conn.component_id for conn in net.connections)
        if a != b:
            pair = frozenset((a, b))
            counts[pair] = counts.get(pair, 0) + 1
    return counts


def _clearance_penalty(schematic: Schematic, series: Dict[str, dict]) -> float:
    close_link_pairs = _series_neighbor_pairs(series)
    direct_links = _direct_link_counts(schematic)
    comps = sorted(schematic.components.values(), key=lambda comp: comp.id)
    penalty = 0.0
    for i, a in enumerate(comps):
        for b in comps[i + 1 :]:
            if frozenset((a.id, b.id)) in close_link_pairs:
                continue
            clearance = _component_clearance(a, b)
            direct_count = direct_links.get(frozenset((a.id, b.id)), 0)
            preferred = 80.0 if direct_count == 1 else (280.0 if not a.is_standard and not b.is_standard else 80.0)
            deficit = max(0.0, preferred - clearance)
            penalty += (0.10 if direct_count == 1 else 0.35) * deficit * deficit
    return penalty


def _local_clearance_penalty(schematic: Schematic, cid: str, series: Dict[str, dict]) -> float:
    comp = schematic.components[cid]
    close_link_pairs = _series_neighbor_pairs(series)
    direct_links = _direct_link_counts(schematic)
    penalty = 0.0
    for other in schematic.components.values():
        if other.id == cid or frozenset((cid, other.id)) in close_link_pairs:
            continue
        direct_count = direct_links.get(frozenset((cid, other.id)), 0)
        preferred = 80.0 if direct_count == 1 else (280.0 if not comp.is_standard and not other.is_standard else 80.0)
        deficit = max(0.0, preferred - _component_clearance(comp, other))
        penalty += (0.10 if direct_count == 1 else 0.35) * deficit * deficit
    return penalty


def _crossing_count(schematic: Schematic) -> int:
    """Count distinct orthogonal crossings between different routed nets."""
    horizontal = []
    vertical = []
    for wire in schematic.wires:
        if wire.start == wire.end:
            continue
        if wire.is_horizontal:
            horizontal.append(wire)
        elif wire.is_vertical:
            vertical.append(wire)
    crossings: Set[Tuple[str, str, float, float]] = set()
    for h in horizontal:
        hy = h.start.y
        h_min, h_max = sorted((h.start.x, h.end.x))
        for v in vertical:
            if h.net_name == v.net_name:
                continue
            vx = v.start.x
            v_min, v_max = sorted((v.start.y, v.end.y))
            if h_min <= vx <= h_max and v_min <= hy <= v_max:
                net_pair = tuple(sorted((h.net_name, v.net_name)))
                crossings.add((net_pair[0], net_pair[1], vx, hy))
    return len(crossings)


def _route_metrics(schematic: Schematic, series: Dict[str, dict] | None = None) -> Tuple[float, float, float]:
    from schematic.layout.router import route_schematic

    allocate_pins_and_dimensions(schematic, use_geometry=True)
    _resolve_equivalent_pins(schematic)
    route_schematic(schematic)
    wire_length = sum(w.start.manhattan_to(w.end) for w in schematic.wires)
    net_graph: Dict[str, Dict[Tuple[float, float], Set[Tuple[float, float]]]] = {}
    for wire in schematic.wires:
        a = wire.start.as_tuple()
        b = wire.end.as_tuple()
        adj = net_graph.setdefault(wire.net_name, {})
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    endpoint_groups: Dict[Tuple[str, ...], List[str]] = {}
    for net in schematic.nets:
        if _net_is_distribution(net):
            continue
        endpoint_ids = tuple(sorted({c.component_id for c in net.connections}))
        if len(endpoint_ids) >= 2:
            endpoint_groups.setdefault(endpoint_ids, []).append(net.name)
    bundled_nets = {name for names in endpoint_groups.values() if len(names) >= 2 for name in names}
    bends = 0
    bundled_bends = 0
    for net_name, adj in net_graph.items():
        for node, neighbors in adj.items():
            if len(neighbors) != 2:
                continue
            a, b = list(neighbors)
            if (a[0] == node[0]) != (b[0] == node[0]):
                bends += 1
                if net_name in bundled_nets:
                    bundled_bends += 1
    min_x = min(c.x for c in schematic.components.values())
    min_y = min(c.y for c in schematic.components.values())
    max_x = max(c.x + c.width for c in schematic.components.values())
    max_y = max(c.y + c.height for c in schematic.components.values())
    spread = (max_x - min_x) + (max_y - min_y)
    overlap_penalty = 0.0
    comps = list(schematic.components.values())
    for i, a in enumerate(comps):
        for b in comps[i + 1 :]:
            if a.rect.intersects(b.rect):
                overlap_penalty += 100000.0
    crossings = _crossing_count(schematic)
    clearances = _clearance_penalty(schematic, series or {})
    # Readability outranks compactness: true crossings and crowded macro
    # placements cost enough that a longer open route is usually preferable.
    score = (
        wire_length
        + 90.0 * bends
        + 350.0 * bundled_bends
        + 0.02 * spread
        + 1200.0 * crossings
        + clearances
        + overlap_penalty
    )
    routed_names = {w.net_name for w in schematic.wires}
    for net in schematic.nets:
        if len(net.connections) < 2:
            continue
        if net.name not in routed_names:
            score += 20000.0
            continue
        wire_nodes = {
            point
            for wire in schematic.wires
            if wire.net_name == net.name
            for point in (wire.start.as_tuple(), wire.end.as_tuple())
        }
        for conn in net.connections:
            pin = _pin_for_connection(schematic, conn)
            if pin and pin.position.as_tuple() not in wire_nodes:
                score += 5000.0
    return score, wire_length, float(bends)


def _proxy_score(schematic: Schematic, series: Dict[str, dict] | None = None) -> float:
    """Cheap Manhattan/MST estimate used to rank moves before routing them."""
    allocate_pins_and_dimensions(schematic, use_geometry=True)
    total = 0.0
    for net in schematic.nets:
        points = []
        for conn in net.connections:
            pin = _pin_for_connection(schematic, conn)
            if pin:
                xy = pin.position.as_tuple()
                if xy not in points:
                    points.append(xy)
        if len(points) < 2:
            continue
        # Manhattan minimum spanning tree is a lower bound on routed wire.
        connected = [points.pop(0)]
        while points:
            best = min(
                ((abs(a[0] - b[0]) + abs(a[1] - b[1]), i, a) for a in connected for i, b in enumerate(points)),
                key=lambda x: (x[0], x[1], x[2]),
            )
            distance, index, source = best
            endpoint = points[index]
            total += distance
            # A non-aligned pair needs at least one orthogonal turn. This
            # keeps exact pin-row alignment competitive with small changes in
            # shared-rail length during candidate ranking.
            if source[0] != endpoint[0] and source[1] != endpoint[1]:
                total += 75.0
            connected.append(points.pop(index))
    comp_list = list(schematic.components.values())
    for i, a in enumerate(comp_list):
        for b in comp_list[i + 1 :]:
            if a.rect.intersects(b.rect):
                total += 10000.0
    return total + _clearance_penalty(schematic, series or {})


def _peer_alignment_offsets(schematic: Schematic, comp: Component) -> Set[Tuple[float, float]]:
    offsets: Set[Tuple[float, float]] = set()
    for net, conn in _component_connections(schematic, comp.id):
        pin = _pin_for_connection(schematic, conn)
        if not pin:
            continue
        for peer_conn in net.connections:
            if peer_conn.component_id == comp.id:
                continue
            peer_pin = _pin_for_connection(schematic, peer_conn)
            if not peer_pin:
                continue
            dy = snap_to_grid(peer_pin.position.y - pin.position.y)
            dx = snap_to_grid(peer_pin.position.x - pin.position.x)
            if 0 < abs(dy) <= 640.0:
                offsets.add((0.0, dy))
            if 0 < abs(dx) <= 960.0:
                offsets.add((dx, 0.0))
    return offsets


def _direct_pair_alignment_offsets(schematic: Schematic, comp: Component) -> Set[Tuple[float, float]]:
    """Moves that align a pin directly with its sole peer on a two-node net."""
    offsets: Set[Tuple[float, float]] = set()
    for net, conn in _component_connections(schematic, comp.id):
        if len(net.connections) != 2:
            continue
        pin = _pin_for_connection(schematic, conn)
        peer_conn = next((c for c in net.connections if c.component_id != comp.id), None)
        peer_pin = _pin_for_connection(schematic, peer_conn) if peer_conn else None
        if not pin or not peer_pin:
            continue
        dy = snap_to_grid(peer_pin.position.y - pin.position.y)
        dx = snap_to_grid(peer_pin.position.x - pin.position.x)
        peer_comp = schematic.components[peer_conn.component_id]
        horizontal_pair = abs(peer_comp.center.x - comp.center.x) >= abs(peer_comp.center.y - comp.center.y)
        if horizontal_pair and 0 < abs(dy) <= 960.0:
            offsets.add((0.0, dy))
        elif not horizontal_pair and 0 < abs(dx) <= 1200.0:
            offsets.add((dx, 0.0))
    return offsets


def _candidate_offsets(schematic: Schematic, comp: Component) -> List[Tuple[float, float]]:
    offsets: Set[Tuple[float, float]] = set()
    for delta in (-1280.0, -640.0, -320.0, -160.0, -80.0, -20.0, 20.0, 80.0, 160.0, 320.0, 640.0, 1280.0):
        offsets.add((0.0, delta))
        offsets.add((delta, 0.0))
    offsets.update(_peer_alignment_offsets(schematic, comp))
    offsets.update(_gap_placement_offsets(schematic, comp))
    return sorted(offsets, key=lambda d: (abs(d[0]) + abs(d[1]), d[1], d[0]))


def _gap_placement_offsets(schematic: Schematic, comp: Component) -> Set[Tuple[float, float]]:
    """Moves a component into a free gap between two of its connected peers."""
    peer_ids = {
        peer_conn.component_id
        for net, _ in _component_connections(schematic, comp.id)
        if not _net_is_distribution(net)
        for peer_conn in net.connections
        if peer_conn.component_id != comp.id and peer_conn.component_id in schematic.components
    }
    peers = sorted(
        (schematic.components[cid] for cid in peer_ids),
        key=lambda peer: (comp.center.manhattan_to(peer.center), peer.id),
    )[:8]
    offsets: Set[Tuple[float, float]] = set()
    for i, first in enumerate(peers):
        for second in peers[i + 1 :]:
            left, right = sorted((first, second), key=lambda item: (item.x, item.id))
            horizontal_gap = right.x - (left.x + left.width)
            already_in_horizontal_gap = left.x + left.width <= comp.x and comp.x + comp.width <= right.x
            if horizontal_gap >= comp.width + 40.0 and not already_in_horizontal_gap:
                center_x = (left.x + left.width + right.x - comp.width) / 2.0
                x_targets = {
                    snap_to_grid(left.x + left.width + 40.0),
                    snap_to_grid(center_x - GRID_SIZE / 2.0),
                    snap_to_grid(center_x + GRID_SIZE / 2.0),
                    snap_to_grid(center_x - GRID_SIZE),
                    snap_to_grid(center_x + GRID_SIZE),
                    snap_to_grid(right.x - comp.width - 40.0),
                }
                y_targets = {
                    comp.y,
                    snap_to_grid(left.y + left.height + 20.0),
                    snap_to_grid(right.y + right.height + 20.0),
                    snap_to_grid((first.center.y + second.center.y) / 2.0 - comp.height / 2.0),
                }
                for x in x_targets:
                    for y in y_targets:
                        if x >= MARGIN and y >= MARGIN and abs(x - comp.x) + abs(y - comp.y) <= 1600.0:
                            offsets.add((x - comp.x, y - comp.y))

            top, bottom = sorted((first, second), key=lambda item: (item.y, item.id))
            vertical_gap = bottom.y - (top.y + top.height)
            already_in_vertical_gap = top.y + top.height <= comp.y and comp.y + comp.height <= bottom.y
            if vertical_gap >= comp.height + 40.0 and not already_in_vertical_gap:
                y_targets = {
                    snap_to_grid(top.y + top.height + 40.0),
                    snap_to_grid((top.y + top.height + bottom.y - comp.height) / 2.0),
                    snap_to_grid(bottom.y - comp.height - 40.0),
                }
                x_targets = {
                    comp.x,
                    snap_to_grid(top.x + top.width + 20.0),
                    snap_to_grid(bottom.x + bottom.width + 20.0),
                    snap_to_grid((first.center.x + second.center.x) / 2.0 - comp.width / 2.0),
                }
                for x in x_targets:
                    for y in y_targets:
                        if x >= MARGIN and y >= MARGIN and abs(x - comp.x) + abs(y - comp.y) <= 1600.0:
                            offsets.add((x - comp.x, y - comp.y))
    return offsets


def _optimize_with_routing(
    schematic: Schematic,
    movable: Set[str],
    series: Dict[str, dict],
    hub: Component,
    passes: int = 2,
    route_candidates: int = 2,
    reverse_order: bool = False,
) -> float:
    current = _snapshot(schematic)
    current_score, _, _ = _route_metrics(schematic, series)
    ordered_ids = sorted(movable, key=lambda cid: (len(schematic.components[cid].pins), cid))

    # Multi-neighborhood coordinate descent. The MST estimate narrows the
    # choices, but every component also gets a clearance-driven move and a
    # topology-alignment move evaluated against a complete reroute. This lets
    # a placement trade a little wire length for fewer crossings and more air.
    for _pass in range(passes):
        changed = False
        visit_order = list(reversed(ordered_ids)) if reverse_order or _pass % 2 else ordered_ids
        for cid in visit_order:
            comp = schematic.components[cid]
            origin = _snapshot(schematic)
            offsets = _candidate_offsets(schematic, comp)
            direct_alignment_offsets = _direct_pair_alignment_offsets(schematic, comp)
            gap_offsets = _gap_placement_offsets(schematic, comp)
            origin_proxy = _proxy_score(schematic, series)
            ranked: List[Tuple[float, Tuple[float, float], Dict[str, Tuple[float, float]], float]] = []
            for dx, dy in offsets:
                _restore(schematic, origin)
                comp = schematic.components[cid]
                comp.x = snap_to_grid(max(MARGIN, comp.x + dx))
                comp.y = snap_to_grid(max(MARGIN, comp.y + dy))
                _refit_small_parts(schematic, series, hub)
                positions = _snapshot(schematic)
                if positions == origin:
                    continue
                candidate_proxy = _proxy_score(schematic, series)
                local_clearance = _local_clearance_penalty(schematic, cid, series)
                ranked.append((candidate_proxy, (dx, dy), positions, local_clearance))
            _restore(schematic, origin)
            ranked.sort(key=lambda item: (item[0], abs(item[1][0]) + abs(item[1][1]), item[1]))
            shortlist = []

            def include(item) -> None:
                if item is not None and all(item[2] != prior[2] for prior in shortlist):
                    shortlist.append(item)

            if ranked:
                include(ranked[0])
            gap_moves = [
                item for item in ranked
                if item[1] in gap_offsets
                and item[1][0] != 0.0
                and item[1][1] != 0.0
                and item[0] <= origin_proxy + 2500.0
            ]
            if gap_moves:
                include(min(gap_moves, key=lambda item: (item[0], abs(item[1][0]) + abs(item[1][1]), item[1])))
            direct_aligned = [item for item in ranked if item[1] in direct_alignment_offsets]
            if direct_aligned:
                include(direct_aligned[0])
            if ranked:
                current_clearance = _local_clearance_penalty(schematic, cid, series)
                clearance_moves = [item for item in ranked if item[3] + 1.0 < current_clearance]
                if clearance_moves and (item := min(clearance_moves, key=lambda item: (item[3], item[0], item[1])))[3] + 1000.0 < current_clearance:
                    include(item)
                else:
                    # One long deterministic probe can escape a local minimum
                    # where wire distance alone keeps a useful move hidden.
                    long_moves = [item for item in ranked if abs(item[1][0]) + abs(item[1][1]) >= 320.0]
                    if long_moves and len(shortlist) < 2:
                        include(long_moves[0])
            shortlist = shortlist[:route_candidates]
            best_layout = None
            best_score = current_score
            for _, _, positions, _ in shortlist:
                _restore(schematic, positions)
                _refit_small_parts(schematic, series, hub)
                score, _, _ = _route_metrics(schematic, series)
                if score + 1e-6 < best_score:
                    best_score = score
                    best_layout = _snapshot(schematic)
            if best_layout is not None:
                current = best_layout
                current_score = best_score
                changed = True
            _restore(schematic, current)
        if not changed:
            break
    _restore(schematic, current)
    _refit_small_parts(schematic, series, hub)
    allocate_pins_and_dimensions(schematic, use_geometry=True)
    _resolve_equivalent_pins(schematic)
    from schematic.layout.router import route_schematic
    route_schematic(schematic)
    return current_score


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

        series, shunts = _classify_small_parts(self.schematic)
        hub = _choose_hub(comps, self.schematic, series, shunts)
        macro_ids = {c.id for c in comps if c.id not in series and c.id not in shunts}
        if hub.id not in macro_ids:
            macro_ids.add(hub.id)
        graph = _build_graph(self.schematic, macro_ids, series)

        # Optimize every deterministic start independently. Keeping only the
        # best raw start and refining it once was a major source of local minima.
        best_snapshot = None
        best_score = math.inf
        seed_count = 3 if len(macro_ids) > 3 else 1
        for seed in range(seed_count):
            _initial_layout(self.schematic, hub, graph, series, seed)
            score = _optimize_with_routing(
                self.schematic, macro_ids - {hub.id}, series, hub, passes=1, route_candidates=1
            )
            if score < best_score:
                best_score = score
                best_snapshot = _snapshot(self.schematic)

        if best_snapshot:
            _restore(self.schematic, best_snapshot)
            _refit_small_parts(self.schematic, series, hub)
            _optimize_with_routing(
                self.schematic,
                macro_ids - {hub.id},
                series,
                hub,
                passes=1,
                route_candidates=2,
                reverse_order=True,
            )
            allocate_pins_and_dimensions(self.schematic, use_geometry=True)
            _resolve_equivalent_pins(self.schematic)
            from schematic.layout.router import route_schematic
            route_schematic(self.schematic)
        self._finalize_bounds()

    def _finalize_bounds(self) -> None:
        comps = list(self.schematic.components.values())
        if not comps:
            return
        self.schematic.width = max(max(c.x + c.width for c in comps) + MARGIN, 850.0)
        self.schematic.height = max(max(c.y + c.height for c in comps) + MARGIN, 600.0)


def place_schematic(schematic: Schematic) -> None:
    SchematicPlacer(schematic).place()
