"""Structure of a circuit: which nets become symbols, which parts form a
functional block.

* **Symbol nets.**  A ground net with three or more terminals and a supply
  net with many (``supply_symbol_min``) are not drawn as wires that have to
  reach every corner of the drawing.  Each of its pins gets a short stub with a tag
  carrying the net name, the convention of every schematic editor.  Which
  nets are supplies is the one thing taken from names (``colors.supply_kind``):
  structure cannot tell a supply from a bus, both join many parts.  A
  supply says nothing about which parts belong together, so it takes no
  part in grouping.  A positive supply with few terminals stays a wire:
  it is usually the power path itself, worth seeing.
* **Islands**: parts joined by wires (signal nets), directly or not.  Each
  island is laid out on its own.
* **Hub**: a part wired much more strongly than any other (the
  microcontroller of a typical project) is a block of its own; the
  functional groups around it are found without it.
* **Blocks**: groups of parts wired more tightly to each other than to the
  rest -- the communities of the weighted part graph that maximise
  modularity (Newman & Girvan 2004; greedy agglomeration as in Clauset,
  Newman & Moore 2004, followed by single-part moves).  A net with k
  terminals adds 1 / (k - 1) to every pair of its parts, so two parts
  sharing a four-wire bus are bound much more tightly than two parts on a
  common three-terminal net.  Small groups are not split further.

Grouping uses the wiring only, never names.  A block is placed as a unit
and kept apart from the other blocks, so a reader sees a MOSFET
driver, the SPI peripheral or the battery path as a group at a glance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from schematic_txt.colors import supply_kind
from schematic_txt.layout.netlist import Netlist


@dataclass
class Structure:
    symbol_nets: set[int] = field(default_factory=set)
    islands: list[list[str]] = field(default_factory=list)  # parts per island, input order
    blocks: list[list[list[str]]] = field(default_factory=list)  # per island: parts per block
    loose: list[str] = field(default_factory=list)  # parts without any connection


def signal_weights(parts: list[str], nl: Netlist, skip: set[int]) -> dict[str, dict[str, float]]:
    """Connection strength between parts over the nets not in ``skip``
    (clique model: a k-terminal net adds 1 / (k - 1) per pair)."""
    w: dict[str, dict[str, float]] = {p: {} for p in parts}
    for info in nl.nets:
        if info.index in skip or len(info.terminals) < 2:
            continue
        s = 1.0 / (len(info.terminals) - 1)
        ps = [p for p in info.parts if p in w]
        for a in ps:
            for b in ps:
                if a != b:
                    w[a][b] = w[a].get(b, 0.0) + s
    return w


def _components(parts: list[str], w: dict[str, dict[str, float]]) -> list[list[str]]:
    idx = {p: i for i, p in enumerate(parts)}
    seen: set[str] = set()
    out: list[list[str]] = []
    for start in parts:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            c = stack.pop()
            comp.append(c)
            for d in w[c]:
                if d not in seen:
                    seen.add(d)
                    stack.append(d)
        out.append(sorted(comp, key=idx.__getitem__))
    return out


def communities(parts: list[str], w: dict[str, dict[str, float]], resolution: float = 1.0) -> list[list[str]]:
    """Modularity communities of a connected weighted graph, deterministic
    (ties by input order).  Returns the groups in input order of their
    first member."""
    n = len(parts)
    if n <= 2:
        return [list(parts)]
    idx = {p: i for i, p in enumerate(parts)}
    deg = [sum(w[p].values()) for p in parts]
    m = sum(deg) / 2.0
    if m <= 0:
        return [[p] for p in parts]
    # community state
    comm = list(range(n))  # part -> community id
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    tot = {i: deg[i] for i in range(n)}
    between: dict[int, dict[int, float]] = {i: {} for i in range(n)}
    for p in parts:
        i = idx[p]
        for q, x in w[p].items():
            between[i][idx[q]] = between[i].get(idx[q], 0.0) + x

    def gain(a: int, b: int) -> float:
        return between[a].get(b, 0.0) / m - resolution * tot[a] * tot[b] / (2.0 * m * m)

    # 1. greedy agglomeration
    while True:
        best = None
        for a in sorted(members):
            for b in sorted(between[a]):
                if b <= a:
                    continue
                g = gain(a, b)
                if g > 1e-12 and (best is None or g > best[0] + 1e-12):
                    best = (g, a, b)
        if best is None:
            break
        _, a, b = best
        for i in members[b]:
            comm[i] = a
        members[a] += members.pop(b)
        tot[a] += tot.pop(b)
        for c, x in between.pop(b).items():
            if c == a:
                continue
            between[a][c] = between[a].get(c, 0.0) + x
            between[c].pop(b, None)
            between[c][a] = between[c].get(a, 0.0) + x
        between[a].pop(b, None)

    # 2. single-part moves while modularity improves (Kernighan-Lin style
    #    refinement of the greedy result)
    def link(i: int, c: int) -> float:
        p = parts[i]
        return sum(x for q, x in w[p].items() if comm[idx[q]] == c)

    for _ in range(4):
        moved = False
        for i in range(n):
            c0 = comm[i]
            if len(members[c0]) == 1:
                continue
            k = deg[i]
            stay = link(i, c0) / m - resolution * k * (tot[c0] - k) / (2.0 * m * m)
            best_c, best_g = c0, stay
            for c in sorted({comm[idx[q]] for q in w[parts[i]]}):
                if c == c0:
                    continue
                g = link(i, c) / m - resolution * k * tot[c] / (2.0 * m * m)
                if g > best_g + 1e-12:
                    best_c, best_g = c, g
            if best_c != c0:
                members[c0].remove(i)
                members[best_c].append(i)
                tot[c0] -= k
                tot[best_c] += k
                comm[i] = best_c
                moved = True
        if not moved:
            break

    groups = [sorted(ms) for ms in members.values() if ms]
    groups.sort(key=lambda g: g[0])
    return [[parts[i] for i in g] for g in groups]


def _split_disconnected(group: list[str], w: dict[str, dict[str, float]]) -> list[list[str]]:
    """A community must be connected inside itself (single-part moves can
    leave a member that is only linked through other communities)."""
    sub = {p: {q: x for q, x in w[p].items() if q in group} for p in group}
    return _components(group, sub)


def find_hub(island: list[str], w: dict[str, dict[str, float]], min_degree: int = 4) -> str | None:
    """The part wired to at least ``min_degree`` others whose connection
    strength (sum over its partners) is 1.5 times that of any other part,
    if there is one."""
    if len(island) < min_degree + 1:
        return None
    strength = {p: sum(w[p].values()) for p in island}
    ranked = sorted(island, key=lambda p: (-strength[p], -len(w[p]), island.index(p)))
    first, second = ranked[0], ranked[1]
    if len(w[first]) >= min_degree and strength[first] >= 1.5 * strength[second]:
        return first
    return None


def _group(parts: list[str], w: dict[str, dict[str, float]], min_split: int) -> list[list[str]]:
    """Blocks of a connected part set: one block if it is small, else its
    modularity communities."""
    if len(parts) < min_split:
        return [list(parts)]
    out: list[list[str]] = []
    for c in communities(parts, w):
        out += _split_disconnected(c, w)
    return out


def _blocks(
    parts: list[str], nl: Netlist, skip: set[int], min_split: int
) -> tuple[list[list[str]], list[list[list[str]]]]:
    """Islands and their blocks for the wiring without the nets in ``skip``."""
    w = signal_weights(parts, nl, skip)
    wired = [p for p in parts if nl.part_nets.get(p)]
    sub = {p: w[p] for p in wired}
    islands: list[list[str]] = []
    all_blocks: list[list[list[str]]] = []
    for island in _components(wired, sub):
        islands.append(island)
        blocks: list[list[str]] = []
        hub = find_hub(island, sub)
        if hub is None:
            blocks = _group(island, sub, min_split)
        else:
            blocks.append([hub])
            rest = [p for p in island if p != hub]
            rw = {p: {q: x for q, x in sub[p].items() if q != hub} for p in rest}
            for comp in _components(rest, rw):
                blocks += _group(comp, rw, min_split)
        order = {p: i for i, p in enumerate(island)}
        blocks.sort(key=lambda b: order[b[0]])
        all_blocks.append(blocks)
    return islands, all_blocks


def analyse(
    parts: list[str],
    nl: Netlist,
    symbols: bool = True,
    ground_symbol_min: int = 3,
    supply_symbol_min: int = 5,
    min_split: int = 6,
) -> Structure:
    """Tags: ground from ``ground_symbol_min`` terminals, positive supplies
    from ``supply_symbol_min``, and a positive supply with one terminal less
    when it reaches three or more blocks (it would cross the drawing; a
    local supply such as a converter output stays a wire)."""
    st = Structure()
    st.loose = [p for p in parts if not nl.part_nets.get(p)]
    if symbols:
        for info in nl.nets:
            kind = supply_kind(info.name)
            k = len(info.terminals)
            if (kind == "ground" and k >= ground_symbol_min) or (
                kind == "positive" and k >= supply_symbol_min
            ):
                st.symbol_nets.add(info.index)
    st.islands, st.blocks = _blocks(parts, nl, st.symbol_nets, min_split)
    if symbols:
        block_of = {p: i for bs in st.blocks for i, b in enumerate(bs) for p in b}
        island_of = {p: i for i, isl in enumerate(st.islands) for p in isl}
        spread = set()
        for info in nl.nets:
            if info.index in st.symbol_nets or supply_kind(info.name) != "positive":
                continue
            if len(info.terminals) < supply_symbol_min - 1:
                continue
            reached = {(island_of.get(p), block_of.get(p)) for p in info.parts}
            if len(reached) >= 3:
                spread.add(info.index)
        if spread:
            st.symbol_nets |= spread
            st.islands, st.blocks = _blocks(parts, nl, st.symbol_nets, min_split)
    return st
