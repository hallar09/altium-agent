# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""MCP Tools for Altium Designer.

Altium Agent changes: forked from eda-agent; KiCad and EasyEDA surfaces
removed, Altium Agent tool modules registered (docs/PROVENANCE.md).

Error-shape convention (two families, consistent within each):

- Bridge-backed tools (anything that round-trips Altium) report failures as
  ``{"error": "<message>", <count_field>: 0, ...}`` -- or raise, in which
  case the MCP layer surfaces the exception text. Successful payloads come
  from the Pascal handler verbatim.
- Offline calculators (``pcb_calc_*``, ``design_compute_*``, exporters)
  report ``{"ok": False, "reason": "<message>"}`` and ``{"ok": True, ...}``
  on success.

New tools should match the family they belong to rather than invent a third
shape.
"""

from .application import (
    register_application_tools,
    register_meta_tools,
)
from .guidance import register_guidance_tools
from .uiauto import register_uiauto_tools
from .project import register_project_tools
from .library import register_library_tools
from .generic import register_generic_tools
from .pcb import register_pcb_tools
from .review import register_review_tools
from .sim import register_sim_tools
from .design import register_design_tools
from .render import register_render_tools
from .audit import register_audit_tools
from .route import register_route_tools
from .eda import register_eda_tools
from .parts import register_parts_tools
from .live import register_live_tools
from .footprint import register_footprint_tools
from .agent import register_agent_tools
from .offline import register_offline_tools
from .house_style import register_house_style_tools
from .routing_broker import register_routing_broker_tools

# Altium Agent drives Altium Designer only. The backend switch of
# eda-agent (kicad / easyeda / both) was removed; the names stay so callers
# and the environment variable keep working and an unknown value is
# still refused loudly.
BACKENDS = ("altium",)
DEFAULT_BACKEND = "altium"

# "full" advertises every tool. "minimal" registers them all internally
# but advertises only tool_catalog + tool_invoke, for MCP clients that
# cap tool count or stall serializing hundreds of schemas at startup.
# See altium_agent.tools.registry for why this beats merging tools into
# generic dispatchers.
TOOLSETS = ("full", "minimal")
DEFAULT_TOOLSET = "full"

#: Design tools that compute over a plan and never touch a bridge.
#:
#: MEASURED, not judged by name: each was called with the Altium bridge
#: replaced by a tripwire (tests/test_offline_design_tools.py), so this
#: list is the evidence behind their ``offline`` maturity badge.
#: (In eda-agent this list also chose what the EasyEDA backend got.)
OFFLINE_DESIGN_TOOLS = (
    "design_add_circuit_block",
    "design_add_part",
    "design_apply_hierarchy",
    "design_autonomy_guide",
    "design_bom_file",
    "design_compose_netlist",
    "design_compute_component_value",
    "design_connect_bus",
    "design_describe_circuits",
    "design_edit_plan",
    "design_generate_bom",
    "design_get_discipline",
    "design_layout_schematic",
    "design_learn_from_layout",
    "design_list_circuit_blocks",
    "design_load_fab_profile",
    "design_next_action",
    "design_plan_hierarchy",
    "design_review_file",
    "design_session_log",
    "design_session_report",
    "design_session_resume",
    "design_session_start",
    "design_session_status",
    "design_solve_netlist_file",
    "design_suggest_diff_pair_traces",
    "design_suggest_partition",
    "design_synthesize_rules",
    "design_validate_requirement",
)



#: Review tools that need no bridge. Measured the same way as
#: OFFLINE_DESIGN_TOOLS: called with the Altium bridge replaced by a
#: tripwire, and the tripwire did not fire. The datasheet discipline is
#: about how a part is verified, not about which editor is open, so
#: withholding it from a backend taught nothing except that the rules
#: are Altium's. They are not.
OFFLINE_REVIEW_TOOLS = (
    "design_datasheet_checklist",
)



def register_all_tools(mcp):
    """Register all Altium tools with the MCP server."""
    # Dialog-driving tools. Altium-only and Windows-only:
    # they press buttons in the GUI for the one operation that
    # has no API at all, so they do not belong on any other
    # backend and are registered with the Altium suite alone.
    register_uiauto_tools(mcp)
    register_application_tools(mcp)
    register_project_tools(mcp)
    register_library_tools(mcp)
    register_generic_tools(mcp)
    register_pcb_tools(mcp)
    register_review_tools(mcp)
    register_sim_tools(mcp)
    register_design_tools(mcp)
    register_render_tools(mcp)
    register_audit_tools(mcp)
    register_route_tools(mcp)


def register_backend(
    mcp, backend: str = DEFAULT_BACKEND, toolset: str = DEFAULT_TOOLSET,
) -> str:
    """Register the tool surface for one backend and return the name used.

    An unknown backend falls back to the default rather than raising, so a
    stray value in the environment can never leave the server toolless.

    Args:
        toolset: "full" advertises every tool. "minimal" advertises only
            tool_catalog and tool_invoke while keeping all the others
            reachable through them, for clients that cannot cope with a
            few hundred tools.
    """
    toolset = (toolset or DEFAULT_TOOLSET).strip().lower()
    if toolset not in TOOLSETS:
        toolset = DEFAULT_TOOLSET
    if toolset == "minimal":
        return _register_minimal(mcp, backend)
    return _register_full(mcp, backend)


def _register_minimal(mcp, backend: str) -> str:
    """Capture the whole surface, then advertise only the meta-tools."""
    from .registry import MINIMAL_TOOLS, ToolRegistry

    registry = ToolRegistry()
    name = _register_full(registry, backend)
    missing = [t for t in MINIMAL_TOOLS if t not in registry]
    if missing:
        # Advertising nothing usable would be worse than a full surface,
        # so fall back loudly rather than serve an unusable server.
        raise RuntimeError(
            f"minimal toolset needs {missing}, which the backend did not "
            f"register")
    for tool_name in MINIMAL_TOOLS:
        # These close over `registry`, so they discover and dispatch
        # across everything captured above even though the client sees
        # only these two.
        mcp.tool()(registry.get(tool_name).fn)
    return name


def _register_full(mcp, backend: str = DEFAULT_BACKEND) -> str:
    backend = (backend or DEFAULT_BACKEND).strip().lower()
    if backend not in BACKENDS:
        backend = DEFAULT_BACKEND
    # ONE SOURCE OF TRUTH FOR WHICH EDA IS ACTIVE (kept from eda-agent,
    # where a second resolver once reviewed the wrong design).
    from ..core.backends import set_active_backend
    set_active_backend(backend)
    register_all_tools(mcp)
    # Snapshot review (analyze_review_design).
    register_eda_tools(mcp)
    # tool_catalog / tool_invoke describe the tool surface itself and must
    # exist for the minimal toolset to be possible at all.
    register_meta_tools(mcp)
    # tool_guide: which tool for which document, and what is proven
    # impossible.
    register_guidance_tools(mcp)
    # Part sourcing, the live layout view, datasheet fetch and package
    # checks need no editor.
    register_parts_tools(mcp)
    register_live_tools(mcp)
    register_footprint_tools(mcp)
    # Altium Agent additions (tools/agent.py and what it imports).
    register_agent_tools(mcp)
    register_offline_tools(mcp)
    # House style and placement (phase c).
    register_house_style_tools(mcp)
    # The routing broker (phase d).
    register_routing_broker_tools(mcp)
    return backend


__all__ = [
    "register_all_tools",
    "register_backend",
    "register_eda_tools",
    "OFFLINE_DESIGN_TOOLS",
    "OFFLINE_REVIEW_TOOLS",
    "BACKENDS",
    "DEFAULT_BACKEND",
    "TOOLSETS",
    "DEFAULT_TOOLSET",
    "register_application_tools",
    "register_guidance_tools",
    "register_uiauto_tools",
    "register_meta_tools",
    "register_parts_tools",
    "register_live_tools",
    "register_footprint_tools",
    "register_agent_tools",
    "register_offline_tools",
    "register_house_style_tools",
    "register_routing_broker_tools",
    "register_project_tools",
    "register_library_tools",
    "register_generic_tools",
    "register_pcb_tools",
    "register_review_tools",
    "register_sim_tools",
    "register_design_tools",
    "register_render_tools",
    "register_audit_tools",
    "register_route_tools",

]
