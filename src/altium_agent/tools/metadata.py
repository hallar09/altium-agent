# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""Out-of-band metadata for the MCP tool surface.

FastMCP (mcp 1.5.x, pinned <2) has no ``tags``/``annotations`` field on its
tool model, so tool metadata cannot ride on the ``@mcp.tool()`` decorator.
This module keeps a decorator-independent registry instead, keyed by tool
name, that other layers consume:

- the future discovery layer (a ``tool_catalog`` meta-tool) filters by
  ``category`` / ``interaction`` so a client need not load 345 schemas;
- the docs generator groups by ``category`` and badges by ``maturity``;
- a CI drift test asserts every registered tool resolves here and that no
  override names a tool that no longer exists.

Three axes:

``category`` -- derived from the name prefix; 100% mechanical.

``maturity`` -- how well the tool is verified.
    ``offline``         pure Python, deterministic, fully covered by CI
                        unit tests; never touches Altium.
    ``simulator``       bridge-backed, and EVERY command it sends is
                        implemented by the in-repo Python Altium
                        simulator, so the whole call can be driven in CI
                        with no Altium.
    ``live_verified``   bridge-backed and recorded as verified against a
                        live Altium by Altium Agent's own release run
                        (tools/live_verified.json). Takes precedence over
                        ``simulator``.
    ``live_unverified`` bridge-backed, needs a live Altium, and has no
                        Altium Agent live record yet (eda-agent's
                        ``live_only``).
  ``simulator`` is measured against the simulator itself rather than
  estimated, and a guard test fails if the two disagree. Read it as
  "exercisable without Altium", not as "a test exercises it today":
  those are different claims and only the first is enforced here.
  The Phase 1.3 nightly live-run is the intended source of a future
  promotion to a ``verified`` tier; when that lands, record per-tool
  results in ``MATURITY_OVERRIDES``.

``interaction`` -- what running the tool does to the Altium session.
    ``readonly``   queries / reads; no mutation.
    ``silent``     mutates, no dialog -- the loop stays responsive.
    ``modal``      pops a non-suppressible Altium dialog that BLOCKS the
                   polling loop until a human clicks (see the bridge audit).
    ``partial``    succeeds but leaves the job incomplete; a follow-up tool
                   is required to finish.
"""

from __future__ import annotations

# --- maturity tiers ---------------------------------------------------------
OFFLINE = "offline"
SIMULATOR = "simulator"
#: Bridge-backed and recorded as verified against a live Altium by Altium
#: Agent itself (src/altium_agent/tools/live_verified.json, written by the
#: release-verification run in docs/RELEASE_VERIFICATION.md). A record from
#: eda-agent does not count: the script that answers is a different build.
LIVE_VERIFIED = "live_verified"
#: Bridge-backed, needs a live Altium, and not yet verified live by Altium
#: Agent. eda-agent called this tier ``live_only``.
LIVE_UNVERIFIED = "live_unverified"
#: Kept so forked code that names the old tier still reads correctly.
LIVE_ONLY = LIVE_UNVERIFIED
MATURITIES = (OFFLINE, SIMULATOR, LIVE_VERIFIED, LIVE_UNVERIFIED)

# --- interaction classes ----------------------------------------------------
READONLY = "readonly"
SILENT = "silent"
MODAL = "modal"
PARTIAL = "partial"
INTERACTIONS = (READONLY, SILENT, MODAL, PARTIAL)

# --- category derivation ----------------------------------------------------
# Ordered longest-prefix-first so ``application`` wins over ``app_`` etc.
_CATEGORY_BY_PREFIX = (
    ("app_", "application"),
    ("proj_", "project"),
    ("lib_", "library"),
    ("obj_", "generic"),
    ("sch_", "schematic"),
    ("pcb_", "pcb"),
    ("audit_", "audit"),
    ("design_", "design"),
    ("route_", "routing"),
    ("place_", "placement"),
    ("ref_", "reference"),
    ("analyze_", "analysis"),
    # Meta tools describe the tool surface itself, not a document.
    ("tool_", "meta"),
)

#: The prefixes a tool name may start with (TOOL_NAMING.md). A test holds
#: every registered tool to this list.
TOOL_PREFIXES = tuple(prefix for prefix, _ in _CATEGORY_BY_PREFIX)

# Tools every one of whose bridge commands the in-repo Altium simulator
# implements, so the whole call can be driven in CI with no Altium.
#
# This list is GROUND TRUTH, not an estimate, and is not maintained by
# hand: tests/test_simulator_maturity_is_measured.py probes the real
# simulator for every tool and fails unless this set matches EXACTLY, in
# both directions. Adding a simulator handler is what promotes a tool;
# the test tells you the new list.
#
# It replaced a per-CATEGORY guess (application / project / library /
# generic / schematic were assumed covered). That proxy was wrong on
# more than half the bridge-backed surface: 131 tools claimed coverage
# for commands the simulator answers with UNKNOWN_ACTION, and 18 it
# fully answers were published as live_only. A category is not a
# handler, and a tool is only covered when EVERY command it sends is,
# because one unanswered call ends the run just as surely.
_SIMULATOR_TOOLS = frozenset({
    "app_context", "app_detach", "app_get_active_document",
    "app_get_version",
    "app_list_documents", "app_set_active_document",
    "audit_find_missing_decoupling",
    "audit_find_pin_net_name_mismatches",
    "audit_find_unconnected_ic_pins", "design_lint_report",
    "lib_add_footprint_arc", "lib_add_footprint_pad",
    "lib_add_footprint_pads", "lib_add_footprint_track",
    "lib_add_footprint_tracks", "lib_add_pins",
    "lib_add_symbol_polygon", "lib_add_symbol_rectangle", "lib_add_symbol_text",
    "lib_batch_rename", "lib_batch_set_params", "lib_create_footprint",
    "lib_create_ic_symbol", "lib_create_standard_footprint",
    "lib_create_symbol", "lib_diff_libraries",
    "lib_get_component_details", "lib_get_components",
    "lib_link_3d_model", "lib_link_footprint",
    "obj_create", "obj_delete", "obj_deselect_all", "obj_get_font_id",
    "obj_get_font_spec", "obj_modify", "obj_query", "obj_run_process",
    "obj_select", "obj_zoom", "pcb_bind_pad_nets",
    "pcb_build_from_project", "pcb_create_nets_from_list",
    "pcb_get_board_outline", "pcb_get_board_statistics",
    "pcb_get_components", "pcb_get_nets", "pcb_get_unrouted_nets",
    "pcb_get_vias", "pcb_move_components", "pcb_place_components",
    "pcb_place_tracks", "pcb_place_via", "pcb_run_drc",
    "pcb_set_rules_enabled",
    "proj_add_document", "proj_annotate", "proj_compile",
    "proj_create", "proj_cross_probe", "proj_export_bom_html",
    "proj_export_netlist", "proj_export_pdf", "proj_get_board_info",
    "proj_get_bom", "proj_get_component_info", "proj_get_focused",
    "proj_get_nets", "proj_get_parameters", "proj_get_stats",
    "proj_list_documents", "proj_open", "proj_remove_document",
    "proj_run_output", "proj_save", "proj_set_parameter",
})
# Meta tools and the route planner never touch the Altium bridge.
_OFFLINE_CATEGORIES = frozenset({"meta"})
# pcb / audit / simulation -> live_only; design is mixed (mostly offline,
# handled by the offline classifier below).

# Tools that are pure Python regardless of their prefix (deterministic, no
# bridge round-trip). Substring match keeps the calculators in one rule.
_OFFLINE_SUBSTRINGS = ("_calc_", "calc_", "_compute_", "compute_")

# Pure Python tools filed under a category that is otherwise bridge-backed:
# the package checks compute over a package description and never reach an
# editor, so they work with nothing running.
_OFFLINE_BY_NAME = frozenset({
    # Altium Agent phase (c): files in, JSON out (place_apply is live).
    "ref_profile", "ref_compare", "place_plan", "place_check",
    # Altium Agent phase (d): the broker's file side (the live ones are
    # route_freerouting, route_import_ses and
    # route_set_via_tenting).
    "route_engines", "route_broker_plan", "route_export_dsn", "route_converge", "route_via_style",
    "route_freerouting_status",
    "lib_check_package", "lib_render_package",
    # Part sourcing queries part providers (registries, local libraries)
    # and datasheet fetch downloads a PDF; neither reaches the bridge.
    # (eda-agent filed these under a "parts" category that was offline.)
    "lib_search_parts", "lib_fetch_part", "lib_fetch_datasheet",
    # The route planner works on a board model, never on Altium.
    "route_plan", "route_plan_repairs",
    # Reads a DRC report file; never runs DRC.
    "analyze_drc_report",
})

# ...except calculators that read their input FROM THE BOARD. The
# substring rule above assumes a calc tool is pure maths; these query
# Altium first, so reporting them as offline sends anyone filtering for
# "works without Altium" straight into a bridge error.
_CALC_NEEDS_BRIDGE = frozenset({"pcb_calc_polygon_area"})

# design_* tools that are pure Python (per the design-agent surface: plan
# authoring, analysis, and derivation all run offline). The bridge-backed
# design tools are the explicit exceptions in _DESIGN_BRIDGE below.
_DESIGN_BRIDGE = frozenset(
    {
        "design_execute_plan",
        "design_audit_schematic",
        "design_validate",
        "design_review_snapshot",
        "design_snapshot_inventory",
        "design_datasheet_checklist",
        "design_learn_from_layout",
        # Both fetch board state over the bridge before reporting.
        "design_lint_report",
        "design_visual_review",
        # These three extract symbol geometry, or read the live sheet,
        # over the bridge. Each CATCHES the failure and answers anyway,
        # so with no Altium running they return a degraded result rather
        # than an error, and calling them offline is what sends somebody
        # to rely on that result.
        "design_preview_plan",
        "design_hints_from_sheet",
        "design_plan_from_sheet",
    }
)

# --- explicit interaction overrides (from the DelphiScript bridge audit) ----
# Only tools that are NOT plain readonly/silent under the default rules.
INTERACTION_OVERRIDES = {
    # Altium Agent: writes the DSN file it exports (into the outputs folder).
    "route_export_dsn": SILENT,
    # Non-suppressible modal dialogs that block the single-threaded loop.
    "proj_sync_pcb": MODAL,          # ECO / Update-PCB dialog
    "proj_sync_schematic": MODAL,    # Update-Schematic dialog
    "pcb_add_teardrops": MODAL,      # Teardrop dialog
    "pcb_remove_teardrops": MODAL,   # Teardrop dialog
    # The wizard and its ECO, which block the loop for as long as they
    # are up. This tool is the one that clicks them rather than a human,
    # but that does not make it silent: while it runs, the bridge cannot
    # answer, and anyone filtering for operations that keep the session
    # responsive must not be handed this one.
    "app_update_from_libraries": MODAL,
    # MEASURED MODAL during the live sweep of 2026-08-24. Each of these was
    # published as silent, so anything filtering for "keeps the session
    # responsive" was handed a tool that blocks the bridge until a human
    # answers a dialog. The dialog seen is named beside each one.
    # Design Rule Checker (TDesignRuleCheckForm). Altium exposes no
    # non-interactive DRC trigger, so the tool's only functional path is
    # the modal one: it refuses unless called with allow_modal=True.
    # Measured behind the dialog: a 30-minute dead loop to an 1800 s
    # client timeout. pcb_get_clearance_violations reads stored
    # violations instead and stays readonly.
    "pcb_run_drc": MODAL,
    "proj_export_pdf": MODAL,         # Preview PCB / print preview
    "proj_run_output": MODAL,         # Altium's exporter dialogs
    # These three raise "Unsaved Changes" whenever the target is dirty, and
    # that one is a WPF dialog whose buttons expose no window handles, so
    # app_press_dialog_button cannot answer it. Keyboard only.
    "lib_reload_library": MODAL,
    "proj_remove_document": MODAL,
    "proj_close": MODAL,
    # Succeeds but leaves the job incomplete.
    "pcb_place_components": PARTIAL,  # geometry only; needs pcb_build_from_project for nets
    # "diff" homograph: _READONLY_SUBSTRINGS carries "_diff_" for
    # COMPARISON tools (lib_diff_libraries, proj_get_differences), but
    # here it means DIFFERENTIAL. This one creates an object on the
    # board, and reporting it readonly tells anyone filtering for safe
    # operations the opposite of the truth.
    "pcb_create_diff_pair": SILENT,
    # Launches Altium's INTERACTIVE polygon mode: the user draws the
    # boundary by clicking. "silent" claimed it had completed a mutation,
    # so an agent would believe a polygon exists when none does.
    "pcb_start_polygon_placement": PARTIAL,
    # Renders the design and hands back an image; it changes nothing.
    # This one read as readonly only because it was WRONGLY classified
    # offline, and the offline->readonly shortcut carried it. Correcting
    # the maturity removed that shortcut and dropped it to "silent"
    # (mutates). State the truth explicitly rather than depending on a
    # side effect of another rule.
    "design_visual_review": READONLY,
    # Reports a pin's root, its electrical connection point, and every
    # object sitting on each. Pure inspection -- it exists precisely so a
    # caller can debug connectivity WITHOUT touching the sheet. The obj_
    # prefix defaults to "silent" (mutating), which is the opposite of
    # the truth here, so say so explicitly.
    "obj_explain_pin": READONLY,
    # Reads the board (or a saved board model) and measures it: overlaps,
    # gaps, connectivity, corners, return vias, plane islands. Nothing is
    # written. The pcb_ prefix defaults to silent, which would tell anyone
    # filtering for safe operations that an audit edits the board.
    "pcb_layout_audit": READONLY,
    # Reads a database library's declared tables and fields. The lib_ prefix
    # does not make that clear to the classifier; it writes nothing.
    "lib_dblib_info": READONLY,
    # lib_fetch_part writes library files when given download_dir. The
    # "parts" category is offline, and offline falls back to READONLY,
    # which would advertise a tool that touches the filesystem as
    # read-only. Every other file-writing tool here (lib_easyeda_import,
    # lib_extract_cse_zip, proj_export_pdf, pcb_render_svg) is SILENT,
    # so match them. lib_search_parts never writes and stays readonly.
    "lib_fetch_part": SILENT,
    # Writes the downloaded PDF and its sidecar into the workspace cache.
    # Filed under parts, which is offline and so falls back to READONLY;
    # a tool that writes files is SILENT here, as lib_fetch_part is.
    "lib_fetch_datasheet": SILENT,
    # Writes the SVG (and a PNG) it draws, as pcb_render_svg does.
    "lib_render_package": SILENT,
    # Reads the footprint's pads, primitives and the library's layers and
    # writes nothing. The lib_ prefix says nothing about that, and the
    # default would publish an audit as a tool that edits the library.
    "lib_audit_footprint_vs_package": READONLY,
    # Altium Agent: reads processes, windows and a ping; changes nothing.
    "app_diagnose": READONLY,
}

# --- explicit maturity overrides -------------------------------------------
# Populate from the Phase 1.3 nightly live-run as tools become verified.
MATURITY_OVERRIDES: dict[str, str] = {
    # Empty on purpose, and it should stay that way for SIMULATOR: that
    # tier is now derived from _SIMULATOR_TOOLS, which is measured
    # against the simulator and guarded by a test. Pinning a tool here
    # would let a claim outlive the handler that justified it, which is
    # the failure this file just came out of.
    #
    # Reserved for the Phase 1.3 nightly live-run, whose per-tool results
    # ARE external evidence and cannot be derived from the source.
}

# Readonly name markers. A tool whose name starts with / contains one of
# these does not mutate the design.
_READONLY_PREFIXES = ("get_", "list_", "find_", "query_", "audit_")
_READONLY_SUBSTRINGS = (
    "_get_", "_list_", "_find_", "_query_", "_describe_", "_preview_",
    "_review_", "_validate", "_compare_", "_calc_", "_compute_", "_snapshot",
    "_checklist", "_readiness", "_stats", "_search", "_diff_", "_report",
)




def category_of(name: str) -> str:
    """Tool category from an explicit name, else the prefix.

    ``other`` only when neither matches, which a test treats as a defect
    rather than an acceptable default.
    """
    for prefix, cat in _CATEGORY_BY_PREFIX:
        if name.startswith(prefix):
            return cat
    return "other"


def _ported_offline_names() -> frozenset[str]:
    """The altium-mcp-pro file tools (tools/offline): files in, report out."""
    try:
        from .offline import offline_tool_names
    except Exception:  # noqa: BLE001 - metadata must never fail to import
        return frozenset()
    return offline_tool_names()


def _is_offline(name: str, category: str) -> bool:
    if name in _ported_offline_names():
        return True
    if category in _OFFLINE_CATEGORIES:
        return True
    if name in _OFFLINE_BY_NAME:
        return True
    if name in _CALC_NEEDS_BRIDGE:
        return False
    if any(s in name for s in _OFFLINE_SUBSTRINGS):
        return True
    if category == "design" and name not in _DESIGN_BRIDGE:
        return True
    return False


_LIVE_LEDGER: "dict[str, dict] | None" = None


def live_verified_records() -> dict[str, dict]:
    """Altium Agent's own live-verification records, keyed by tool name.

    Read from ``live_verified.json`` beside this module. Each record names
    the Altium build, the date, the script stamp and the evidence file,
    so the badge always points at something a reader can check.
    """
    global _LIVE_LEDGER
    if _LIVE_LEDGER is None:
        import json
        import pathlib

        path = pathlib.Path(__file__).with_name("live_verified.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _LIVE_LEDGER = {r["tool"]: r for r in data.get("records", [])
                        if isinstance(r, dict) and r.get("tool")}
    return _LIVE_LEDGER


def maturity_of(name: str, category: str | None = None) -> str:
    if name in MATURITY_OVERRIDES:
        return MATURITY_OVERRIDES[name]
    category = category or category_of(name)
    if _is_offline(name, category):
        return OFFLINE
    if name in live_verified_records():
        return LIVE_VERIFIED
    if name in _SIMULATOR_TOOLS:
        return SIMULATOR
    return LIVE_UNVERIFIED


def interaction_of(name: str, category: str | None = None) -> str:
    if name in INTERACTION_OVERRIDES:
        return INTERACTION_OVERRIDES[name]
    if name in _ported_offline_names():
        return READONLY
    category = category or category_of(name)
    if category == "audit":
        return READONLY
    if name.startswith(_READONLY_PREFIXES):
        return READONLY
    if any(s in name for s in _READONLY_SUBSTRINGS):
        return READONLY
    # Offline calculators/authoring tools don't touch Altium at all -> treat
    # as readonly with respect to the live session.
    if maturity_of(name, category) == OFFLINE:
        return READONLY
    return SILENT


def tool_metadata(name: str) -> dict[str, str]:
    """Full metadata record for one tool name."""
    category = category_of(name)
    return {
        "name": name,
        "category": category,
        "maturity": maturity_of(name, category),
        "interaction": interaction_of(name, category),
    }


def catalog(names) -> list[dict[str, str]]:
    """Metadata for many tools, sorted by (category, name)."""
    records = [tool_metadata(n) for n in names]
    records.sort(key=lambda r: (r["category"], r["name"]))
    return records
