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

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Optional

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
    "pcb_set_board_shape", "pcb_add_layer", "pcb_modify_layer",
    "pcb_read_stackup", "pcb_create_design_rule", "pcb_read_rules",
    "pcb_create_diff_pair", "pcb_get_diff_pair_rules",
    "pcb_place_stitching_vias", "pcb_repour_polygons", "pcb_run_drc",
    "pcb_read_ratsnest", "pcb_get_unrouted_nets", "pcb_render_svg",
    "place_plan", "place_check", "place_apply",
    "route_engines", "route_broker_plan", "route_freerouting",
    "route_import_ses", "route_situs", "route_converge", "route_refcopy",
    "route_via_style", "route_set_via_tenting",
    # checks
    "analyze_erc", "analyze_sch_pcb_sync", "analyze_board_review",
    "analyze_review_design",
    "analyze_drc_report", "analyze_dfm", "analyze_dfm_profiles",
    "analyze_dfm_use_profile", "analyze_si_generate_stackup",
    "analyze_si_impedance", "analyze_si_pair_skew",
    "ref_profile", "ref_compare",
)


def _json_type(annotation: Any) -> str:
    """Best-effort JSON type name for a Python annotation."""
    text = str(annotation)
    if annotation is inspect.Parameter.empty:
        return "any"
    for needle, name in (("bool", "boolean"), ("int", "integer"),
                         ("float", "number"), ("str", "string"),
                         ("list", "array"), ("dict", "object")):
        if needle in text:
            return name
    return "any"


@dataclass
class ToolSpec:
    """The subset of a FastMCP tool that the meta-tools actually read."""

    name: str
    description: str
    fn: Callable

    @property
    def inputSchema(self) -> dict[str, Any]:
        """A JSON-Schema-ish view, matching what FastMCP exposes.

        Same attribute name and shape as ``mcp.types.Tool.inputSchema``
        so ``tool_catalog`` can read parameters identically whether it is
        looking at a real MCP tool or a captured one.
        """
        props: dict[str, Any] = {}
        required: list[str] = []
        try:
            sig = inspect.signature(self.fn)
        except (TypeError, ValueError):
            return {"type": "object", "properties": {}, "required": []}
        for name, param in sig.parameters.items():
            if name == "self" or param.kind in (
                    param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            entry: dict[str, Any] = {"type": _json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(name)
            elif isinstance(param.default, (str, int, float, bool)):
                entry["default"] = param.default
            props[name] = entry
        return {"type": "object", "properties": props, "required": required}


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
        """Stand in for ``@mcp.tool()``."""
        def deco(fn: Callable) -> Callable:
            self._tools[fn.__name__] = ToolSpec(
                name=fn.__name__,
                description=inspect.getdoc(fn) or "",
                fn=fn,
            )
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
        """Invoke a captured tool by name.

        Returns the tool's own return value. ``tool_invoke`` already
        handles both this shape and FastMCP's content-list shape, so no
        wrapping is needed here.
        """
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"unknown tool: {name}")
        result = spec.fn(**(arguments or {}))
        if inspect.isawaitable(result):
            result = await result
        return result

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
