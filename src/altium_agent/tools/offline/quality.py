# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/quality.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Quality gates and placement quality (kicad-mcp-pro parity), driven by Altium data.

Every gate returns {"gate", "verdict": PASS|WARN|FAIL, "blockers", "warnings", ...} so agents can chain them.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Annotated

from pydantic import Field

from ...analysis.consistency import compare
from ...analysis.copper import ratsnest
from ...analysis.dfm import PROFILES, estimate_cost, run_dfm
from ...analysis.erc import run_erc
from ...offline.context import get_settings
from ...analysis.engines.geometry import Box, boxes_overlap_pairs, text_extent
from ...analysis.engines.placement_rules import PlacementThresholds, critique_placement
from ...offline.errors import InvalidArgument
from ...offline.files.pcbdoc import NONE16, PcbDocument
from ...offline.context import ToolClass
from ...offline.context import output_dir_for, project_path_or_none, resolve_project
from ...offline.context import dfm_profile as session_profile, set_dfm_profile
from .common import PcbArg, ProjectArg, model_for, pcb_for
from .registry import tool

ProfileArg = Annotated[str | None, Field(description="DFM profile; defaults to the session/ALTIUM_AGENT_DFM_PROFILE profile.")]


def _profile(name: str | None) -> str:
    p = name or session_profile() or get_settings().dfm_profile
    if p not in PROFILES:
        raise InvalidArgument(f"unknown DFM profile {p}", hint=f"available: {sorted(PROFILES)}")
    return p


def _gate(name: str, blockers: list[str], warnings: list[str], **extra) -> dict:
    verdict = "FAIL" if blockers else ("WARN" if warnings else "PASS")
    _record_trend(name, verdict, extra.pop("project", None))
    return {"gate": name, "verdict": verdict, "blockers": blockers, "warnings": warnings, **extra,
            "scope": OFFLINE_GATE_SCOPE}


#: Altium Agent: a gate verdict is an offline pre-check, never a release decision.
OFFLINE_GATE_SCOPE = ("Offline pre-check from the design files. PASS means these checks found no "
                      "blocker, not that the design is manufacturing-ready: run Altium's own ERC "
                      "and DRC (proj_run_erc, pcb_run_drc) and review before any fabrication output.")


def _record_trend(gate: str, verdict: str, project: str | None) -> None:
    try:
        out = output_dir_for(project_path_or_none(project), "gates")
        path = out / "gate_history.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "gate": gate, "verdict": verdict}) + "\n")
    except Exception:  # noqa: BLE001 - trend history is best effort
        pass


# ------------------------------------------------------------------ footprint geometry


def footprint_map(doc: PcbDocument) -> dict[str, dict]:
    """Altium components in the FootprintMap shape the ported placement engines expect."""
    ext: dict[int, list[float]] = {}
    nets: dict[int, set[str]] = defaultdict(set)

    def grow(ci: int, x0: float, y0: float, x1: float, y1: float) -> None:
        e = ext.setdefault(ci, [x0, y0, x1, y1])
        e[0], e[1], e[2], e[3] = min(e[0], x0), min(e[1], y0), max(e[2], x1), max(e[3], y1)

    for p in doc.pads:
        if p.component != NONE16:
            r = max(p.size_x, p.size_y) / 2
            grow(p.component, p.x - r, p.y - r, p.x + r, p.y + r)
            if p.net != NONE16:
                nets[p.component].add(doc.net_name(p.net))
    for t in doc.tracks:
        if t.component != NONE16 and t.layer in (33, 34):
            grow(t.component, min(t.x1, t.x2), min(t.y1, t.y2), max(t.x1, t.x2), max(t.y1, t.y2))
    out = {}
    for c in doc.components:
        e = ext.get(c.index, [c.x_mm - 1, c.y_mm - 1, c.x_mm + 1, c.y_mm + 1])
        out[c.designator] = {"ref": c.designator, "x_mm": (e[0] + e[2]) / 2, "y_mm": (e[1] + e[3]) / 2,
                             "origin_mm": [c.x_mm, c.y_mm], "width_mm": e[2] - e[0], "height_mm": e[3] - e[1],
                             "rotation": c.rotation, "value": c.comment, "footprint": c.footprint, "side": c.side,
                             "net_names": sorted(nets.get(c.index, ()))}
    return out


def _visual_findings(doc: PcbDocument) -> list[dict]:
    fps = footprint_map(doc)
    findings = []
    bbox = doc.outline_bbox()
    outline = [(p["x"], p["y"]) for p in doc.outline_mm]
    from ...analysis.copper import _point_in_poly

    for ref, fp in fps.items():
        corners = [(fp["x_mm"] + sx * fp["width_mm"] / 2, fp["y_mm"] + sy * fp["height_mm"] / 2) for sx in (-1, 1) for sy in (-1, 1)]
        if outline and not all(_point_in_poly(x, y, outline) for x, y in corners):
            if bbox and not (bbox["min_x"] <= fp["x_mm"] <= bbox["max_x"] and bbox["min_y"] <= fp["y_mm"] <= bbox["max_y"]):
                findings.append({"level": "FAIL", "code": "offboard_component", "ref": ref, "message": f"{ref} is outside the board"})
            else:
                findings.append({"level": "WARN", "code": "overhangs_outline", "ref": ref,
                                 "message": f"{ref} body extends past the board outline (fine for edge connectors)"})
    for side in ("top", "bottom"):
        boxes = [(r, Box.from_center(f["x_mm"], f["y_mm"], f["width_mm"], f["height_mm"])) for r, f in fps.items() if f["side"] == side]
        for a, b, area in boxes_overlap_pairs(boxes):
            if area >= 1.0:
                findings.append({"level": "WARN", "code": "body_overlap", "ref": f"{a},{b}",
                                 "message": f"{a} and {b} ({side}) overlap ~{area:.1f} mm2"})
    for layer in (33, 34):
        boxes = []
        for t in doc.texts:
            if t.is_designator and t.layer == layer and t.text:
                w, h = text_extent(t.text, t.height or 1.0)
                if round(t.rotation) % 180 == 90:
                    w, h = h, w
                boxes.append((t.text, Box(t.x, t.y, t.x + w, t.y + h)))
        for a, b, area in boxes_overlap_pairs(boxes):
            if area >= 0.2:
                findings.append({"level": "WARN", "code": "designator_overlap", "ref": f"{a},{b}",
                                 "message": f"designators {a} and {b} overlap on {doc.layer_label(layer)}"})
    return findings


# ------------------------------------------------------------------ placement


@tool(ToolClass.READ, "placement", "Critique placement")
def pcb_critique_placement(project: ProjectArg = None, pcb: PcbArg = None,
                           decap_max_distance_mm: float = 5.0, crystal_max_distance_mm: float = 8.0,
                           analog_digital_min_separation_mm: float = 5.0) -> dict:
    """Design-quality placement findings: decoupling caps far from ICs, crystals far from their IC, SMPS hot
    loops too large, analog/digital mixing."""
    doc = pcb_for(project, pcb)
    th = PlacementThresholds(decap_max_distance_mm=decap_max_distance_mm, crystal_max_distance_mm=crystal_max_distance_mm,
                             analog_digital_min_separation_mm=analog_digital_min_separation_mm)
    findings = [f.to_dict() for f in critique_placement(footprint_map(doc), th)]
    return {"findings": findings, "count": len(findings)}


def _hpwl(doc: PcbDocument) -> float:
    fps = footprint_map(doc)
    total = 0.0
    for net, pads in doc.pad_netlist().items():
        refs = {p.rsplit("-", 1)[0] for p in pads}
        pts = [(fps[r]["x_mm"], fps[r]["y_mm"]) for r in refs if r in fps]
        if len(pts) > 1 and len(pts) < 50:  # skip big power nets
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


@tool(ToolClass.READ, "placement", "Score placement")
def pcb_score_placement(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """0-100 placement score from critique rules, overlaps, off-board parts and signal wirelength (HPWL),
    with the reasons behind every deduction."""
    doc = pcb_for(project, pcb)
    crit = [f.to_dict() for f in critique_placement(footprint_map(doc))]
    vis = _visual_findings(doc)
    # Deductions are capped per rule so one repeated issue cannot zero the score on its own.
    caps = {"critique": 25.0, "offboard_component": 40.0, "overhangs_outline": 10.0, "body_overlap": 25.0,
            "designator_overlap": 10.0}
    spent: dict[str, float] = defaultdict(float)
    reasons = []
    for f in crit:
        d = {"error": 8, "warning": 4}.get(f.get("severity", ""), 1)
        key = f"critique:{f.get('rule_id', '')}"
        d = min(d, max(0.0, caps["critique"] - spent[key]))
        spent[key] += d
        reasons.append(f"-{d:g}: {f.get('message', '')}")
    for f in vis:
        d = {"FAIL": 15, "WARN": 2}.get(f["level"], 0)
        d = min(d, max(0.0, caps.get(f["code"], 20.0) - spent[f["code"]]))
        spent[f["code"]] += d
        reasons.append(f"-{d:g}: {f['message']}")
    score = 100.0 - sum(spent.values())
    return {"score": max(0, round(score, 1)), "signal_hpwl_mm": round(_hpwl(doc), 1), "deductions": reasons[:100],
            "critique_findings": len(crit), "visual_findings": len(vis)}


@tool(ToolClass.READ, "placement", "Placement quality gate")
def pcb_placement_quality_gate(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """PASS only when no part is off the board and no same-side bodies overlap; critique findings warn."""
    doc = pcb_for(project, pcb)
    vis = _visual_findings(doc)
    crit = [f.to_dict() for f in critique_placement(footprint_map(doc))]
    blockers = [f["message"] for f in vis if f["level"] == "FAIL" or f["code"] == "body_overlap"]
    warnings = [f["message"] for f in vis if f not in blockers and f["level"] == "WARN" and f["code"] != "body_overlap"]
    warnings += [f.get("message", "") for f in crit]
    return _gate("placement", blockers[:50], warnings[:50], project=project)


@tool(ToolClass.READ, "placement", "Headless visual QA")
def pcb_visual_qa(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Readability QA without rendering: off-board / overhanging parts, same-side body overlap, overlapping
    designators on the silkscreen."""
    doc = pcb_for(project, pcb)
    f = _visual_findings(doc)
    status = "FAIL" if any(x["level"] == "FAIL" for x in f) else ("WARN" if f else "PASS")
    return {"status": status, "findings": f[:200], "count": len(f)}


# ------------------------------------------------------------------ DFM


@tool(ToolClass.READ, "manufacturing", "Load a fab profile")
def dfm_load_manufacturer_profile(profile: Annotated[str, Field(description="Profile name, see dfm_profiles.")]) -> dict:
    """Make a fabricator profile the session default for DFM checks and gates."""
    if profile not in PROFILES:
        raise InvalidArgument(f"unknown profile {profile}", hint=f"available: {sorted(PROFILES)}")
    set_dfm_profile(profile)
    return {"active_profile": profile, **PROFILES[profile]}


@tool(ToolClass.READ, "manufacturing", "Manufacturer DFM check")
def dfm_run_manufacturer_check(project: ProjectArg = None, pcb: PcbArg = None, profile: ProfileArg = None) -> dict:
    """DFM against the active (or given) fab profile, including silkscreen height and copper-to-edge checks."""
    return run_dfm(pcb_for(project, pcb), _profile(profile))


@tool(ToolClass.READ, "manufacturing", "Estimate fabrication cost")
def dfm_calculate_manufacturing_cost(quantity: Annotated[int, Field(ge=1, le=1_000_000)] = 10,
                                     project: ProjectArg = None, pcb: PcbArg = None, profile: ProfileArg = None) -> dict:
    """Rough bare-board cost from area, copper layers and drill hits (profile pricing tables)."""
    return estimate_cost(pcb_for(project, pcb), _profile(profile), quantity)


@tool(ToolClass.READ, "manufacturing", "Manufacturing quality gate")
def manufacturing_quality_gate(project: ProjectArg = None, profile: ProfileArg = None) -> dict:
    """Manufacturing readiness: DFM errors, unrouted connections, schematic/PCB sync and placement problems."""
    model = model_for(project)
    doc = model.pcb()
    if doc is None:
        raise InvalidArgument("project has no PcbDoc")
    prof = _profile(profile)
    dfm = run_dfm(doc, prof, max_examples=3)
    rn = ratsnest(doc)
    sync = compare(model, doc, limit=10)
    vis = _visual_findings(doc)
    blockers, warnings = [], []
    if not dfm["passed"]:
        blockers.append(f"{dfm['summary']['errors']} DFM error(s) for {prof}")
    if dfm["summary"]["warnings"]:
        warnings.append(f"{dfm['summary']['warnings']} DFM warning(s)")
    if rn["unrouted_connections"]:
        blockers.append(f"{rn['unrouted_connections']} unrouted connection(s) on {rn['nets_with_unrouted_connections']} net(s)")
    if not sync["in_sync"]:
        warnings.append("schematic and PCB differ (see sch_pcb_consistency)")
    if any(f["level"] == "FAIL" for f in vis):
        blockers.append("component(s) off the board")
    return _gate("manufacturing", blockers, warnings, project=project, profile=prof,
                 dfm_findings=[f["message"] for f in dfm["findings"]], routing_completion_pct=rn["routing_completion_pct"])


# ------------------------------------------------------------------ gates


@tool(ToolClass.READ, "validation", "Stack-up consistency gate")
def pcb_stackup_consistency_gate(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Copper used on layers missing from the stack-up, asymmetric (warp-prone) stacks, undefined dielectrics."""
    doc = pcb_for(project, pcb)
    blockers, warnings = [], []
    used = {t.layer for t in doc.tracks if t.layer <= 32} | {p.layer for p in doc.pads if p.layer <= 32}
    for lyr in sorted(used):
        if doc.copper_for_legacy(lyr) is None:
            blockers.append(f"copper on {doc.layer_label(lyr)} which is not in the layer stack")
    copper = doc.copper_layers
    if len(copper) % 2 and len(copper) > 1:
        warnings.append(f"odd copper layer count ({len(copper)})")
    th = [c.copper_thickness_mm or 0 for c in copper]
    if th and any(abs(a - b) > 1e-3 for a, b in zip(th, reversed(th))):
        warnings.append("copper thicknesses are not symmetric about the board centre (warp risk)")
    diel = [s for s in doc.stack if s.kind == "dielectric"]
    if any(not s.dielectric_height_mm or not s.dielectric_constant for s in diel):
        warnings.append("some dielectric layers lack thickness or Dk (impedance tools will be inaccurate)")
    return _gate("stackup", blockers, warnings, project=project, copper_layers=[c.name for c in copper],
                 board_thickness_mm=doc.board_thickness_mm)


@tool(ToolClass.READ, "validation", "Schematic quality gate")
def schematic_quality_gate(project: ProjectArg = None) -> dict:
    """Schematic clean enough to proceed? Blocks on annotation, duplicate designators, output conflicts and
    missing footprints; warns on unconnected pins and floating labels."""
    model = model_for(project)
    erc = run_erc(model, max_per_check=5)
    by = erc["summary"]["by_check"]
    blockers = [f"{n} {k}" for k, n in by.items() if k in ("unannotated", "duplicate_designator", "output_conflict", "document")]
    blockers += [f"{by['missing_footprint']} missing_footprint"] if by.get("missing_footprint") else []
    warnings = [f"{n} {k}" for k, n in by.items() if k in ("unconnected_pin", "floating_net_label", "single_pin_net", "undriven_net")]
    return _gate("schematic", blockers, warnings, project=project, summary=erc["summary"])


@tool(ToolClass.READ, "validation", "Schematic connectivity gate")
def schematic_connectivity_gate(project: ProjectArg = None) -> dict:
    """Is the hierarchy electrically meaningful? Sheet entries without matching ports, missing child sheets,
    nets confined to a single pin, components with no connections."""
    model = model_for(project)
    nl = model.netlist()
    blockers, warnings = [], []
    files = {s.path.name.lower(): s for s in model.sheets}
    for s in model.sheets:
        for sym in s.sheet_symbols:
            from pathlib import Path as _P

            child = files.get(_P(sym.file_name.replace("\\", "/")).name.lower())
            if child is None:
                warnings.append(f"sheet symbol {sym.name}: child sheet {sym.file_name} not in project")
                continue
            ports = {p.text for p in child.labels_of("port")}
            for e in sym.entries:
                if e.text not in ports:
                    blockers.append(f"{sym.name}: entry {e.text} has no port in {child.path.name}")
    connected = {m.designator for n in nl.nets for m in n.members}
    isolated = [c.designator for c in model.components() if c.pin_count and c.designator not in connected]
    if isolated:
        warnings.append(f"{len(isolated)} component(s) with no connected pins: {', '.join(isolated[:15])}")
    warnings += nl.warnings[:5]
    return _gate("schematic_connectivity", blockers[:50], warnings, project=project, nets=len(nl.nets))


@tool(ToolClass.READ, "validation", "PCB quality gate")
def pcb_quality_gate(project: ProjectArg = None, pcb: PcbArg = None, profile: ProfileArg = None) -> dict:
    """PCB physically clean enough? Unrouted connections, DFM errors, off-board parts, stack-up problems."""
    doc = pcb_for(project, pcb)
    rn = ratsnest(doc)
    dfm = run_dfm(doc, _profile(profile), max_examples=3)
    stack = pcb_stackup_consistency_gate(project, pcb)
    vis = _visual_findings(doc)
    blockers, warnings = [], []
    if rn["unrouted_connections"]:
        blockers.append(f"{rn['unrouted_connections']} unrouted connection(s)")
    if not dfm["passed"]:
        blockers.append(f"{dfm['summary']['errors']} DFM error(s)")
    blockers += stack["blockers"]
    warnings += stack["warnings"]
    blockers += [f["message"] for f in vis if f["level"] == "FAIL"]
    warnings += [f["message"] for f in vis if f["level"] == "WARN"][:20]
    return _gate("pcb", blockers, warnings, project=project, routing_completion_pct=rn["routing_completion_pct"])


@tool(ToolClass.READ, "validation", "Schematic-to-PCB transfer gate")
def pcb_transfer_quality_gate(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Did the schematic transfer cleanly onto the PCB? (components, footprints, pad nets)."""
    model = model_for(project)
    sync = compare(model, pcb_for(project, pcb), limit=20)
    blockers = []
    if sync["components_only_in_schematic"]:
        blockers.append(f"missing on PCB: {', '.join(sync['components_only_in_schematic'][:15])}")
    if sync["footprint_mismatches"]:
        blockers.append(f"{len(sync['footprint_mismatches'])} footprint mismatch(es)")
    if sync["nets_split_or_unassigned_in_pcb"] or sync["pcb_nets_merging_schematic_nets"]:
        blockers.append("pad nets differ from the schematic")
    warnings = [f"extra on PCB: {', '.join(sync['components_only_in_pcb'][:15])}"] if sync["components_only_in_pcb"] else []
    if sync["netlist_warnings"]:
        warnings += sync["netlist_warnings"][:3]
        blockers, warnings = [], warnings + blockers  # offline netlist not authoritative for these designs
    return _gate("transfer", blockers, warnings, project=project, counts=sync["counts"])


@tool(ToolClass.READ, "validation", "Validate design (offline ERC + PCB summary)")
def validate_design(project: ProjectArg = None) -> dict:
    """Offline ERC and PCB gate summary. Altium's own compile and DRC are separate live tools
    (proj_run_erc, pcb_run_drc); run them before trusting a PASS."""
    out = {"schematic": schematic_quality_gate(project), "pcb": None}
    model = model_for(project)
    if model.pcb():
        out["pcb"] = pcb_quality_gate(project)
    verdicts = [v["verdict"] for v in (out["schematic"], out["pcb"]) if v]
    out["verdict"] = "FAIL" if "FAIL" in verdicts else ("WARN" if "WARN" in verdicts else "PASS")
    out["scope"] = OFFLINE_GATE_SCOPE
    return out


@tool(ToolClass.READ, "validation", "Project quality gate")
def project_quality_gate(project: ProjectArg = None, profile: ProfileArg = None) -> dict:
    """Full gate across schematic, connectivity, transfer, PCB, placement and manufacturing."""
    gates = {"schematic": schematic_quality_gate(project), "connectivity": schematic_connectivity_gate(project)}
    model = model_for(project)
    if model.pcb():
        gates["transfer"] = pcb_transfer_quality_gate(project)
        gates["pcb"] = pcb_quality_gate(project, profile=profile)
        gates["placement"] = pcb_placement_quality_gate(project)
        gates["manufacturing"] = manufacturing_quality_gate(project, profile)
    verdicts = {k: g["verdict"] for k, g in gates.items()}
    overall = "FAIL" if "FAIL" in verdicts.values() else ("WARN" if "WARN" in verdicts.values() else "PASS")
    _record_trend("project", overall, project)
    return {"verdict": overall, "gates": verdicts, "scope": OFFLINE_GATE_SCOPE,
            "blockers": {k: g["blockers"] for k, g in gates.items() if g["blockers"]},
            "warnings": {k: g["warnings"][:10] for k, g in gates.items() if g["warnings"]}}


@tool(ToolClass.READ, "validation", "Gate trend history")
def project_gate_trend(gate: Annotated[str, Field(description="schematic, pcb, placement, manufacturing, project ...")] = "project",
                       project: ProjectArg = None, limit: Annotated[int, Field(ge=1, le=1000)] = 50) -> dict:
    """Past verdicts of one gate (recorded every time a gate runs)."""
    path = output_dir_for(project_path_or_none(project), "gates") / "gate_history.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("gate") == gate:
                rows.append(r)
    return {"gate": gate, "history": rows[-limit:], "runs": len(rows),
            "last": rows[-1]["verdict"] if rows else None}
