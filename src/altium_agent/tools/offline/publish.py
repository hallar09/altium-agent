# SPDX-License-Identifier: Apache-2.0
"""The offline file tools from altium-mcp-pro, published under Altium Agent names.

The modules in this package are altium-mcp-pro's tool modules (MIT, see
THIRD_PARTY_NOTICES.md), adapted only where the host differs. Each tool
is captured by ``registry.tool`` and then published here under the name
``RENAMES`` gives it, or deliberately left out (``None``), so a new tool
in a future port cannot slip in under its old name: an unmapped tool
fails ``register_offline_tools``.

Naming (docs/TOOL_NAMING.md): reading a design FILE uses the verb
``read`` (``pcb_read_tracks``), so it is never confused with the live
``get`` tools that ask the running Altium; checks and calculators are
``analyze_``. Every tool here reads files only, so all are badged
``offline`` and ``readonly``; the few that write (a BOM CSV, a gate
history line) write under the Altium Agent output folder, never beside
the design (offline/context.py).
"""

from __future__ import annotations

import asyncio
import functools
import re
from typing import Any, Callable

#: altium-mcp-pro name -> Altium Agent name, or None to leave it out.
RENAMES: dict[str, str | None] = {
    # validation and SI calculators (tools/analysis.py)
    "erc_check": "analyze_erc",
    "sch_pcb_consistency": "analyze_sch_pcb_sync",
    "dfm_check": "analyze_dfm",
    "dfm_profiles": "analyze_dfm_profiles",
    "decoupling_check": "analyze_decoupling",
    "placement_check": "analyze_placement_overview",
    "design_review": "analyze_board_review",
    "si_impedance": "analyze_si_impedance",
    "si_solve_width": "analyze_si_solve_width",
    "si_board_impedance": "analyze_si_board_impedance",
    "si_current_capacity": "analyze_si_current_capacity",
    # file inspection (tools/inspection.py)
    "pcb_get_tracks": "pcb_read_tracks",
    "pcb_get_vias": "pcb_read_vias",
    "pcb_get_pads": "pcb_read_pads",
    "pcb_get_shapes": "pcb_read_shapes",
    "pcb_get_zones": "pcb_read_zones",
    "pcb_get_layers": "pcb_read_layers",
    "pcb_get_origin": "pcb_read_origin",
    "pcb_get_net_statistics": "pcb_read_net_statistics",
    "pcb_get_ratsnest": "pcb_read_ratsnest",
    "get_unconnected_nets": "pcb_read_unconnected_nets",
    "pcb_get_footprint_layers": "pcb_read_footprint_layers",
    "pcb_export_stats": None,            # pcb_read_summary covers it without writing a file
    "sch_get_wires": "sch_read_wires",
    "sch_get_labels": "sch_read_labels",
    "sch_get_net_names": "sch_read_net_names",
    "sch_list_sheets": "sch_read_sheets",
    "sch_list_sheet_pins": "sch_read_sheet_pins",
    "sch_trace_net": "sch_read_net_trace",
    "sch_get_pin_positions": "sch_read_pin_positions",
    "sch_get_bounding_boxes": "sch_read_bounding_boxes",
    "sch_get_connectivity_graph": "sch_read_connectivity_graph",
    "sch_get_circuit_ir": "sch_read_circuit_ir",
    "sch_get_population_status": "sch_read_population_status",
    "sch_check_power_flags": "analyze_power_flags",
    "altium_set_project": "proj_set_offline_project",
    "altium_get_project_info": None,     # proj_read_summary
    "altium_scan_directory": None,       # proj_find_projects
    "altium_list_recent_projects": None,
    "altium_get_version": None,          # app_get_status / doctor
    "altium_list_tool_categories": None, # tool_catalog
    "altium_get_tools_in_category": None,
    "altium_help": None,                 # tool_guide
    # placement critique and gates (tools/quality.py)
    "pcb_critique_placement": "analyze_placement_critique",
    "pcb_score_placement": "analyze_placement_score",
    "pcb_placement_quality_gate": "analyze_gate_placement",
    "pcb_visual_qa": "analyze_visual_qa",
    "dfm_load_manufacturer_profile": "analyze_dfm_use_profile",
    "dfm_run_manufacturer_check": None,  # same as analyze_dfm
    "dfm_calculate_manufacturing_cost": "analyze_dfm_cost",
    "manufacturing_quality_gate": "analyze_gate_manufacturing",
    "pcb_stackup_consistency_gate": "analyze_gate_stackup",
    "schematic_quality_gate": "analyze_gate_schematic",
    "schematic_connectivity_gate": "analyze_gate_connectivity",
    "pcb_quality_gate": "analyze_gate_pcb",
    "pcb_transfer_quality_gate": "analyze_gate_transfer",
    "validate_design": "analyze_validate_design",
    "project_quality_gate": "analyze_gate_project",
    "project_gate_trend": "analyze_gate_trend",
    # power, thermal, SI and EMC (tools/engineering.py)
    "pdn_calculate_voltage_drop": "analyze_pdn_voltage_drop",
    "pdn_check_copper_weight": "analyze_pdn_copper_weight",
    "pdn_recommend_decoupling_caps": "analyze_pdn_decoupling_caps",
    "check_power_integrity": "analyze_pdn_power_integrity",
    "thermal_calculate_via_count": "analyze_thermal_via_count",
    "thermal_check_copper_pour": "analyze_thermal_copper_pour",
    "thermal_simulate_plane_spreading": "analyze_thermal_plane_spreading",
    "si_analyze_high_speed_channel": "analyze_si_channel",
    "si_check_via_stub": "analyze_si_via_stub",
    "si_check_differential_pair_skew": "analyze_si_pair_skew",
    "si_validate_length_matching": "analyze_si_length_matching",
    "si_list_dielectric_materials": "analyze_si_dielectrics",
    "si_generate_stackup": "analyze_si_generate_stackup",
    "si_synthesize_stackup_for_interfaces": "analyze_si_stackup_for_interfaces",
    "si_calculate_decoupling_placement": "analyze_si_decoupling_placement",
    "emc_check_return_path_continuity": "analyze_emc_return_path",
    "emc_check_ground_plane_voids": "analyze_emc_ground_voids",
    "emc_check_split_plane_crossing": "analyze_emc_split_plane_crossing",
    "emc_check_via_stitching": "analyze_emc_via_stitching",
    "emc_check_differential_pair_symmetry": "analyze_emc_pair_symmetry",
    "emc_check_high_speed_routing_rules": "analyze_emc_high_speed_rules",
    "emc_check_decoupling_placement": "analyze_emc_decoupling",
    "emc_run_full_compliance": "analyze_emc_compliance",
    "pcb_check_creepage_clearance": "analyze_creepage",
    "pcb_route_corner_style_gate": "analyze_gate_corners",
    # manufacturing (tools/manufacturing.py): fabrication outputs are not
    # Altium Agent's to start (they need the user's explicit approval)
    "bom_generate": "proj_read_bom",
    "outjob_run": None,
    "release_package": None,
    # project, schematic, PCB and library files
    "project_find": "proj_find_projects",
    "project_summary": "proj_read_summary",
    "project_variants": "proj_read_variants",
    "outjob_list": "proj_read_outjobs",
    "sch_list_components": "sch_read_components",
    "sch_get_component": "sch_read_component",
    "sch_netlist": "sch_read_netlist",
    "sch_net_detail": "sch_read_net",
    "sch_sheet": "sch_read_sheet",
    "pcb_summary": "pcb_read_summary",
    "pcb_stackup": "pcb_read_stackup",
    "pcb_components": "pcb_read_components",
    "pcb_nets": "pcb_read_nets",
    "pcb_net_detail": "pcb_read_net",
    "pcb_rules": "pcb_read_rules",
    "pcb_classes": "pcb_read_classes",
    "pcb_pad_netlist": "pcb_read_pad_netlist",
    "lib_symbols": "lib_read_symbols",
    "lib_footprints": "lib_read_footprints",
    "lib_search": "lib_search_files",
}

#: Names in docstrings that are not ported tools but have an equivalent.
_DOC_ALIASES = {
    "live_compile": "proj_run_erc",
    "live_pcb_drc": "pcb_run_drc",
    "server_status": "app_get_status",
}

_MODULES = ("analysis", "inspection", "quality", "engineering", "manufacturing",
            "project", "schematic", "pcb", "library")


def _captured():
    import importlib

    for m in _MODULES:
        importlib.import_module(f"{__package__}.{m}")
    from .registry import TOOLS

    return TOOLS


def _rewrite_doc(doc: str) -> str:
    names = {k: v for k, v in RENAMES.items() if v} | _DOC_ALIASES
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b")
    return pattern.sub(lambda m: names[m.group(1)], doc or "")


def offline_tool_names() -> frozenset[str]:
    """Every tool this package publishes (all ``offline`` / ``readonly``)."""
    return frozenset(v for v in RENAMES.values() if v)


def _publishable(fn: Callable, new_name: str) -> Callable:
    """An async wrapper under the new name: runs the file work in a thread
    and turns the ported typed errors into an ``ok: false`` reply."""
    from ...offline.errors import OfflineError

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = await asyncio.to_thread(fn, *args, **kwargs)
        except OfflineError as exc:
            return {"ok": False, **exc.to_dict()}
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        if isinstance(result, dict) and "ok" not in result:
            result = {"ok": True, **result}
        return result

    wrapper.__name__ = new_name
    wrapper.__qualname__ = new_name
    wrapper.__doc__ = _rewrite_doc(fn.__doc__ or "")
    return wrapper


def register_offline_tools(mcp) -> None:
    tools = _captured()
    unmapped = sorted({t.name for t in tools} - set(RENAMES))
    if unmapped:
        raise RuntimeError(f"ported tools without a naming decision: {unmapped}")
    for spec in tools:
        new = RENAMES[spec.name]
        if new is None or spec.live:
            continue
        mcp.tool()(_publishable(spec.fn, new))


__all__ = ["RENAMES", "offline_tool_names", "register_offline_tools"]
