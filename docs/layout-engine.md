# Layout engine: design, algorithms and tuning

This document describes how Schematic.txt turns a netlist into a drawing:
where components go, how wires are routed, how the result is scored, and how
to change any of it.  It is written for contributors.

The guiding principle: **the output is documentation for humans**.  The
engine optimises for the clearest drawing, not the smallest one.  When goals
conflict, the priority order is

1. correct connectivity (hard, never traded)
2. human readability
3. clear grouping and signal relationships
4. few crossings
5. few bends
6. whitespace and consistent spacing
7. stable, predictable layout
8. reasonably short wires
9. compactness

---

## 1. Results at a glance

All 17 inputs (the 4 examples plus 13 layout test cases in
`tests/layout_cases/`) were laid out by the previous engine (commit
`558353d`) and the new one, and both outputs were scored with the same
metrics (section 6).  Full side-by-side drawings and per-case numbers:
[`docs/comparison/index.md`](comparison/index.md).

| Metric (sum over 17 inputs) | Before | After |
|---|---:|---:|
| Mean readability score (0–100) | 34.9 | 60.9 |
| Hard violations (unconnected pins, ambiguous touches, …) | 34 | **0** |
| Wire crossings | 229 | 86 |
| Bends | 359 | 265 |
| 20 px zig-zag jogs | 33 | 13 |
| Near-crossings (wire ends 20 px from a foreign wire) | 121 | 66 |
| Unrelated wires running 20 px apart | 73 | 28 |
| Total wire length | 81 240 px | 71 900 px |
| Total layout time | 6.0 s | 16.0 s |

16 of 17 inputs score higher.  The exception, `02_branching` (75.7 → 72.5),
is a case where the score disagrees with the eye: the new drawing has its
pins facing their sources and no wrap-around wires, at the price of one
extra crossing (see section 11).

The old engine left pins **unconnected** in 4 of the 17 inputs (including the
large example, where `SDCard.3V3` had no wire at all) and produced
ambiguous corner contacts in one more.  The new engine treats connectivity as
a hard constraint and verifies it on every run.

Before / after, large example:

| Before (`558353d`) | After |
|---|---|
| ![before](comparison/example_large.before.svg) | ![after](comparison/example_large.after.svg) |

---

## 2. The original architecture (as found)

```
text ──parse──▶ Schematic ──place_schematic──▶ positions ──route_schematic──▶ wires ──render──▶ SVG
```

* **Data model** (`schematic/models.py`): `Component` (box or standard
  symbol) with `Pin`s (side, order, terminal position), `Net`s with
  `NetConnection`s, routed `WireSegment`s and `WireJunction`s.  Everything
  sits on a 20 px grid.
* **Parser** (`schematic/parser.py`): line DSL and YAML, unchanged by this
  work except two fixes (section 12).
* **Placement** (old `placer.py`): pick one *hub* (most pins), assign every
  other part to the hub's left or right by votes from the hub's pin sides,
  split the left side into hard-coded "bands" by **component name**
  (`MOTOR`, `DISPLAY`, `BATTERY`, `SHUNT`, `DCDC`, … — plus a special case
  for parts literally called `Shunt` and `Battery`), stack parts in columns,
  and remove overlaps with a single pairwise pass.  Placement never looked
  at routing.
* **Pin allocation** (old `pin_allocator.py`): free pins face the centroid
  of connected components; pin order by peer y.
* **Routing** (old `router.py`): grid A* per net in a fixed order with
  costs for steps, bends and crossings; multi-pin nets grown greedily; no
  rip-up; failed connections were silently skipped.
* **Rendering** (`svg.py`): wires, junctions, components; net colours
  duplicated between router and renderer.

### Weaknesses found

| Weakness | Consequence |
|---|---|
| Name-based bands and special cases in placement | Overfit to the large example; arbitrary for any other circuit |
| Single-hub, two-column model | Chains, clusters and multi-hub circuits forced into columns; buses split to both sides of the hub |
| Placement blind to routing | Parts placed where wires must detour, wrap around, or cross |
| Single-pass overlap removal | New overlaps possible; no corridor sizing, so corridors too narrow for jogs |
| Sequential routing, no rip-up | Early nets take the best corridors; later nets detour or fail |
| Failures dropped silently | Unconnected pins in 4/17 inputs (34 hard violations in total) |
| No notion of related signals beyond identical component sets | Bundles split, cross each other, take opposite sides |
| Bundle pin orders not reconciled | e.g. the LCD bus: 13 crossings between two parts that could be wired straight |
| No net labels; rails routed like signals | Hard to tell which wire is which |
| Kind detection matched single letters | `Device C` became a capacitor, `Header L` an inductor |
| Vertical capacitors drawn rotated 90° from their pins | Orientation inferred from `height > width` (40×40) |
| Web hot reload rebound modules but not `ParseError` | After the first edit, syntax errors were reported as internal errors |

---

## 3. Problem formulation

**Input.** A hypergraph `H = (V, E)`: vertices are components, each a
rectangle with *ports* (pins) on its boundary; hyperedges are nets, each a
set of ports.  Ports may have a declared side and order (hard) or be free.

**Variables.**

* component positions `(x_v, y_v)` on the 20 px grid;
* side and order of every free port (which also fixes component size);
* for every net `e`, a rectilinear Steiner tree `T_e` on the grid that
  connects exactly the ports of `e`.

**Hard constraints** (feasibility; violating any makes a drawing wrong):

| | Constraint |
|---|---|
| H1 | every port of every net is connected by its net's tree, and only by it |
| H2 | no two component footprints overlap; clearances between parts |
| H3 | wires never enter component bodies and never touch foreign pin terminals |
| H4 | two nets never share a wire segment (no collinear overlap) |
| H5 | two nets may meet only as a straight, perpendicular crossing — never at a bend, branch or wire end of either (no ambiguous T-touch or corner contact) |
| H6 | every wire leaves / enters a pin perpendicular to the component edge |
| H7 | all coordinates on the 20 px grid; output is valid SVG |
| H8 | declared pin sides and orders are kept |

**Objective** (soft, minimised): a weighted sum of crossings, bends, short
zig-zags, near-crossings, wires running directly beside unrelated wires,
bundle incoherence, detours, routes through or along bodies and labels,
area and instability (section 5 lists every term and weight).

The problem is NP-hard several times over: rectilinear Steiner trees
(Garey & Johnson 1977), crossing minimisation, and bend minimisation with a
free embedding (Tamassia 1987 is polynomial only for a *fixed* planar
embedding).  The engine therefore decomposes it and uses heuristics with an
explicit, measurable objective.

---

## 4. Research and choice of approach

### 4.1 What the literature says matters to readers

* **Crossings matter most, then bends** (Purchase 1997); continuity of paths
  is a strong factor in path tracing (Ware, Purchase, Colpoys & McGill
  2002).  Crossings at large angles are much less harmful than shallow ones
  (Huang, Eades & Hong 2014) — orthogonal drawings cross at 90° by
  construction.
* **Humans lay out orthogonal networks differently from classic
  algorithms** (HOLA, Kieffer, Dwyer, Marriott & Wybrow 2016): user-preferred
  layouts correlate with *grid-like alignment*, compactness and symmetry,
  and negatively with crossings, bends and uneven edge lengths.  Humans also
  add deliberate "aesthetic bends" and keep trees on the outside.
* **Gestalt**: proximity and connectedness drive grouping; continuation
  drives path following; common region and similarity (colour) group
  related elements (Bennett et al. 2007).  Related wires should therefore
  run together, be coloured alike and sit near the parts they connect.
* **Mental map / stability**: preserving the orthogonal ordering of nodes
  when adjusting a layout keeps it recognisable (Misue, Eades, Lai &
  Sugiyama 1995).

### 4.2 Candidate techniques

| Technique | Strengths | Weaknesses here | Used? |
|---|---|---|---|
| Force-directed / stress majorization (Eades; Gansner, Koren & North 2004) | Topology-driven, finds clusters and rings | Ignores ports; organic, radial "spider-web" results; symmetric parts land on opposite sides of a hub | **Yes**, as one seed, with port-side constraints |
| Layered / Sugiyama with port constraints (Sugiyama et al. 1981; Spönemann et al. 2009; Schulze et al. 2014; used by netlistsvg via ELK) | Structured flow, proven crossing reduction (barycentre), pin-aligned coordinates (Brandes & Köpf) | Needs directions; hub stars give tall columns; long edges | **Yes**, as the primary seed (columns grown from the hub by port direction) |
| Constraint-based layout (IPSep-CoLa, VPSC: Dwyer et al. 2005; grid-like layout: Kieffer et al. 2013) | Hard non-overlap + soft alignment | Needs a QP solver for full generality | Partly: separation-style legalisation, alignment via priority placement |
| Simulated annealing / multicriteria hill climbing (Davidson & Harel 1996; Stott et al. 2011 for metro maps) | Arbitrary weighted objectives | Slow in pure Python; random-looking if the objective misses structure | Replaced by deterministic steepest descent with structured moves |
| ILP / CP (e.g. Nöllenburg & Wolff 2011 for metro maps) | Optimal hard/soft constraint handling | No solver available without new dependencies; slow | No (hard/soft split adopted conceptually) |
| Topology–Shape–Metrics, bend minimisation by min-cost flow (Tamassia 1987) | Optimal bends for a fixed embedding | Assumes planarisation; components are large boxes with many ports | No |
| Maze routing / A* on a grid (Lee 1961; Hart et al.) | Any cost model, hard rules easy to enforce | Order dependence; slow if unbounded | **Yes**, with a direction/run-length state and bounded search |
| Line-probe routing (Mikami–Tabuchi 1968, Hightower 1969) | Few bends, fast | Not complete; no rich cost model | No |
| Orthogonal connector routing with nudging (Wybrow, Marriott & Stuckey 2009) | Optimal length+bends per connector, then separates shared segments | Nudging needs continuous coordinates; grid is a project invariant | Ideas adopted: port-direction route shapes; ordering of wires sharing a corridor |
| Ordered bundles / metro-line crossing minimisation (Benkert et al. 2006; Pupyrev et al. 2011) | Related edges as parallel ribbons with consistent order | Separate optimisation stage | Ideas adopted: shared-corridor crossing estimate, bundle affinity cost |
| Negotiated congestion (PathFinder, McMurchie & Ebeling 1995) | Removes net-order dependence | Many full iterations | Adopted as rip-up-and-reroute with acceptance test |
| Steiner heuristics (Hanan grid; FLUTE, Chu & Wong 2008) | Short trees | Shortest is not most readable | Incremental tree growth by multi-source A* |

### 4.3 The chosen hybrid

A **hybrid global + local strategy with routing in the loop**:

1. semantic analysis decides what nets and parts *mean*;
2. **two different seeds** (layered port-aware columns, and stress
   majorization) capture structure and topology respectively;
3. a **deterministic local search** on an *estimated routing* objective lets
   placement anticipate routing;
4. every candidate is **actually routed and scored**; the real metrics, not
   the estimate, choose the winner.  For ambiguous free two-pin parts, the
   opposite axis is placed and routed as an extra candidate, so orientation
   is decided by the router rather than by a kind-specific rule;
5. the winner gets **rip-up-and-reroute**; if any pin is still unconnected,
   the engine **widens the corridors and starts again**.

Why: each piece is understandable and testable on its own; the engine is
deterministic (no randomness, no time limits, no iteration over hash-ordered
sets); it is pure Python with no new dependencies; and the multi-candidate
selection makes it robust — the estimate guides, but the real router has the
final word.  Measurements during development showed exactly this: the
layered seed alone is often already excellent, the search usually improves
it, and in a minority of cases the stress seed wins (section 9).

---

## 5. Objective function and weights

All weights live in `schematic/layout/config.py`.  Routing and placement
costs are in **grid steps**: 1 = cost of 20 px of straight wire.

### 5.1 Router cost model (`RoutingWeights`)

| Term | Default | Meaning |
|---|---:|---|
| `step` | 1.0 | per grid step of wire |
| `bend` | 6.0 | per bend (× `rail_bend_factor` = 0.6 for power rails) |
| `jog` | 14.0 | extra when a bend follows another bend after only one step (20 px zig-zag) |
| `crossing` | 14.0 | crossing an unrelated net |
| `crossing_related` | 20.0 | crossing a member of the same bundle / differential pair |
| `near_foreign` | 1.5 | per step directly beside (20 px) an unrelated wire |
| `bundle_stray` | 0.35 | per step a bundle member does *not* run beside an already routed mate |
| `pin_corridor` | 3.0 | passing through the two grid steps in front of another net's pin |
| `hug` | 0.4 | running directly along a component outline |
| `label` | 1.5 | running through the name / value text of a standard part |
| `junction` | 1.0 | every new T-junction |

### 5.2 Placement estimate (`PlacementWeights`)

| Term | Default | Meaning |
|---|---:|---|
| `bend` | 5.0 | per estimated bend of a pin-to-pin connection |
| `length` | 0.12 | per grid step of estimated wire |
| `crossing` | 9.0 | per estimated crossing between two nets |
| `obstacle` | 4.0 | per body an estimated route cuts through, plus the detour (2 bends + way round) |
| `near_miss` | 6.0 | facing pins offset by one grid step (would produce a tiny jog) |
| `misfacing` | 12.0 | connection needs ≥ 3 bends (pin faces away from its peer) |
| `rail_factor` | 0.25 | multiplier for power / ground rails |
| `area` | 0.004 | per grid step² of bounding box |
| `aspect` / `max_aspect` | 1.0 / 4.0 | per unit of aspect ratio above 4:1 (long signal chains are fine) |
| `stability` | 0.02 | per grid step moved away from the seed (mental-map preservation) |

### 5.3 Readability score (`ScoreWeights`, used for reporting and candidate selection)

`score = 100 · exp(−P)`, halved if any hard violation exists, with

```
P = (0.45·crossings + 0.12·bends + 0.6·jogs + 0.35·near_crossings
     + 0.25·tight_unrelated_parallels + 0.25·dense_regions) / connections
  + 0.6·(detour_ratio − 1)
  + 0.5·(1 − bundle_parallelism)          (only if the drawing has bundles)
```

`connections` = Σ over nets of (terminals − 1), so the score is comparable
across drawings of different size.  The exponential keeps the score in
(0, 100] and makes it fall quickly when problems pile up.

### 5.4 Spacing (`SpacingConfig`, pixels)

| Field | Default | Meaning |
|---|---:|---|
| `min_h_gap` | 100 | terminal-to-terminal corridor between side-by-side parts (two escape columns + free tracks) |
| `track_gap` | 20 | extra corridor per net beyond two between the pair |
| `min_v_gap` | 60 | gap between stacked footprints |
| `satellite_offset` | 60 | distance of a pull-up / filter part from its pin |
| `group_gap`, `isolated_gap`, `margin` | 160, 60, 80 | between sub-circuits, isolated parts, border |

---

## 6. Quality metrics (`schematic/layout/metrics.py`)

`evaluate(schematic)` scores any placed and routed schematic — also those
produced by older engine versions (via `schematic/serialize.py`).  Wires are
rasterised to unit grid edges, which makes all checks exact.

| Metric | Definition |
|---|---|
| `wire_length`, `lower_bound_length`, `detour_ratio` | total wire vs. Σ per net of max(HPWL, MST/1.5) (a lower bound of the rectilinear Steiner tree) |
| `bends`, `max_bends_single_net` | nodes where a net's wire turns 90° |
| `crossings` | nodes where two nets cross straight and perpendicular |
| `near_crossings` | a bend / branch / wire end of a net pointing at an unrelated wire one step away |
| `acute_angles` | segment pairs of a net meeting below 90° (always 0 for orthogonal output) |
| `small_jogs` | runs of one grid step between two direction changes |
| `junctions` | branch points (dots) |
| `min_spacing`, `avg_spacing` | distance to the nearest parallel *unrelated* wire |
| `tight_parallel_pairs` | unrelated net pairs running 20 px apart for ≥ 40 px |
| `bundles`, `bundled_nets`, `parallelism_score` | per bundle member: share of its wire within 40 px of a mate |
| `component_overlaps`, `min_component_gap` | body rectangles |
| `dense_regions` | 100×100 px cells with ≥ 400 px of wire from ≥ 6 nets |
| `component_area_ratio`, `width`, `height` | whitespace and size |
| `violations` | `unrouted_pins`, `disconnected_nets`, `wire_through_component`, `collinear_overlap`, `dual_nodes`, `t_touches`, `foreign_terminal_contact`, `diagonal_segments`, `off_grid_segments`, `component_overlaps` |
| `readability_score` | section 5.3 |

`QualityReport.format_table()` prints a human-readable table; the CLI shows
it with `--report` and writes JSON with `--json-report FILE`.

---

## 7. New architecture

```
text
 │ parse (parser.py)
 ▼
Schematic ──────────────────────────────────────────────── engine.py: layout_schematic()
 │ 1 normalise        normalize.py   nets sharing a pin are one electrical node
 │ 2 analyse          semantics.py   ground / power / signal, rails, bundles,
 │                                   diff pairs, families, part roles, hubs
 │ 3 for each candidate (seed, search?) on a copy:
 │     place          placer.py      classification, satellites, port configuration,
 │                    seeds.py         layered seed │ stress seed
 │                    search.py        local search on the estimate (estimate.py)
 │                    pin_allocator.py sizes, pin sides / orders / coordinates
 │     route (first pass)  router.py
 │     labels         labels.py
 │     score          metrics.py
 │ 4 keep the best candidate; rip-up-and-reroute it; relabel; rescore
 │ 5 any pin unconnected? widen spacing, go to 3 (≤ 2 times)
 ▼
render (renderer/svg.py, colours from styles.py) ──▶ SVG
```

| Module | Responsibility |
|---|---|
| `layout/config.py` | every tunable weight and setting; effort presets |
| `layout/normalize.py` | input normalisation |
| `layout/semantics.py` | single source of truth for net / part meaning |
| `layout/placer.py` | placement core: classification, satellites, port configuration, legalisation, packing |
| `layout/seeds.py` | layered and stress initial placements |
| `layout/search.py` | deterministic local search |
| `layout/estimate.py` | fast routing estimate used by placement |
| `layout/pin_allocator.py` | component shape from pins |
| `layout/router.py` | grid router |
| `layout/labels.py` | net-name label placement |
| `layout/metrics.py` | quality metrics and score |
| `layout/engine.py` | orchestration, candidate selection, feedback |
| `serialize.py`, `styles.py` | geometry ⇄ JSON; net colours |

Public API (`schematic/__init__.py`) is unchanged and extended:
`process_schematic(text, config=None)`, `generate_schematic_svg(text,
dark_theme=True, config=None)`, `place_schematic`, `route_schematic`,
`render_schematic_to_svg`, plus `layout_schematic`, `LayoutConfig`,
`LayoutResult`, `QualityReport`, `evaluate_quality`.

---

## 8. Placement

### 8.1 Semantic analysis (`semantics.py`)

* **Net class** from net-name tokens (`GND`, `AGND`, `VSS`… → ground;
  `VCC`, `3V3`, `+12V`, `VBAT`, `DISP_VCC`… → power), falling back to a vote
  over pin names for auto-named nets.  A **rail** is a power/ground net
  spanning ≥ 3 components.
* **Bundles**: non-rail nets joining the same set of non-passive components
  (SPI lines, UART pairs, an I²C bus whose pull-ups differ).  Kinds: `diff`
  (complementary suffixes: P/N, +/−, H/L, DP/DM…), `bus` (multi-drop),
  `parallel`.
* **Families** (`I2C_SDA` + `I2C_SCL` → `I2C`) and **differential pairs**
  also across bundles.
* **Part roles**: standard 2-pin parts become `shunt` (supply on one side,
  signal on the other: pull-ups, filter caps), `decoupling` (supply–ground)
  or `series` (in the signal or power path; fuses, inductors and switches
  always are).
* **Hubs**: modules with the highest signal connectivity.

### 8.2 Satellites

Shunt and decoupling parts are not placed on their own.  They become
*satellites* hanging off the wire of the pin they serve, vertical, with the
supply side away from the signal (pull-up above, filter / pull-down below) —
the conventional drawing.  Anchor and side are a small optimisation: every
macro pin on the net is a candidate anchor, both sides are allowed, and the
choice minimises (10 × connected neighbour pins the body would wall in)
+ (3 × deviation from convention) + (1 × not at the hub).  Satellites on one
side of an anchor get cumulative distances along the wire that leave room
for their upright labels.  Their footprint is merged into the anchor's, so
corridors automatically make room.

### 8.3 Seeds

* **Layered** (`LayeredSeedMixin`): the hub is column 0; parts are added in
  order of connection strength.  A declared pin side fixes the column
  offset (wired to the hub's left pins → left column); otherwise a part
  joins the column of already placed *bus mates*, continues outward from
  its parent, or balances left / right.  Within each column the part with
  the strongest links to other columns takes its **exact pin-aligned**
  position first, weaker parts take the nearest free slot below (priority
  method; least-squares compromises would align nothing exactly).
  Corridor widths follow the number of wires that must jog between two
  columns; parts are aligned to the column edge their pins face.
* **Stress** (`StressSeedMixin`): stress majorization on graph distances
  (edge length grows as connection strength falls), seeded by a
  deterministic BFS, with declared pin sides enforced as separation
  constraints after each iteration.

### 8.4 Port configuration (repeated whenever geometry changes)

* free pins face their peers; for a **multi-drop bus**, a device's pin
  faces the bus *source* (the hub), not its fellow devices;
* a pin whose peer sits directly above / below goes to the **outer** side,
  so stacked parts connect with a U outside instead of looping round;
* **supply pins of stacked parts go to the side away from their signals**,
  so the bus keeps the inner corridor and the rails get a clean trunk
  outside (cuts the inherent comb crossings from ~6 to ~1 per device);
* pins on a side are ordered by the barycentre of their peers (rails:
  supply first, ground last), so bundles leave a part without crossing;
* two-pin parts are initially oriented along their connection; when both
  axes are plausible, the opposite axis becomes a routed candidate;
* equivalent pins (several `GND` pins) resolve to the one nearest the peers.

### 8.5 Legalisation

Footprints = body + pin terminals + satellites.  Required clearance between
two parts: a terminal-to-terminal corridor of `min_h_gap` plus `track_gap`
per extra net between them, or `min_v_gap` when stacked.  **Parts that are
wired to each other must be stacked or keep a full corridor** — a diagonal
arrangement with a narrow gap leaves no tracks for the jogs between their
facing pins.  Overlaps are resolved by pushing the part further from the
group centre outwards along the cheapest axis (relative order preserved).

### 8.6 The routing estimate (`estimate.py`)

Each net becomes pin-to-pin connections: a **star from the hub's pin** for
multi-drop signal nets (taps off the source's line, the way buses are drawn)
and a Manhattan MST for rails.  Each connection gets the minimal-bend
orthogonal shape that respects both escape directions: straight, L, Z
(jog column between the escape points, otherwise S), U, S.  Costs: bends,
length, near-misses, detours around bodies.  **Crossings** count proper
intersections; for two estimated routes sharing a line the estimate does what
the router will do — put them on adjacent tracks in the better order — and
counts a crossing only if their end stubs interleave (exact for a pair).
Moving one part re-evaluates only the nets attached to it.

### 8.7 Local search (`search.py`)

Deterministic steepest descent: for each part, candidate moves are
*align* (shift so that one connection becomes straight), *shift* (±20, ±60,
±140 px), *relocate* (right / left / above / below each of its three most
strongly wired partners, edge-aligned) and *swap* with a nearby part.
Illegal moves are skipped; the best improving move is applied.  After each
sweep pins are re-configured and the layout re-legalised.  An exact pruning
bound skips a move whose net-cost increase cannot be offset even if all its
crossings vanished.

### 8.8 Packing

Unconnected sub-circuits are laid out separately and packed in rows; parts
without any connection go into a row below the drawing.

---

## 9. Routing (`router.py`)

### 9.1 Search space

A* on the 20 px grid over states **(node, heading, run-length bucket)**; the
bucket (first segment / one step since a bend / longer) lets the cost model
punish 20 px zig-zags.  Heuristic: Manhattan distance (admissible, all step
costs ≥ 1).  Each search is first confined to the bounding box of tree and
target plus 14 steps; only if that fails is the whole grid searched.

### 9.2 Hard rules (enforced while searching)

* never enter a body, a foreign terminal, or the **escape node** in front of
  another net's pin (shared escape nodes are allowed only to their owners);
* never use a grid edge already used by any net (H4);
* bend only at nodes no other net occupies; pass a node another net occupies
  only straight and perpendicular to it (H5);
* leave the source pin and enter the target pin along the pin's outward axis
  (H6);
* never pass through the net's own tree (no self-crossings, no 4-way joins).

### 9.3 Net order, trees and recovery

1. nets whose two pins face each other on one line (straight wires),
2. signal bundles together, in pin order,
3. other signals by half-perimeter,
4. local power nets, then rails (ground last).

Multi-pin nets grow a Steiner tree: the pin nearest to the current tree is
joined by a **multi-source search from every valid tree node** (not at
crossings, not at existing 3-way junctions).  If a pin cannot be reached,
**conflict-driven recovery** runs a search that prices other nets'
occupancy instead of forbidding it, rips up exactly the nets that block that
path, commits the blocked net and re-routes the victims (depth-limited).
A net that still cannot be routed is reported, never drawn ambiguously.

### 9.4 Rip-up and reroute

On the winning candidate, every net whose cost exceeds its lower bound by at
least one bend is ripped up and re-routed against all others; the new route
is kept only if strictly cheaper (two passes by default).  This removes most
of the order dependence of sequential routing (cf. PathFinder).

### 9.5 Output

Unit edges are merged into maximal straight segments, split at bends,
junctions and pin terminals.  Junction dots go on nodes of degree ≥ 3 and
on pins where a net branches.

---

## 10. Bundles and parallel routing

Related wires behave as groups through several mechanisms that reinforce
each other:

* **placement**: bundle pins are ordered consistently on both parts
  (barycentric), parts sharing a bus share a column, bus pins face the
  source, and the corridor between strongly connected parts is widened by
  one track per wire;
* **routing**: bundle members are routed consecutively in pin order; a
  member pays `bundle_stray` for every step it does not run beside an
  already routed mate, so later members follow the first one as a ribbon at
  20 px pitch; crossing a mate costs more than crossing an unrelated net;
  running beside an unrelated wire costs `near_foreign`, which keeps
  unrelated groups apart;
* **differential pairs** are bundles of kind `diff` and therefore adjacent
  wherever geometry allows;
* **colour and labels**: each bus family has one hue; every user-named net
  gets one label on a free horizontal segment.

Measured coherence (`parallelism_score`) on the four examples rose from
0.69–0.90 to 0.85–0.96 (1.0 on the 8-bit LCD bus and the labelled circuit).

---

## 11. Refinement and feedback

* **Candidates**: `balanced` effort tries `layered`, `layered+search` and
  `stress+search`.  Each is placed, routed (first pass), labelled and scored
  with the real metrics; the best (fewest violations, then highest score)
  wins.  Which candidate wins depends on the circuit: on the 17 test inputs
  the plain layered seed wins 6 (e.g. the linear chain, the I²C bus, the
  crossing-prone case), `layered+search` wins 10 (e.g. example_large, the dense
  network) and `stress+search` wins 1 (the buck converter with its long
  feedback loop, which is a ring rather than a hub).
* **Orientation candidates**: after the seed/search candidates, ambiguous
  free two-pin standard parts (both axes plausible from their peer
  directions) are refined on the *winning drawing*: every position stays,
  the part gets the opposite axis, its satellites re-slot, the result is
  legalised, routed and scored.  This is a local change, not a fresh global
  placement, so a crossing gain elsewhere cannot be mistaken for an
  orientation gain.  The trials are spread over groups first;
  `orientation_candidates` caps them (1 `fast`, 2 `balanced`, 4 `thorough`).
  This replaces kind-specific orientation rules such as "diodes horizontal".
* **Rotation candidates**: the same local refinement now also rotates any
  other part by 90° (clockwise or counter-clockwise, whichever direction
  faces its peers better).  Graphical/image parts are always considered
  first, because their pin layout is what makes rotation worthwhile; other
  parts are tried when their pins face away from their wiring.  Every part
  is rotatable by default; a declaration can opt out with
  `rotatable: false` (on a `part` template or on a component).
  `rotation_candidates` caps the trials (1 `fast`, 4 `balanced`,
  6 `thorough`).  Positions are kept and the group is legalised again, so a
  rotated part can push its neighbours aside without re-optimising the whole
  drawing.
* **Rip-up passes** run on the normal winner before orientation refinement;
  a close orientation variant gets the same treatment before it may replace
  it (`orientation_reroute_margin`, `orientation_accept_margin`).
* **Spacing feedback**: if pins are still unconnected the corridors were too
  tight; spacing grows by 60 / 40 px and the pipeline reruns (≤ 2 times).

---

## 12. Other changes

* **Parser**: a net now records whether its name was generated
  (`Net.auto_named`); single-letter kind words are no longer accepted in the
  "Value Kind" form (`Device C` is not a capacitor; `R 10k` and `kind: c`
  still work).
* **Rendering**: colours per theme from `styles.py` with ground / power taken
  from the semantic class; standard symbols drawn in local coordinates and
  rotated for vertical placement (all kinds; labels stay upright); diode
  direction follows the anode pin; orientation taken from the pins (fixes
  vertical capacitors); net labels; tight view box with a minimum size;
  `data-net` attribute on wires.
* **Model**: `NetLabel`, `Schematic.labels`.
* **CLI**: `--effort {fast,balanced,thorough}`, `--report`, `--json-report`,
  `--light`.
* **Web**: effort selector; the whole `schematic` package is reloaded on
  each request; parse errors are recognised after reloads.

---

## 13. Known limitations and pathological cases

* **Runtime.** Pure Python.  `balanced` lays out the large example
  (13 parts, 22 nets) in about 9 s, `fast` in about 2.5 s; small drawings take
  well under a second.  The default rotation probes account for the larger
  share: every additional rotation candidate routes the whole drawing.
  Larger, unstructured inputs grow quickly: random
  netlists with 18 / 25 parts took 6.5 s / 18 s with `balanced`, 30 parts
  5.5 s with `fast` (all without violations).  The placement estimate's
  crossing count is quadratic in connections and A* cost grows with the grid;
  use `fast` for live previews of big drawings.
* **The score is a proxy.**  It measures wires, not structure, and can prefer
  a drawing that looks less orderly (`02_branching`: the old layout scores
  higher despite pins facing away and wires wrapping round parts).
  Candidate selection inherits this.  Always look at the drawings
  (`tools/layout_report.py`).
* **Supply rails** still produce the longest wires in large drawings: they
  are routed last and must go round everything.  A power-symbol (net label)
  mode for rails would remove them but changes what the drawing promises;
  not implemented.
* **Local search is greedy** and sensitive: small changes to weights can move
  individual cases by several points either way.  The candidate selection
  dampens this but does not remove it.
* **Inherent crossings remain**: a bus tapped by stacked devices, fully
  inverted declared pin orders (`07_crossing_prone`), interleaved hub pins.
  They are drawn as regular, perpendicular crossings.
* **Routing is not globally optimal**: no exact channel routing / nudging;
  bundle coherence is encouraged by cost, not guaranteed.
* **Box components never get top / bottom pins automatically** (only when
  declared); the box header is not adjusted for top pins.
* **Satellites** are limited to two-pin parts with a supply on exactly one
  side (or decoupling between supply and ground).
* **Labels**: one per user-named net; skipped when no free horizontal
  segment exists.
* **Spacing feedback** only reacts to unconnected pins, not to merely
  congested corridors.

---

## 14. Tuning guide

| Symptom | Knob (in `LayoutConfig`) |
|---|---|
| wires take long detours to avoid crossings | lower `routing.crossing` / `crossing_related`, or raise `routing.step` |
| too many crossings | raise `routing.crossing`; raise `placement.crossing` |
| staircases / small zig-zags | raise `routing.jog`, `routing.bend`; `placement.near_miss` |
| related wires drift apart | raise `routing.bundle_stray`; lower `near_foreign` |
| unrelated wires bunch together | raise `routing.near_foreign` |
| drawing too sparse | lower `spacing.min_h_gap`, `min_v_gap`, `track_gap`; raise `placement.area` |
| corridors too tight, detours around parts | raise `spacing.min_h_gap` / `track_gap` |
| long chains folded into blocks | lower `placement.aspect`, raise `max_aspect` |
| parts drift far from the seed | raise `placement.stability` |
| pull-ups too close / too far | `spacing.satellite_offset` |
| rails wrap round the bus side | `outer_rail_pins` (on by default) |
| slow | `config_for_effort("fast")`, fewer `candidates`, lower `search_sweeps`, `reroute_passes` |
| want the best possible | `config_for_effort("thorough")` |

After changing defaults: run the comparison report, look at every drawing,
then regenerate the baseline (section 16).

---

## 15. Tests

| File | What it checks |
|---|---|
| `tests/test_layout_quality.py` (new) | all 17 inputs: no violations, grid, labels clear of parts, SVG renders; structural expectations (straight chain, ribbon bus, straight SPI, coherent differential pairs and links, crossing-free clusters, isolated parts out of the way, fan-out junctions); regression floors and exact geometry snapshots against `tests/layout_quality_baseline.json` |
| `tests/test_semantics.py` (new) | net classes, rails, bundles, diff pairs, part roles, kind-detection fix, net merging |
| `tests/test_metrics.py` (new) | each metric on hand-built geometry with known answers |
| `tests/test_estimate.py` (new) | route shapes, shared-corridor crossing estimate |
| `tests/test_router.py` (new) | hard rules on hand-placed parts; honest failure reporting |
| `tests/test_engine.py` (new) | spacing feedback, candidate selection, effort levels, empty / single-part inputs |
| `tests/test_determinism.py` (new) | identical SVG across interpreter processes with different `PYTHONHASHSEED` |
| `tests/test_layout.py` (modified) | standard symbol sizes are now checked independently of orientation (a pull-up is drawn vertically, 40×60) |
| `tests/test_parser.py`, `tests/test_renderer.py` | unchanged, passing |

Run everything with `python3 -m unittest discover tests` (about 40 s).

---

## 16. Contributor guide

* **Add a layout test case**: put a commented `.txt` file into
  `tests/layout_cases/` stating what a good drawing must show; add a
  structural assertion to `tests/test_layout_quality.py` if the property is
  crisp; regenerate the baseline.
* **Compare with the previous engine**:
  `python3 tools/layout_report.py --baseline <git-rev> --out build/report`
  (PNG previews need ImageMagick; `--no-png` writes SVG only; `--cases` takes
  globs, `--no-baseline` renders only the current engine).
* **Regenerate the baseline** after an intended change, *after looking at the
  drawings*: `UPDATE_LAYOUT_BASELINE=1 python3 -m unittest
  tests.test_layout_quality`.  Commit the JSON together with the change.
* **Inspect one layout**: `python3 app.py file.txt -o out.svg --report
  --json-report q.json` (`placement_candidate` and all candidate scores are
  in the JSON).
* **Determinism rules**: never iterate over a raw `set` or rely on hash
  order; sort or use insertion-ordered dicts.  No randomness, no time-based
  budgets.
* **Where to put things**: meaning of nets / parts → `semantics.py`; a new
  weight → `config.py` with unit and comment; anything that changes what is
  *legal* → router hard rules *and* a metric violation *and* a test.

---

## References

* Benkert, Nöllenburg, Uno, Wolff. *Minimizing Intra-Edge Crossings in Wiring Diagrams and Public Transportation Maps.* GD 2006, LNCS 4372 (2007). <https://i11www.iti.kit.edu/extra/publications/bnuw-miecw-07.pdf>
* Bennett, Ryall, Spalteholz, Gooch. *The Aesthetics of Graph Visualization.* Computational Aesthetics 2007.
* Brandes, Köpf. *Fast and Simple Horizontal Coordinate Assignment.* GD 2001, LNCS 2265 (2002); erratum: Brandes, Walter, Zink, <https://arxiv.org/abs/2008.01252>
* Chu, Wong. *FLUTE: Fast Lookup Table Based Rectilinear Steiner Minimal Tree Algorithm for VLSI Design.* IEEE TCAD 27(1), 2008. <https://home.engineering.iastate.edu/cnchu/pubs/j29.pdf>
* Davidson, Harel. *Drawing Graphs Nicely Using Simulated Annealing.* ACM TOG 15(4):301–331, 1996.
* Dwyer, Marriott, Stuckey. *Fast Node Overlap Removal.* GD 2005, LNCS 3843. <https://doi.org/10.1007/11618058_15>
* Gansner, Koren, North. *Graph Drawing by Stress Majorization.* GD 2004, LNCS 3383. <https://www.graphviz.org/documentation/GKN04.pdf>
* Hanan. *On Steiner's Problem with Rectilinear Distance.* SIAM J. Appl. Math. 14:255–265, 1966.
* Huang, Eades, Hong. *Larger Crossing Angles Make Graphs Easier to Read.* JVLC 2014.
* Kieffer, Dwyer, Marriott, Wybrow. *Incremental Grid-like Layout Using Soft and Hard Constraints.* GD 2013. <https://arxiv.org/abs/1308.6368>
* Kieffer, Dwyer, Marriott, Wybrow. *HOLA: Human-like Orthogonal Network Layout.* IEEE TVCG 22(1):349–358, 2016. <https://ialab.it.monash.edu/~dwyer/papers/hola2015.pdf>
* Marriott, Purchase, Wybrow, Goncu. *Memorability of Visual Features in Network Diagrams.* IEEE TVCG 18(12), 2012. <https://users.monash.edu/~mwybrow/papers/marriott-infovis-2012.pdf>
* McMurchie, Ebeling. *PathFinder: A Negotiation-Based Performance-Driven Router for FPGAs.* FPGA 1995.
* Misue, Eades, Lai, Sugiyama. *Layout Adjustment and the Mental Map.* JVLC 6:183–210, 1995.
* Nöllenburg, Wolff. *Drawing and Labeling High-Quality Metro Maps by Mixed-Integer Programming.* IEEE TVCG 17(5), 2011. <https://i11www.iti.kit.edu/extra/publications/nw-dlhqm-10.pdf>
* Pupyrev, Nachmanson, Bereg, Holroyd. *Edge Routing with Ordered Bundles.* GD 2011. <https://arxiv.org/abs/1209.4227>
* Purchase. *Which Aesthetic Has the Greatest Effect on Human Understanding?* GD 1997, LNCS 1353. <https://eprints.gla.ac.uk/35804>
* Purchase. *Metrics for Graph Drawing Aesthetics.* JVLC 13(5):501–516, 2002. <https://eprints.gla.ac.uk/35814>
* Sander. *Layout of Directed Hypergraphs with Orthogonal Hyperedges.* GD 2003, LNCS 2912.
* Schulze, Spönemann, von Hanxleden. *Drawing Layered Graphs with Port Constraints.* JVLC 2014. <https://rtsys.informatik.uni-kiel.de/~biblio/downloads/papers/jvlc13.pdf>
* Spönemann, Fuhrmann, von Hanxleden, Mutzel. *Port Constraints in Hierarchical Layout of Data Flow Diagrams.* GD 2009. <https://rtsys.informatik.uni-kiel.de/~biblio/downloads/papers/gd09.pdf>
* Stott, Rodgers, Martinez-Ovando, Walker. *Automatic Metro Map Layout Using Multicriteria Optimization.* IEEE TVCG, 2011. <https://www.cs.kent.ac.uk/pubs/2011/2963>
* Sugiyama, Tagawa, Toda. *Methods for Visual Understanding of Hierarchical System Structures.* IEEE Trans. SMC 11(2):109–125, 1981.
* Tamassia. *On Embedding a Graph in the Grid with the Minimum Number of Bends.* SIAM J. Comput. 16(3):421–444, 1987.
* Ware, Purchase, Colpoys, McGill. *Cognitive Measurements of Graph Aesthetics.* Information Visualization 1(2):103–110, 2002.
* Wybrow, Marriott, Stuckey. *Orthogonal Connector Routing.* GD 2009, LNCS 5849. <https://doi.org/10.1007/978-3-642-11805-0_22>
* *The Development of Automatic Schematic Generator.* J. Korean Inst. of Telematics and Electronics A 28A(9):761–773, 1991 (preprocessing → logical placement → pin assignment → global → detailed routing). <https://www.koreascience.or.kr/article/JAKO199111920698416.page>
* netlistsvg (schematics from Yosys netlists with ELK layered layout). <https://github.com/nturley/netlistsvg>
