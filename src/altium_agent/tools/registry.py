# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""A capturing stand-in for the MCP server, used by the minimal toolset.

Some MCP clients cap how many tools a server may expose, or serialize
every schema into the model context at startup and slow to a crawl. This
server registers several hundred, so those clients cannot use it at all.

The fix is NOT to merge tools into generic dispatchers: the specialised
names, descriptions and schemas are what let a model find the right
operation and follow the per-tool discipline, and collapsing them
measurably degrades that. Instead, ``minimal`` keeps every tool intact
but stops ADVERTISING them, exposing only ``tool_catalog`` (discover)
and ``tool_invoke`` (run by name).

:class:`ToolRegistry` is what makes that possible. It quacks like the
FastMCP object the registrars expect:

* ``tool()`` captures the decorated function instead of publishing it
* ``list_tools()`` and ``call_tool()`` answer from what it captured

Because ``tool_catalog`` and ``tool_invoke`` close over the object they
were registered against, handing them a registry makes them discover and
dispatch across the FULL set while the client only ever sees two tools.
Neither function needs to know which mode it is running in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from mcp.server.fastmcp.tools import Tool
from mcp.types import ToolAnnotations

__all__ = ["CORE_TOOLS", "MINIMAL_TOOLS", "ToolRegistry", "ToolSpec"]

#: The only tools a minimal server advertises. ``tool_catalog`` finds an
#: operation, ``tool_invoke`` runs it, and between them they reach
#: everything else.
MINIMAL_TOOLS = ("tool_catalog", "tool_invoke")

#: What a "core" server advertises: the tools the shipped skills name, the
#: ones a design run reaches for every few calls, and the two meta-tools,
#: which still reach every other tool. All 470-odd schemas come to about
#: 150k tokens; a client that loads them costs that on every turn.
CORE_TOOLS = MINIMAL_TOOLS + (
    "tool_guide",
    # health and Altium
    "app_ping", "app_diagnose", "app_recover", "app_checkpoint",
    "app_save_all", "app_get_active_document", "app_set_active_document",
    "app_list_documents", "app_screenshot",
    # the design run
    "design_get_discipline", "design_autonomy_guide",
    "design_session_start", "design_session_log", "design_session_status",
    "design_session_resume", "design_session_report", "design_next_action",
    "design_status", "design_gate_review", "design_undo_to_stage",
    "design_validate_requirement", "design_list_circuit_blocks",
    "design_add_circuit_block", "design_add_part", "design_edit_plan",
    "design_validate_plan", "design_review_plan", "design_preview_plan",
    "design_execute_plan", "design_hints_from_sheet",
    "design_audit_schematic", "design_generate_bom",
    "design_datasheet_checklist",
    # parts and libraries
    "lib_search_parts", "lib_fetch_part", "lib_fetch_datasheet",
    "lib_check_package", "lib_render_package", "lib_create_ic_symbol",
    "lib_create_passive_symbol", "lib_create_footprint_from_package",
    "lib_create_standard_footprint",
    # project and schematic
    "proj_create", "proj_open", "proj_save", "proj_compile", "proj_run_erc",
    "proj_get_erc_violations", "proj_sync_pcb", "proj_get_nets",
    "sch_render_svg", "obj_query", "obj_modify", "obj_delete",
    # board
    "pcb_set_board_shape", "pcb_set_board_outline", "pcb_set_sheet_position", "pcb_define_board_shape_from_selection",
    "pcb_sync_layer_stack_region",
    "pcb_create_primitives_from_board_shape", "pcb_add_board_cutout", "pcb_add_layer", "pcb_modify_layer",
    "pcb_read_stackup", "pcb_import_layer_stack", "pcb_create_design_rule", "pcb_read_rules",
    # rules: fixing Altium's blank-board defaults is most of rule setup
    "pcb_audit_rules", "pcb_set_rule_properties", "pcb_delete_design_rule",
    "pcb_set_rules_enabled", "pcb_get_design_rules", "pcb_get_rule_properties",
    "pcb_place_via", "pcb_place_vias", "pcb_focus_board",
    "pcb_create_diff_pair", "pcb_get_diff_pair_rules",
    "pcb_place_stitching_vias", "pcb_add_drill_pair", "pcb_get_drill_pairs", "pcb_repour_polygons", "pcb_run_drc",
    "pcb_read_ratsnest", "pcb_get_unrouted_nets", "pcb_render_svg",
    "place_plan", "place_check", "place_apply",
    # AI-driven placement and routing: the agent decides, the session checks
    "place_session_start", "place_session_part", "place_session_move", "place_session_check",
    "place_session_view", "place_session_undo", "place_session_export",
    "route_session_start", "route_session_route", "route_session_draw", "route_session_fanout",
    "route_session_check", "route_session_ripup", "route_session_undo", "route_session_view",
    "route_session_export", "route_session_packets", "route_session_merge",
    "route_engines", "route_broker_plan",
    "route_import_ses", "route_situs", "route_converge", "route_refcopy",
    "route_via_style", "route_set_via_tenting", "route_power_fanout",
    # the routing skill: rules per class, pours and planes for power
    "pcb_read_summary", "pcb_read_net_statistics", "pcb_get_differential_pairs",
    "pcb_create_net_class", "pcb_lock_net_routing", "pcb_place_polygon_rect", "pcb_set_plane_net",
    # checks
    "analyze_erc", "analyze_sch_pcb_sync", "analyze_board_review",
    "analyze_review_design",
    "analyze_drc_report", "analyze_dfm", "analyze_dfm_profiles",
    "analyze_dfm_use_profile", "analyze_si_generate_stackup",
    "analyze_si_impedance", "analyze_si_pair_skew",
    "ref_profile", "ref_compare",
)


@dataclass
class ToolSpec:
    """One captured tool: FastMCP's own ``Tool`` plus the attribute names
    the meta-tools read.

    The schema and the argument validation are FastMCP's, built by
    ``Tool.from_function`` exactly as ``@mcp.tool()`` would, so a tool
    reached through ``tool_invoke`` gets the same ``list[str]`` /
    ``dict`` types in ``tool_catalog`` and the same coercion and
    validation errors as one the client called directly.
    """

    tool: Tool

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def description(self) -> str:
        return self.tool.description or ""

    @property
    def fn(self) -> Callable:
        return self.tool.fn

    @property
    def annotations(self) -> Optional[ToolAnnotations]:
        return self.tool.annotations

    @property
    def inputSchema(self) -> dict[str, Any]:
        """Same attribute name and shape as ``mcp.types.Tool.inputSchema``."""
        return self.tool.parameters


class ToolRegistry:
    """Collects tool registrations and can dispatch to them by name."""

    def __init__(self, delegate: Optional[Any] = None) -> None:
        """
        Args:
            delegate: when given, every captured tool is ALSO registered
                on this object. That keeps a full-surface server working
                while still building the index.
        """
        self._delegate = delegate
        self._tools: dict[str, ToolSpec] = {}

    # -- registration -------------------------------------------------
    def tool(self, *args: Any, **kwargs: Any):
        """Stand in for ``@mcp.tool()``, with the same keyword arguments."""
        def deco(fn: Callable) -> Callable:
            spec = ToolSpec(Tool.from_function(fn, **kwargs))
            self._tools[spec.name] = spec
            if self._delegate is not None:
                self._delegate.tool(*args, **kwargs)(fn)
            return fn
        return deco

    def prompt(self, *args: Any, **kwargs: Any):
        """Pass prompts through; they are not part of the tool surface."""
        if self._delegate is not None and hasattr(self._delegate, "prompt"):
            return self._delegate.prompt(*args, **kwargs)
        return lambda fn: fn

    def resource(self, *args: Any, **kwargs: Any):
        if self._delegate is not None and hasattr(self._delegate, "resource"):
            return self._delegate.resource(*args, **kwargs)
        return lambda fn: fn

    # -- the read side the meta-tools use -----------------------------
    async def list_tools(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    async def call_tool(self, name: str, arguments: Optional[dict] = None):
        """Validate the arguments and invoke a captured tool by name.

        Returns the tool's own return value. ``tool_invoke`` already
        handles both this shape and FastMCP's content-list shape, so no
        wrapping is needed here. Bad arguments raise FastMCP's
        ``ToolError``, naming the parameter, before the tool runs.
        """
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"unknown tool: {name}")
        return await spec.tool.run(arguments or {})

    # -- plain accessors ----------------------------------------------
    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
