# Layout engine

How the engine decides where parts go and how wires run, why it works that
way, and how to change it without breaking it.

## 1. Goal: readability

The tool documents wiring. A reader must be able to follow every wire from
pin to pin and recognise functional groups. The drawing does not need to be
as compact as possible, but a group should look like one.

"Readable" means the eye can **follow a wire** without hesitating anywhere,
and it sees **groups** at a glance. That gives testable properties.

**A single wire**

| | Property | Why | Implementation |
|---|---|---|---|
| L0 | **Unambiguous and complete** | an ambiguous drawing is wrong | hard rules: every pin connected, no wire through a part or a foreign terminal, no shared wire segments, foreign nets touch only as a straight, perpendicular crossing |
| L1 | **Few bends, two-pin wires straight** | the eye follows straight lines (Gestalt: good continuation); a bend that a 2-step shift would remove looks like a mistake | bend cost; alignment moves (single connections and whole straight-wired chains) |
| L2 | **No crossings**, perpendicular if unavoidable | strongest factor for reading errors (Purchase 1997; Ware et al. 2002) | crossing cost; the router only crosses at right angles |
| L3 | **Short and direct** | a wire across the whole drawing is hard to follow and crosses more | length cost (not negligible, see below) |
| L4 | **Pins face their partners** | otherwise wires detour around their own part | the side of every free pin is an optimisation variable |

**Several wires**

| | Property | Implementation |
|---|---|---|
| B1 | **Bundles run in parallel**: wires between the same parts (SPI, UART, I²C, a motor connector) run side by side, in the same order, and bend together | bundle = signal nets joining *exactly the same parts* (purely structural). Router: cost per step without a mate alongside, no spacing cost between mates, mates are routed one after another. Barycentric pin order keeps a bundle from twisting |
| B2 | **Buses and supplies as a trunk with straight branches** | estimate: single-trunk Steiner model; router: Steiner tree |
| B3 | **Unrelated wires keep their distance** | cost per step at 20 px from a foreign wire (estimate and router) |
| B4 | **Pins are not boxed in** | the grid point in front of a pin belongs to its net (hard), the second one is expensive |

**Parts and the whole drawing**

| | Property | Implementation |
|---|---|---|
| P1 | **What belongs together stays together** | wire length; analytic start (partners attract each other) |
| P2 | **Readable text** | text zones are part of the footprint and the router avoids them; pin names of image parts run along their own wire |
| P3 | **Photos upright** | image parts turn by ±90° only, at a cost |
| P4 | **Calm, compact overall picture** | minimum clearance (hard), perimeter term |

**Structure**

| | Property | Implementation |
|---|---|---|
| S1 | **Functional groups are visible**: the parts of a MOSFET driver, a bus transceiver with its termination, the battery path sit together and apart from the rest | blocks found from the wiring alone (modularity communities, `structure.py`); a wider gap between blocks than inside one (`block_gap` vs. `clearance`); blocks laid out compactly |
| S2 | **The core in the middle, everything on the side of its pins**: a microcontroller wired to everything is the centre; each group sits beside the pins it uses, in the order of those pins | hub detection; ring arrangement (`engine._Arranger.place_ring`) |
| S3 | **Supplies do not cut through the drawing**: ground and supply rails become a tag with the net name at every pin, as in every schematic editor | net tags (`structure.py`, `shapes.with_tags`) |

The most important design rule for the drawing itself: **there is exactly
one cost model** (`config.Weights`). The same numbers price a placement
(through the route estimate), steer the router (A* step costs) and score the
result. When all three stages agree on what "better" means, an improvement in
one place cannot cause a regression in another that the model does not see.
The structure (S1-S3) is not priced: it is built in by the way the drawing
is assembled, because a cost model sees each wire and each crossing, not
the picture as a whole.

Default weights: crossing 12, bend 4, length 0.4 per grid step (20 px),
junction 0.5, step beside a foreign wire 0.5, bundle step without a mate
0.4, a photo turned by 90° 30. So a bend costs as much as 200 px of detour,
a crossing as much as three bends, a wire across 100 grid steps as much as
four bends. Nets with many terminals (5 or more: large buses) count bends
and length at half weight. Inside a block the perimeter weighs twice as
much (`block_compactness`), so a group also looks like one.

**Names have almost no influence.** Net, pin and part names are used for
text width (space), colour (`colors.py`: ground grey/black, supplies red)
and for one layout decision: which nets are supplies and become tags
(`colors.supply_kind`). Structure alone cannot tell a supply from a bus, both
join many parts. Grouping uses the wiring only. A test renames parts, pins
and signal nets and requires identical geometry
(`tests/test_engine.py::test_names_have_no_influence`).

## 2. Research

### Related projects

| Project | Approach | Relevance |
|---|---|---|
| **WireViz** (now *filare*) | YAML → Graphviz, harnesses and connector pinouts, BOM | documents single cables, explicitly not the wiring of a whole system; 1D, no 2D placement |
| **netlistsvg** | Yosys netlist → ELK *layered* | layered layout with port constraints; good for data flow, weak with bidirectional hubs |
| **d3-hwschematic** | elkjs, layered + orthogonal routing | the same principle for hardware diagrams |
| **Weave** (arXiv 2026) | netlist → LTspice via elkjs *layered*, grid snapping, read-back check of connectivity | connectivity as a hard, verified constraint (as `metrics.py` does here); struggles with dense multi-pin modules |
| **tscircuit autolayout** | template / pattern matching + packing, row and column layouts | rule based, beta |
| **SKiDL / KiCad export** | netlist from Python, schematic without good auto-layout | — |
| **HOLA** (Kieffer, Dwyer, Marriott, Wybrow 2016) | user study of human drawing; stress + trees outside + alignment | evidence: people prefer grid alignment and straight lines, compactness is secondary |
| **libavoid / Adaptagrams** (Wybrow et al. 2009) | orthogonal connector routing followed by nudging | router ideas |
| Patents on schematic generators | templates for analog topologies; criticism: classic generators optimise wire length instead of readability | confirms the goal |

### Mathematical models

| Model | Fit |
|---|---|
| **Sugiyama / layered with port constraints** (Sugiyama 1981; Schulze et al. 2014) | very tidy for directed flows, but forces everything into columns and needs edge directions; an MCU with pins on both sides is a hub, not a flow |
| **Topology-shape-metrics, bend minimisation by min-cost flow** (Tamassia 1987) | optimal only for a fixed planar embedding; parts with many fixed ports fit poorly |
| **ILP / MIP** (Nöllenburg & Wolff 2011 for metro maps; Mutzel & Weiskircher) | exact, clean hard/soft constraints; needs a solver (e.g. OR-Tools CP-SAT) and scales poorly with crossing variables |
| **Quadratic (analytic) placement with pin offsets** (VLSI placement) | solves the *global* arrangement in milliseconds; pin directions fit in as "target in front of the pin" |
| **Simulated annealing on an arbitrary objective** (Kirkpatrick 1983; Davidson & Harel 1996; Stott et al. 2011 for metro maps) | optimises exactly the objective we want, deterministic with a fixed seed; needs a fast, *honest* estimate |
| **Rectilinear Steiner trees, single-trunk heuristic** (Hanan 1966; FLUTE) | realistic model for buses and supplies |
| **A\* with heading state, rip-up and reroute / PathFinder** (Hart et al. 1968; McMurchie & Ebeling 1995) | standard for grid routing with bend costs and hard rules |
| **Community detection by modularity** (Newman & Girvan 2004; greedy agglomeration, Clauset, Newman & Moore 2004) | finds groups of parts wired more tightly to each other than to the rest, without parameters, in milliseconds for a few dozen parts |
| **Clustering before placement, floorplanning** (VLSI: hierarchical placement of clusters, then of cells) | small problems instead of one large one; the clusters stay together by construction |

**Chosen:** hierarchical placement. The circuit is split into functional
blocks (modularity on the signal wiring, a hub of its own); each block of a
few parts is placed by the VLSI-style annealing on an honest estimate that
agrees with the router; the blocks are then arranged as units around the
core, on the side of the pins they use; finally an A* router with the same
weights. Reasons: the groups a reader looks for are built in rather than
hoped for, every search is small (a handful of parts, a handful of blocks),
and it needs no dependencies (pure Python).

**Replaced: one annealing over the whole drawing** (versions up to 0.2).
14 independent runs over all parts, all routed, the best refined against
the routed result. On a 19-part project with supply wires
this took ten minutes on a four-core Windows machine and still
produced 27 crossings and supply wires around the whole drawing, without
recognisable groups: the cost model sees every wire, but nothing in it says
"these five parts are the MOSFET driver".

**Tried and dropped: exact fine alignment with CP-SAT.** Model: integer
positions, the arrangement is kept (per pair of parts one of the currently
valid separations), a boolean "connection is straight", straight wires must
not cut through a part, objective: bends saved minus displacement. Solved in
0.1–0.2 s. Result: one more straight connection in the largest example, but
the alignment put unrelated wires onto the same lines (conflicts 2 → 23);
with a length term it pulled the drawing together and caused even more
conflicts. An alignment model that does not know the other wires does not
improve readability. The local search already aligns with the full estimate
(including whole straight-wired chains), so no new dependency.

## 3. Architecture

```
text ─parse─▶ Schematic
  layout.engine.layout_schematic
   1 netlist.py     merge nets sharing a pin, resolve pins
   2 structure.py   net tags for supplies; islands; hub; functional blocks
   3 engine.py      per block: annealing (placement.py), once per side its
                    outside wires may leave on, computed on demand
   4 engine.py      per island: arrange the blocks (ring around the core,
                    the rest beside its partners), improve, polish;
                    several trials, all routed for real, the best kept
   5 engine.py      pack islands; route (router.py), widen corridors where
                    a pin stays open; optional refinement (thorough)
   6 labels.py      net names
   7 metrics.py     independent check + quality figures
render (svg.py)
```

| Module | Responsibility |
|---|---|
| `config.py` | **the** cost model and the effort levels |
| `structure.py` | net tags, islands, hub, functional blocks |
| `shapes.py` | part geometry in grid units: variants (rotations), pins, text zones, net tags |
| `netlist.py` | normalisation, net weights, bundles |
| `estimate.py` | route estimate: wire shapes, trunk model, crossings / conflicts |
| `placement.py` | placement state with incremental cost (block gaps, way out of a block), start, annealing, polish |
| `router.py` | grid A*, hard rules, Steiner trees, rip-up and reroute, recovery |
| `engine.py` | pipeline, block layouts, arrangement, selection by routing, corridor widening, output |
| `labels.py`, `metrics.py` | net names; checking and scoring |

### 3.1 Parts (`shapes.py`)

Everything is in integer grid units. Variants: two-pin symbols have four
rotations, changeover switches two mirror images, image parts 0/±90° (never
180°: upside-down photos are hard to read; only 0° with `rotatable: false`),
boxes one variant. Declared pin sides are binding. Every part has a
**footprint** made of body, terminals, text zones (pin labels of image
parts run *along* their own wire, outside the terminal; name and value of
symbols) and **net tags**: a pin on a tagged net carries a stub (one grid
step, on image parts long enough to clear the pin label) and a tag with the
net name. The router may not cross a tag.

### 3.2 Structure (`structure.py`)

1. **Net tags.** A ground net with 3 or more pins, a positive supply with 5
   or more, and one with 4 that reaches three or more blocks become tags.
   A supply with fewer pins stays a wire: it is usually the power path
   itself (`BATT_VCC`, a converter output) and worth seeing.
2. **Islands**: parts connected by the remaining (signal) wiring.
3. **Hub**: the part whose connection strength (sum over its partners, a
   k-terminal net counting 1/(k-1) per pair) is at least 1.5 times that of
   any other part and that is wired to at least four parts. It becomes a
   block of its own.
4. **Blocks**: islands (without the hub) with fewer than six parts stay one
   block; larger ones are split into modularity communities (greedy
   agglomeration, then single-part moves while modularity improves;
   deterministic, ties by input order).

On test case 14 (`tests/cases/14_hub_blocks.txt`) this yields: the MCU as
hub; pump driver (pump, MOSFET, gate resistor, pull-down, flyback diode);
RS485 transceiver with termination and terminal; rain gauge with its RC
filter; battery, fuel gauge and thermistor; fuse, converter and switch; SD
card; soil sensor. GND, 3V3 and 5V are tags.

### 3.3 Block layouts (`engine._layout_block`)

A block is placed by the annealing of `placement.py` on its own parts only
(analytic start, polish, simulated annealing, polish; see below). The nets
that leave the block get an extra term (`Layout._escape_cost`): from the
best terminal of the net, a straight run to one side of the block's frame
(length, the turns needed to point that way, bodies in the way). So a
layout "facing left" has its outside wires at its left edge. Layouts are
computed on demand per side, `block_restarts` runs each; the distinct
results are kept, best estimate first.

**Annealing (unchanged from the flat engine, now per block).** Variables:
position, variant, and the side of every free box pin (pins on a side
ordered by the height of their partners). Hard constraint: `clearance` free
grid lines between footprints, `block_gap` between parts of different
blocks. Objective: estimated net costs (every connection the cheapest shape
that respects both pin directions; multi-terminal nets the cheaper of a
spanning tree and a single trunk) + crossings, conflicts, side-by-side runs
+ perimeter + rotation. Updated incrementally on every move; a test compares
it with a full recomputation after every move.

### 3.4 Arrangement (`engine._Arranger`)

1. The **core** (the block most strongly wired to the others) is placed
   first, in its natural orientation.
2. **Ring** (single-part core with three or more partner blocks): every
   block wired more strongly to the core than to any other block goes beside
   the core, on the side where the core pins it uses are (majority weighted
   like the connections), in the order of those pins along the side. Each
   block has a target position: opposite its pins, slid so that as many of
   its wires to the core as possible run straight. The positions of a column
   are then solved together: in pin order, with the block gap between
   neighbours, as near the targets as possible, weighted by how strongly
   each block is wired to the core (weighted L1 isotonic regression, pool
   adjacent violators). So an SD card on four SPI wires keeps its straight
   bus, and a light sensor on one wire moves aside. Its layout is the one
   facing the core. A box
   core with free pins: blocks go to the shorter of the left and right
   column, and the box pins are set to face their block *before* anything is
   placed (turning them later would resize the box into its neighbours).
3. **Everything else**, one block at a time (most strongly wired to what is
   placed first): the cheapest spot beside a placed partner, all four sides,
   each with the layout facing that partner, slid to make a wire straight,
   centred or flush.
4. **Improve**: blocks outside the ring move to better spots beside their
   partners, ring blocks slide along their column, while the estimate gets
   cheaper. Then the part-level **polish** (straighten connections, pin
   sides).

**Selection by routing.** The estimate misjudges long wires in large
drawings (on a 19-part project, "improve" lowered the estimate from 903 to 675
while the routed cost rose from 751 to 888). So the arrangement as placed,
as polished and as improved are all routed for real and the cheapest wins;
`arrange_trials` repeat this with the second- and third-best block layouts.
Routing costs about a second for 19 parts, the annealing of blocks is shared
between trials.

### 3.5 Routing (`router.py`)

A* on the 20 px grid over states (node, heading), costs from `Weights`. Hard
rules: no body, no tag, no foreign terminal, no point in front of a foreign
pin; pins are left straight; no shared wire segments; foreign nets share a
point only as a straight, perpendicular crossing; no loops, no four-way
joins. Multi-terminal nets grow as a Steiner tree (next terminal to the
nearest valid tree node). Bundles are routed one after another; a bundle
member pays for every step without a routed mate directly alongside,
foreign wires at 20 px cost, the second field in front of a foreign pin
costs (so pins are not boxed in). Terminals that cannot be connected
trigger conflict recovery (search with high penalties instead of
prohibitions, rip up exactly the blocking nets, route again).

If a pin stays open, the engine **widens corridors**: at a free cut line in
front of the pin, grid lines are inserted (everything beyond the line moves
away). This keeps every alignment exactly and cannot create overlaps. The
last resort spreads all gaps evenly.

### 3.6 Refinement (`thorough`)

Annealing moves are sampled on the finished drawing, the `refine_batch`
with the best estimate are routed for real (quickly: no widening, no
repair), and the best is kept if the routed drawing gets cheaper; it stops
after `refine_patience` rounds without improvement. Parts keep the block
gap, so refinement cannot dissolve the groups.

### 3.7 Effort levels and running time

Everything runs in one process (the flat engine forked parallel processes on
Linux only; on Windows its runs were sequential).

| Level | Runs per block | Moves per part (min. per run) | Trials | Refinement | test case 14 (21 parts) |
|---|---:|---:|---:|---|---:|
| `fast` | 2 | 150 (800) | 2 | – | ~4 s |
| `balanced` | 3 | 300 (2000) | 3 | – | ~11 s |
| `thorough` | 6 | 800 (5000) | 8 | 20 rounds | ~43 s |

Wall-clock times on a four-core Intel i3-10100 under Windows (note:
`time.process_time()` under-reports by about 4x on that machine, measure
wall time). Flat engine on the same machine, `fast`: about 49 s.

Over all examples and test cases (18 inputs; cost with the
same weights, but tagged supplies have no wire cost, so compare crossings
and bends):

| | Cost | Crossings | Bends |
|---|---:|---:|---:|
| flat engine, `fast` | 3305 | 75 | 260 |
| hierarchical, `fast` | 1698 | 24 | 138 |
| hierarchical, `balanced` | 1653 | 17 | 139 |

`balanced` is not uniformly better than `fast` per input (other block
layouts, other trials); the selection by routing keeps both close to their
best. `scripts/layout_report.py` compares any two revisions.

## 4. Quality check (`metrics.py`)

Computed from the emitted geometry, independently of the router: wires are
split into grid edges. Violations: open terminals, shared edges, ambiguous
contacts, wires through parts or net tags, contact with a foreign terminal,
overlapping parts, diagonal wires. Figures: crossings, bends, length, junctions,
straight two-terminal connections, bundle cohesion (share of bundle wire
directly beside a mate), steps beside foreign wires, cost (same weights) and
a readability score from 0 to 100 (for humans only).

`schematic render --report` prints the figures; `scripts/layout_report.py`
compares the working tree against any git revision on all examples and test
cases, with images side by side.

## 5. Tests

| File | Checks |
|---|---|
| `test_estimate.py` | wire shapes, obstacles, pin fronts, fold-backs, crossings / conflicts, trunk model |
| `test_placement.py` | incremental cost equals recomputation (for every move type, also with block gaps and the way out of a block), clearance invariant, pin sides as variables |
| `test_router.py` | hard rules on hand-made examples, honest failure, tree structure |
| `test_engine.py` | every example and test case without violations, on the grid, labels clear, SVG; straight chain; bundles; names without influence; same SVG across processes with different hash seeds |
| `test_structure.py` | supply recognition, hub and blocks from the wiring (also with other part names), buses and local supplies stay wires, tags instead of supply wires, gaps between blocks, blocks on the side of their pins |

The full runs over every input are marked `slow`: `uv run pytest -m "not
slow"` skips them.

## 6. Limits and next steps

* **Buses on stacked modules.** Free box pins only go left or right. A bus
  running past three modules stacked in a column therefore crosses itself
  at every module (`esp32_sensors_bus`: 6 crossings). Free pins on the top
  or bottom of a box would allow the classic drawing: a horizontal bus with
  the modules hanging below it.
* **Estimate vs. routing.** The estimate handles long wires, multi-terminal
  nets and mismatched bundle order poorly; selection by routing
  compensates. A more accurate estimate would let the block moves rely on
  it.
* **Block layouts** sometimes route a wire around a part of the same block
  (a box whose pin faces the wrong way); more runs (`thorough`) help.
* **Tags by name.** Which nets become tags is decided by name
  (`colors.supply_kind`) and terminal count. An explicit choice in the input
  ("draw this net as tags" / "as a wire") would be the next step for the
  cases the rule gets wrong.
* **Signal flow.** `->` / `<-` in the input give directions; they are not
  used yet. They could order blocks (sources left, loads right) where there
  is no hub.

## References

* Ayer, Brunk, Ewing, Reid, Silverman. *An Empirical Distribution Function for Sampling with Incomplete Information.* Ann. Math. Statist. 26(4), 1955 (pool adjacent violators).
* Benkert, Nöllenburg, Uno, Wolff. *Minimizing Intra-Edge Crossings in Wiring Diagrams and Public Transportation Maps.* GD 2006.
* Clauset, Newman, Moore. *Finding community structure in very large networks.* Phys. Rev. E 70, 2004.
* Davidson, Harel. *Drawing Graphs Nicely Using Simulated Annealing.* ACM TOG 15(4), 1996.
* Hanan. *On Steiner's Problem with Rectilinear Distance.* SIAM J. Appl. Math. 14, 1966.
* Hart, Nilsson, Raphael. *A Formal Basis for the Heuristic Determination of Minimum Cost Paths.* IEEE SSC 4(2), 1968.
* Kieffer, Dwyer, Marriott, Wybrow. *HOLA: Human-like Orthogonal Network Layout.* IEEE TVCG 22(1), 2016. <https://ialab.it.monash.edu/~dwyer/papers/hola2015.pdf>
* Kirkpatrick, Gelatt, Vecchi. *Optimization by Simulated Annealing.* Science 220, 1983.
* McMurchie, Ebeling. *PathFinder: A Negotiation-Based Performance-Driven Router for FPGAs.* FPGA 1995.
* Mutzel, Weiskircher. *Bend Minimization in Orthogonal Drawings Using Integer Programming.* <https://www.ac.tuwien.ac.at/files/pub/Mutzel02b.pdf>
* Newman, Girvan. *Finding and evaluating community structure in networks.* Phys. Rev. E 69, 2004.
* Nöllenburg, Wolff. *Drawing and Labeling High-Quality Metro Maps by Mixed-Integer Programming.* IEEE TVCG 17(5), 2011.
* Purchase. *Which Aesthetic Has the Greatest Effect on Human Understanding?* GD 1997.
* Schulze, Spönemann, von Hanxleden. *Drawing Layered Graphs with Port Constraints.* JVLC 2014. <https://rtsys.informatik.uni-kiel.de/~biblio/downloads/papers/jvlc13.pdf>
* Stott, Rodgers, Martinez-Ovando, Walker. *Automatic Metro Map Layout Using Multicriteria Optimization.* IEEE TVCG 2011.
* Sugiyama, Tagawa, Toda. *Methods for Visual Understanding of Hierarchical System Structures.* IEEE SMC 11(2), 1981.
* Tamassia. *On Embedding a Graph in the Grid with the Minimum Number of Bends.* SIAM J. Comput. 16(3), 1987.
* Ware, Purchase, Colpoys, McGill. *Cognitive Measurements of Graph Aesthetics.* Information Visualization 1(2), 2002.
* Wybrow, Marriott, Stuckey. *Orthogonal Connector Routing.* GD 2009.
* *Weave: Verified Netlist-to-Schematic Conversion via Layered Graph Layout.* arXiv:2607.03835. <https://arxiv.org/abs/2607.03835>
* WireViz / filare. <https://pypi.org/project/wireviz>
* netlistsvg. <https://github.com/nturley/netlistsvg>
* d3-hwschematic. <https://www.skypack.dev/view/d3-hwschematic>
* tscircuit autolayout. <https://github.com/tscircuit/autolayout>
* Eclipse ELK Layered. <https://eclipse.dev/elk/blog/posts/2025/25-08-21-layered.html>
