# Layout engine

How the engine decides where parts go and how wires run, why it works that
way, and how to change it without breaking it.

## 1. Goal: readability

The tool documents wiring. A reader must be able to follow every wire from
pin to pin and recognise functional groups. The drawing does not need to be
compact.

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

The most important design rule follows from this: **there is exactly one
cost model** (`config.Weights`). The same numbers price a placement (through
the route estimate), steer the router (A* step costs) and score the result.
When all three stages agree on what "better" means, an improvement in one
place cannot cause a regression in another that the model does not see.

Default weights: crossing 12, bend 4, length 0.4 per grid step (20 px),
junction 0.5, step beside a foreign wire 0.5, bundle step without a mate
0.4. So a bend costs as much as 200 px of detour, a crossing as much as three
bends, a wire across 100 grid steps as much as four bends. Length started at
0.2; side by side, those drawings were too wide and had too many long wires
across the picture. Nets with many terminals (5 or more: supply, ground,
large buses) count bends and length at half weight: they have to reach
everything, the signals should stay clean. This classification follows from
the number of terminals alone.

**Names have no influence.** Net, pin and part names are used only for text
width (space) and colour (`colors.py`: ground grey/black, supplies red). A
test renames everything and requires identical geometry
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

**Chosen:** VLSI-style two-stage placement (analytic globally, annealing
locally) on an honest estimate that agrees with the router; an A* router
with the same weights; several independent runs in parallel, all routed for
real. Reasons: the objective is free (no columns imposed), every decision
(position, rotation, pin side) is a variable rather than a rule, and it needs
no dependencies (pure Python).

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
   1 netlist.py     merge nets sharing a pin, resolve pins,
                    net weight from the number of terminals
   2 placement.py   per sub-circuit and run:
                      analytic start → polish → simulated annealing
                      → polish   (runs in parallel processes)
   3 engine.py      route every run for real (router.py); where pins stay
                    open, widen corridors and route again; the cheapest
                    drawing wins and is refined against the real objective
   4 labels.py      net names
   5 metrics.py     independent check + quality figures
render (svg.py)
```

| Module | Responsibility |
|---|---|
| `config.py` | **the** cost model and the effort levels |
| `shapes.py` | part geometry in grid units: variants (rotations), pins, text zones |
| `netlist.py` | normalisation, net weights, bundles, sub-circuits |
| `estimate.py` | route estimate: wire shapes, trunk model, crossings / conflicts |
| `placement.py` | placement state with incremental cost, start, annealing, polish |
| `router.py` | grid A*, hard rules, Steiner trees, rip-up and reroute, recovery |
| `engine.py` | pipeline, candidate selection, corridor widening, refinement, output |
| `labels.py`, `metrics.py` | net names; checking and scoring |

### 3.1 Parts (`shapes.py`)

Everything is in integer grid units. Variants: two-pin symbols have four
rotations, changeover switches two mirror images, image parts 0/±90° (never
180°: upside-down photos are hard to read; only 0° with `rotatable: false`),
boxes one variant. Declared pin sides are binding. Every part has a
**footprint** made of body, terminals and text zones (pin labels of image
parts run *along* their own wire, outside the terminal; name and value of
symbols).

### 3.2 Placement (`placement.py`)

**Variables:** position, variant, and **the side of every free box pin**.
Pins on one side are ordered by the height of their partners (barycentre,
which keeps a bundle from crossing itself); the side itself is an
optimisation variable (annealing moves: flip a pin, flip a bundle, re-orient
a box).

**Hard constraint:** at least `clearance` free grid lines between
footprints (default 2: enough for the terminal fields of both sides).

**Objective:** sum of the estimated net costs + crossings, conflicts and
side-by-side runs of unrelated nets + perimeter + rotation cost for image
parts. Bundle mates may (and should) run side by side; sharing the same line
costs only a quarter of a conflict (the router puts them on neighbouring
tracks).

**Estimate (`estimate.py`):**

* Every pin-to-pin connection gets the cheapest shape that respects both pin
  directions (straight, L, Z/U, 3 or 4 bends), with free coordinates taken
  from midpoints and the edges of parts in the way.
* Obstacles are part bodies **and** the grid points of foreign terminals and
  the points directly in front of them: the same points the router blocks.
  So estimate and router agree on what is allowed.
* Nets with ≥ 3 terminals: the cheaper of a spanning tree (MST) and a
  **single-trunk Steiner tree** (one straight trunk, branches straight, as an
  L, or as a U around their own part). Without the trunk model the estimate
  punishes exactly the tidy bus arrangement.
* Between two nets, real crossings and **conflicts** count: the same line, or
  a wire ending on a foreign wire. The router has to cross or detour there,
  so a conflict costs as much as a crossing.

The cost is updated **incrementally** on every move (only nets near the
moved parts). A test compares it with a full recomputation after every move;
a difference would be a bug, not noise.

**One run:**

1. *Analytic start*: quadratic wire length with pin directions; every
   connection pulls the partner's terminal to a point in front of its own pin
   (both ways). Gauss-Seidel sweeps; every 10 sweeps the best variant per part
   and provisional sides for free pins. Then legalisation (next free spot in
   connection order). A constructive start (part by part at the cheapest
   spot) measured clearly worse and was removed.
2. *Polish* (steepest descent: straighten every connection, ±1 grid step,
   variant, pin side).
3. *Simulated annealing* over structured moves: shift; align a connection
   (also "put it in front of the pin", and a whole straight-wired chain
   together); move next to a neighbour; swap; rotate; shift a whole side of
   the drawing; pin sides (single, as a bundle, a box together with its
   partner boxes, since otherwise both ends of a bundle stay the wrong way
   round because each half alone would be worse). Start temperature from the
   mean uphill step, geometric cooling, the best state is kept.
4. *Polish* again, so every connection that can be made straight by aligning
   is.

### 3.3 Routing (`router.py`)

A* on the 20 px grid over states (node, heading), costs from `Weights`. Hard
rules: no body, no foreign terminal, no point in front of a foreign pin; pins
are left straight; no shared wire segments; foreign nets share a point only
as a straight, perpendicular crossing; no loops, no four-way joins.
Multi-terminal nets grow as a Steiner tree (next terminal to the nearest
valid tree node). Bundles are routed one after another; a bundle member pays
for every step without a routed mate directly alongside, foreign wires at
20 px cost, the second field in front of a foreign pin costs (so pins are not
boxed in). Terminals that cannot be connected trigger conflict recovery
(search with high penalties instead of prohibitions, rip up exactly the
blocking nets, route again), after the rip-up passes, as long as a pin is
open.

If a pin stays open, the engine **widens corridors**: at a free cut line in
front of the pin, grid lines are inserted (everything beyond the line moves
away). This keeps every alignment exactly and cannot create overlaps. The
last resort spreads all gaps evenly.

### 3.4 Selection

`restarts` runs with different seeds, in parallel processes (`fork`; results
are collected in a fixed order, so the outcome equals a sequential run).
**Every** run is routed for real and scored with the report's figures; the
cheapest drawing wins. Deterministic: fixed seeds, input order rather than
hash or name order.

Why route them all: the estimate predicts the routed quality only roughly.
Example `03_dense_network`, 12 runs: the lowest estimate (338) routes to 454,
an estimate of 415 routes to 350. The differences come mostly from
multi-terminal nets (trunk model vs. the actual Steiner tree) and from
bundles whose pin order does not match at both ends. Routing, on the other
hand, costs only about a tenth of a placement run (dense: 0.8 s vs. 11 s).

It also follows that **many shorter runs beat a few long ones.** Measured
over all 19 inputs (sum, same weights, `balanced` budget):

| Variant | Cost | Crossings | Bends | Time |
|---|---:|---:|---:|---:|
| 6 × 1000 moves, best 3 routed | 4191 | 112 | 328 | 173 s |
| 6 × 1000, all routed | 4147 | 110 | 314 | 184 s |
| 14 × 700, all routed | 4047 | 102 | 308 | 287 s |
| **14 × 500, all routed (current)** | **4043** | **102** | **296** | 210 s |

Single cases vary a lot between variants (seed lottery); the sum is what is
meaningful.

### 3.5 Refining the finished drawing

The estimate steers the annealing, but the result is measured on the routed
drawing. So the winner is finally improved **against the real objective**
(`engine._refine`):

1. On the winning placement, annealing moves are sampled; per round the
   `refine_batch` with the best estimate are **routed for real** (in
   parallel) and scored with the report figures.
2. The best change is kept if the finished drawing gets cheaper; otherwise
   the round counts as unsuccessful. After `refine_patience` unsuccessful
   rounds (or `refine_rounds`) it stops.
3. Candidates are routed *quickly*: without corridor widening and without
   conflict repair. A candidate with an open pin loses anyway, and if
   everything routes directly, the result is identical to full routing. This
   halves the time (dense: 47 → 29 s), because a few hard-to-route candidates
   would otherwise hold up the whole round.

Effect (sum over 19 inputs):

| Level | without → with refinement | Crossings | Time |
|---|---:|---:|---:|
| `fast` | 4962 → 4538 | 131 → 121 | 42 → 55 s |
| `balanced` | 4043 → 3808 | 102 → 90 | 210 → 299 s |

| Level | Runs | Moves per part | Refinement (rounds / per round / patience) | 19 parts |
|---|---:|---:|---:|---:|
| `fast` | 2 | 300 | 4 / 7 / 1 | ~20 s |
| `balanced` | 14 | 500 | 15 / 14 / 3 | ~2 min |
| `thorough` | 28 | 1500 | 60 / 14 / 6 | ~9 min |

`thorough` pays off clearly for dense circuits (compared to `balanced`):
`03_dense_network` 286 → 240, `13_functional_regions` 281 → 216,
`example_large` 689 → 599.

## 4. Quality check (`metrics.py`)

Computed from the emitted geometry, independently of the router: wires are
split into grid edges. Violations: open terminals, shared edges, ambiguous
contacts, wires through parts, contact with a foreign terminal, overlapping
parts, diagonal wires. Figures: crossings, bends, length, junctions,
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
| `test_placement.py` | incremental cost equals recomputation (for every move type), clearance invariant, pin sides as variables |
| `test_router.py` | hard rules on hand-made examples, honest failure, tree structure |
| `test_engine.py` | every example and test case without violations, on the grid, labels clear, SVG; straight chain; bundles; names without influence; same SVG across processes with different hash seeds |

The full runs over every input are marked `slow`: `uv run pytest -m "not
slow"` skips them.

## 6. Limits and next steps

* **Running time.** Pure Python. `fast` takes about 10–20 s for 19 parts,
  `balanced` ~2 min, `thorough` ~9 min. The live editor uses `fast` by
  default.
* **Estimate vs. routing.** The estimate handles multi-terminal nets and
  bundles with mismatched pin order poorly; selection compensates by routing
  every run and by refinement. A more accurate estimate would improve the
  annealing itself and shorten refinement.
* **Search noise.** Annealing is stochastic (deterministic per seed); single
  runs vary by ±15–20 % in cost. More runs (`thorough`) smooth this out.
* **Exact fine alignment.** For a fixed coarse arrangement, "which
  connections can be straight at the same time" is a small integer problem;
  OR-Tools CP-SAT could solve it exactly (a new dependency, so not done).
* **Supplies as symbols.** Ground and supplies are drawn as wires (this is
  wiring documentation). An optional mode with supply symbols instead of
  wires would relieve large drawings a lot; it would have to be an explicit
  choice in the input (per net), not a naming rule.
* **Rip-up** judges each net on its own; a negotiating variant (PathFinder
  with history costs) would be more robust in very dense corridors.

## References

* Benkert, Nöllenburg, Uno, Wolff. *Minimizing Intra-Edge Crossings in Wiring Diagrams and Public Transportation Maps.* GD 2006.
* Davidson, Harel. *Drawing Graphs Nicely Using Simulated Annealing.* ACM TOG 15(4), 1996.
* Hanan. *On Steiner's Problem with Rectilinear Distance.* SIAM J. Appl. Math. 14, 1966.
* Hart, Nilsson, Raphael. *A Formal Basis for the Heuristic Determination of Minimum Cost Paths.* IEEE SSC 4(2), 1968.
* Kieffer, Dwyer, Marriott, Wybrow. *HOLA: Human-like Orthogonal Network Layout.* IEEE TVCG 22(1), 2016. <https://ialab.it.monash.edu/~dwyer/papers/hola2015.pdf>
* Kirkpatrick, Gelatt, Vecchi. *Optimization by Simulated Annealing.* Science 220, 1983.
* McMurchie, Ebeling. *PathFinder: A Negotiation-Based Performance-Driven Router for FPGAs.* FPGA 1995.
* Mutzel, Weiskircher. *Bend Minimization in Orthogonal Drawings Using Integer Programming.* <https://www.ac.tuwien.ac.at/files/pub/Mutzel02b.pdf>
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
