# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/analysis.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Validation and engineering-analysis tools (offline)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ...analysis import si
from ...analysis.consistency import compare
from ...analysis.dfm import PROFILES, run_dfm
from ...analysis.erc import run_erc
from ...analysis.review import decoupling_review, placement_overview
from ...offline.context import get_settings
from ...offline.errors import InvalidArgument
from ...offline.context import ToolClass
from .common import PcbArg, ProjectArg, model_for, pcb_for
from .registry import tool

ProfileArg = Annotated[str | None, Field(description=f"DFM profile: {', '.join(sorted(PROFILES))}. "
                                                     "Defaults to ALTIUM_AGENT_DFM_PROFILE.")]


@tool(ToolClass.READ, "validation", "Offline ERC")
def erc_check(project: ProjectArg = None,
              max_per_check: Annotated[int, Field(ge=1, le=1000)] = 50) -> dict:
    """Schematic checks without Altium: annotation, duplicates, missing footprints/values, unconnected pins,
    floating labels, single-pin nets, output conflicts, undriven nets."""
    return run_erc(model_for(project), max_per_check=max_per_check)


@tool(ToolClass.READ, "validation", "Schematic vs PCB consistency")
def sch_pcb_consistency(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Preview of Design > Update PCB: missing/extra components, footprint and comment mismatches, split or
    merged nets between the schematic and the PcbDoc."""
    model = model_for(project)
    board = pcb_for(project, pcb)
    return compare(model, board)


@tool(ToolClass.READ, "validation", "DFM check")
def dfm_check(project: ProjectArg = None, pcb: PcbArg = None, profile: ProfileArg = None) -> dict:
    """Check track widths, via/pad drills, annular rings, layer count, board size and rule limits against a
    fabricator profile."""
    return run_dfm(pcb_for(project, pcb), profile or get_settings().dfm_profile)


@tool(ToolClass.READ, "validation", "List DFM profiles")
def dfm_profiles() -> dict:
    """Built-in fabricator capability profiles (typical values; confirm with your fab)."""
    return {"default": get_settings().dfm_profile, "profiles": PROFILES}


@tool(ToolClass.READ, "validation", "Decoupling placement review")
def decoupling_check(project: ProjectArg = None, pcb: PcbArg = None,
                     max_distance_mm: Annotated[float, Field(gt=0, le=50)] = 3.0) -> dict:
    """For each IC power pin, distance to the nearest capacitor on the same net; flags far or missing decoupling."""
    return decoupling_review(pcb_for(project, pcb), max_distance_mm=max_distance_mm)


@tool(ToolClass.READ, "validation", "Placement overview")
def placement_check(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Components per designator prefix and side, parts outside the board outline, non-orthogonal rotations."""
    return placement_overview(pcb_for(project, pcb, primitives=False))


@tool(ToolClass.READ, "validation", "Design review gate")
def design_review(project: ProjectArg = None, profile: ProfileArg = None,
                  decoupling_distance_mm: Annotated[float, Field(gt=0, le=50)] = 3.0) -> dict:
    """One-call review: ERC, schematic/PCB sync, DFM, decoupling and placement, with a PASS/WARN/FAIL verdict
    and the findings that drive it. Offline pre-check; finish with live_compile and live_pcb_drc."""
    model = model_for(project)
    nl = model.netlist()
    erc = run_erc(model, nl, max_per_check=10)
    out: dict = {"project": str(model.project.path), "erc": erc["summary"],
                 "erc_top_findings": [f for f in erc["findings"] if f["severity"] == "error"][:15]}
    board = model.pcb()
    blockers, warnings = [], []
    if erc["summary"]["errors"]:
        blockers.append(f"{erc['summary']['errors']} ERC error(s)")
    if erc["summary"]["warnings"]:
        warnings.append(f"{erc['summary']['warnings']} ERC warning(s)")
    if board is None:
        warnings.append("project has no PcbDoc")
    else:
        sync = compare(model, board, nl, limit=15)
        dfm = run_dfm(board, profile or get_settings().dfm_profile, max_examples=5)
        dec = decoupling_review(board, max_distance_mm=decoupling_distance_mm)
        place = placement_overview(board)
        out.update({
            "sync": {k: sync[k] for k in ("in_sync", "components_only_in_schematic", "components_only_in_pcb",
                                          "footprint_mismatches", "nets_split_or_unassigned_in_pcb", "counts")},
            "dfm": {"profile": dfm["profile"]["name"], "passed": dfm["passed"], "summary": dfm["summary"],
                    "findings": dfm["findings"]},
            "decoupling": {"issue_count": dec["issue_count"], "issues": dec["issues"][:15]},
            "placement": {"outside_board_outline": place["outside_board_outline"], "rotation": place["rotation"]},
        })
        if not sync["in_sync"]:
            (warnings if nl.warnings else blockers).append("schematic and PCB are out of sync")
        if not dfm["passed"]:
            blockers.append(f"{dfm['summary']['errors']} DFM error(s) for {dfm['profile']['name']}")
        if dfm["summary"]["warnings"]:
            warnings.append(f"{dfm['summary']['warnings']} DFM warning(s)")
        if dec["issue_count"]:
            warnings.append(f"{dec['issue_count']} decoupling issue(s)")
        if place["outside_board_outline"]:
            blockers.append(f"{len(place['outside_board_outline'])} component(s) outside the board outline")
    out["verdict"] = "FAIL" if blockers else ("WARN" if warnings else "PASS")
    out["blockers"] = blockers
    out["warnings"] = warnings
    out["next_steps"] = ["Run proj_run_erc for Altium's own ERC",
                         "Run pcb_run_drc for the full DRC (clearances, shorts), then analyze_drc_report",
                         "Review dfm/decoupling findings with a person before any fabrication output"]
    out["scope"] = ("Offline pre-check. PASS is not a statement that the design is "
                    "manufacturing-ready.")
    return out


@tool(ToolClass.READ, "signal-integrity", "Impedance calculator")
def si_impedance(
    geometry: Literal["microstrip", "stripline"],
    width_mm: Annotated[float, Field(gt=0)],
    height_mm: Annotated[float, Field(gt=0, description="Dielectric height to the reference plane (stripline: to the upper plane).")],
    er: Annotated[float, Field(gt=1, le=20, description="Dielectric constant (Dk).")] = 4.2,
    thickness_mm: Annotated[float, Field(ge=0)] = 0.035,
    height2_mm: Annotated[float | None, Field(gt=0, description="Stripline: dielectric height to the lower plane (asymmetric).")] = None,
    spacing_mm: Annotated[float | None, Field(gt=0, description="Edge-to-edge gap for a differential pair.")] = None,
) -> dict:
    """Single-ended (and differential) impedance and propagation delay for a trace geometry."""
    return si.impedance(geometry, width_mm, height_mm, er, thickness_mm, height2_mm, spacing_mm)


@tool(ToolClass.READ, "signal-integrity", "Solve trace width for impedance")
def si_solve_width(
    target_ohm: Annotated[float, Field(gt=5, lt=200)],
    geometry: Literal["microstrip", "stripline"],
    height_mm: Annotated[float, Field(gt=0)],
    er: Annotated[float, Field(gt=1, le=20)] = 4.2,
    thickness_mm: Annotated[float, Field(ge=0)] = 0.035,
    height2_mm: Annotated[float | None, Field(gt=0)] = None,
    spacing_mm: Annotated[float | None, Field(gt=0, description="Set for a differential pair; target is then Zdiff.")] = None,
) -> dict:
    """Trace width that hits a target impedance (differential when spacing_mm is given)."""
    return si.solve_width(target_ohm, geometry, height_mm, er, thickness_mm, height2_mm, spacing_mm,
                          differential_target=spacing_mm is not None)


@tool(ToolClass.READ, "signal-integrity", "Board-aware impedance")
def si_board_impedance(
    layer: Annotated[str, Field(description="Copper layer name as in the stack, e.g. 'Top Layer'.")],
    width_mm: Annotated[float | None, Field(gt=0, description="Trace width to evaluate.")] = None,
    target_ohm: Annotated[float | None, Field(gt=5, lt=200, description="Solve the width for this impedance instead.")] = None,
    spacing_mm: Annotated[float | None, Field(gt=0, description="Differential pair gap.")] = None,
    project: ProjectArg = None,
    pcb: PcbArg = None,
) -> dict:
    """Impedance for a trace on a real board layer, with geometry taken from the PcbDoc stack-up
    (nearest copper layers treated as reference planes)."""
    doc = pcb_for(project, pcb, primitives=False)
    try:
        geo = si.board_geometry(doc, layer)
    except ValueError as exc:
        raise InvalidArgument(str(exc)) from None
    if target_ohm:
        res = si.solve_width(target_ohm, geo["geometry"], geo["height_mm"], geo["er"], geo["thickness_mm"],
                             geo.get("height2_mm"), spacing_mm, differential_target=spacing_mm is not None)
    elif width_mm:
        res = si.impedance(geo["geometry"], width_mm, geo["height_mm"], geo["er"], geo["thickness_mm"],
                           geo.get("height2_mm"), spacing_mm)
    else:
        raise InvalidArgument("pass width_mm or target_ohm")
    res["derived_from_stack"] = geo
    res["layer"] = layer
    return res


@tool(ToolClass.READ, "signal-integrity", "Current capacity (IPC-2221)")
def si_current_capacity(
    width_mm: Annotated[float | None, Field(gt=0, description="Trace width -> max current.")] = None,
    current_a: Annotated[float | None, Field(gt=0, description="Current -> required width.")] = None,
    copper_um: Annotated[float, Field(gt=0, description="Copper thickness in micrometres (35 = 1 oz).")] = 35.0,
    temp_rise_c: Annotated[float, Field(gt=0, le=100)] = 10.0,
    external: bool = True,
) -> dict:
    """IPC-2221 trace current capacity or required width for a current."""
    if width_mm:
        return si.ipc2221_current(width_mm, copper_um, temp_rise_c, external)
    if current_a:
        return si.ipc2221_width_for_current(current_a, copper_um, temp_rise_c, external)
    raise InvalidArgument("pass width_mm or current_a")
