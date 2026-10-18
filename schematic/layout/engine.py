"""Layout engine orchestration: normalise -> analyse -> place -> route -> select.

``layout_schematic`` runs the whole pipeline on a parsed ``Schematic``:

1. input normalisation (nets sharing a pin are one electrical node);
2. semantic analysis (net classes, bundles, roles);
3. for every placement candidate (seed strategy, with / without local
   search), placement + routing on a copy;
4. selection of the candidate with the best *real* quality report
   (no violations first, then the readability score) -- the routed result,
   not the placement estimate, has the final word;
5. rip-up-and-reroute passes on the winner, net labels;
6. if pins are still unconnected: widen the spacing and start again.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from schematic.models import Schematic
from schematic.layout.config import DEFAULT_CONFIG, LayoutConfig
from schematic.layout.labels import place_net_labels
from schematic.layout.metrics import QualityReport, evaluate
from schematic.layout.normalize import merge_shared_pin_nets
from schematic.layout.placer import Placer
from schematic.layout.router import GridRouter
from schematic.layout.semantics import Semantics, analyze


@dataclass
class LayoutResult:
    report: QualityReport
    semantics: Semantics
    strategy: str = ""
    candidates: List[Tuple[str, float, int]] = field(default_factory=list)  # (strategy, score, violations)
    attempts: int = 1                    # > 1 when spacing had to be widened


def _rank(report: QualityReport) -> Tuple[int, float]:
    return (report.violation_count, -report.readability_score)


def layout_schematic(schematic: Schematic, config: Optional[LayoutConfig] = None) -> LayoutResult:
    """Runs the full pipeline in place and returns the final quality report.

    Every placement candidate is routed once (first pass); the best candidate
    by real readability score then gets the rip-up-and-reroute passes.  If the
    result still leaves pins unconnected, the corridors were too tight for the
    router: spacing is widened and the whole pipeline runs again (routing
    feedback on placement, connectivity first)."""
    cfg = config or DEFAULT_CONFIG
    merge_shared_pin_nets(schematic)
    sem = analyze(schematic)
    original = copy.deepcopy(schematic)
    result: Optional[LayoutResult] = None
    work_cfg = cfg
    for attempt in range(1 + cfg.spacing_retries):
        if attempt:
            work_cfg = copy.deepcopy(work_cfg)
            work_cfg.spacing.min_h_gap += 60.0
            work_cfg.spacing.min_v_gap += 40.0
            schematic.__dict__.update(copy.deepcopy(original).__dict__)
        result = _layout_once(schematic, work_cfg, sem)
        result.attempts = attempt + 1
        unrouted = result.report.violations.get("unrouted_pins", 0) + result.report.violations.get(
            "disconnected_nets", 0)
        if not unrouted:
            break
    assert result is not None
    return result


def _reroute_candidate(schematic: Schematic, router: GridRouter, report: QualityReport,
                       sem: Semantics, cfg: LayoutConfig) -> Tuple[Schematic, QualityReport]:
    """Gives a candidate the same rip-up treatment before it is compared."""
    if not cfg.reroute_passes:
        return schematic, report
    before = copy.deepcopy(schematic)
    router.improve(cfg.reroute_passes)
    router.emit()
    if cfg.net_labels:
        place_net_labels(schematic)
    improved = evaluate(schematic, sem, cfg.score)
    if _rank(improved) <= _rank(report):
        return schematic, improved
    return before, report


def _orientation_better(candidate: QualityReport, base: QualityReport,
                        margin: float) -> bool:
    if candidate.violation_count != base.violation_count:
        return candidate.violation_count < base.violation_count
    return candidate.readability_score > base.readability_score + margin


def _layout_once(schematic: Schematic, cfg: LayoutConfig, sem: Semantics) -> LayoutResult:
    best: Optional[Tuple[QualityReport, Schematic, str, GridRouter]] = None
    tried: List[Tuple[str, float, int]] = []
    for strategy, search in cfg.candidates:
        label = strategy + ("+search" if search else "")
        work = copy.deepcopy(schematic)
        Placer(work, semantics=sem, config=cfg).place(strategy, search=search)
        router = GridRouter(work, semantics=sem, config=cfg)
        router.route_initial()
        router.emit()
        if cfg.net_labels:
            place_net_labels(work)
        report = evaluate(work, sem, cfg.score)
        tried.append((label, report.readability_score, report.violation_count))
        if best is None or _rank(report) < _rank(best[0]):
            best = (report, work, label, router)
    assert best is not None
    report, work, label, router = best

    # Compare locally refined orientations against the *same* rip-up-processed
    # drawing.  Without this, a marginal first-pass gain could replace a layout
    # whose later rip-up would have been better.
    work, report = _reroute_candidate(work, router, report, sem, cfg)

    # The orientation heuristic only initialises the axis.  For parts whose
    # two connection directions are ambiguous, route the other axis on the
    # winning drawing (keeping all positions) and let the real score decide.
    base_label = label
    base_strategy = label.split("+")[0]
    probe = Placer(copy.deepcopy(work), semantics=sem, config=cfg)
    for cid, vertical in probe.orientation_variants():
        variant = copy.deepcopy(work)
        variant.wires.clear()
        variant.junctions.clear()
        variant.labels.clear()
        Placer(variant, semantics=sem, config=cfg,
               orientation_overrides={cid: vertical},
               keep_positions=True).place(base_strategy, search=False)
        vrouter = GridRouter(variant, semantics=sem, config=cfg)
        vrouter.route_initial()
        vrouter.emit()
        if cfg.net_labels:
            place_net_labels(variant)
        vreport = evaluate(variant, sem, cfg.score)
        if (_rank(vreport) < _rank(report)
                or vreport.readability_score >= report.readability_score - cfg.orientation_reroute_margin):
            variant, vreport = _reroute_candidate(variant, vrouter, vreport, sem, cfg)
        vlabel = f"{base_label}+orient:{cid}"
        tried.append((vlabel, vreport.readability_score, vreport.violation_count))
        if _orientation_better(vreport, report, cfg.orientation_accept_margin):
            report, work, label, router = vreport, variant, vlabel, vrouter

    schematic.__dict__.update(work.__dict__)
    return LayoutResult(report=report, semantics=sem, strategy=label, candidates=tried)
