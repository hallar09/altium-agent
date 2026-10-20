# SPDX-License-Identifier: Apache-2.0
"""Gate reviews, undo to a stage, and design_status (Altium Agent, phase e)."""

from __future__ import annotations

import asyncio
from typing import Any

from .house_style import _offline


def _journal(session_id: str):
    from ..config import get_config
    from ..design.session import SessionStore

    store = SessionStore(get_config().workspace_dir / "design_sessions")
    return store.get(session_id) if session_id else store.active()


def register_design_gate_tools(mcp) -> None:

    @mcp.tool()
    @_offline
    def design_gate_review(gate: str, project: str | None = None, session_id: str = "",
                           sheets: list[str] | None = None, drc_report: str | None = None,
                           profile: Any = None, width_px: int = 1600) -> dict[str, Any]:
        """Look before claiming: pictures, numbers and a rubric for a stage gate (no Altium).

        Renders the SAVED design to PNG (board top and bottom, schematic
        sheets) with the findings numbered on the pictures (overlaps, parts
        off the board, decaps far from their IC, connectors off the edge,
        DRC copper problems, and the ratsnest of unrouted connections as
        dashed lines), measures the gate's numbers (ERC, unrouted, DRC,
        style) and returns the gate's rubric. Read every PNG, answer each
        rubric item from what you see, then log the stage result with the
        numbers. Records the pictures in the session journal.

        Args:
            gate: "schematic", "placement", "routing" or "verification".
            project: The .PrjPcb; default the session's or the active one.
            session_id: The design session; default the active one.
            sheets: Only these schematic sheets (names); default all.
            drc_report: A DRC report to mark; default the newest one in the
                project folder.
            profile: A house-style profile to compare with (routing and
                verification gates); default the session's.
            width_px: PNG width in pixels.
        """
        from ..design.gate_review import review
        from ..design.stage_checkpoints import project_for, session_profile

        j = _journal(session_id)
        proj = project_for(j, project)
        prof = profile if profile is not None else (session_profile(j) if j else None)
        out = review(str(proj), gate, sheets=sheets, drc_report=drc_report, profile=prof, width=width_px)
        if j is not None:
            for im in out["images"]:
                j.artifact(im.get("png") or im["svg"], kind=f"render:{gate}")
            out["session_id"] = j.session_id
        return out

    @mcp.tool()
    async def design_undo_to_stage(stage: str, session_id: str = "", confirm: bool = False,
                                   altium_closed: bool = False) -> dict[str, Any]:
        """Put the design back to how it was when a stage started, in one call.

        Takes a safety checkpoint of the current files (so the undo can be
        undone), closes the project in Altium WITHOUT saving, restores the
        files from the checkpoint taken on entering ``stage`` (files made
        since are removed), records a rewind in the journal (this stage and
        every later one are open again) and reopens the project. Without
        ``confirm`` it only says what it would do.

        Args:
            stage: A pipeline stage that was entered with design_session_log
                (stage_enter) in a session started with a project.
            session_id: The design session; default the active one.
            confirm: Must be True to change anything.
            altium_closed: Say True if the project is already closed in
                Altium (or Altium is not running); then Altium is not asked.
        """
        from ..bridge import get_bridge
        from ..design.session import STAGES
        from ..design.stage_checkpoints import (
            project_for,
            stage_checkpoints,
            undo_files,
        )
        from ..placement.service import is_protected_design

        j = _journal(session_id)
        if j is None:
            return {"ok": False, "error": "no design session; start one with design_session_start(project=...)"}
        if stage not in STAGES:
            return {"ok": False, "error": f"stage must be one of {list(STAGES)}"}
        proj = project_for(j, None)
        if is_protected_design(str(proj)):
            return {"ok": False, "refused": f"{proj} is a sample or installed design; it is never edited"}
        cps = stage_checkpoints(j)
        if stage not in cps:
            return {"ok": False, "error": f"no checkpoint was taken on entering {stage}",
                    "stages_with_checkpoints": [s for s in STAGES if s in cps]}
        if not confirm:
            return {"ok": True, "would": {"project": str(proj), "restore_checkpoint": cps[stage],
                                          "close_in_altium_without_saving": not altium_closed,
                                          "reopen": not altium_closed},
                    "hint": "Pass confirm=true to undo. Unsaved Altium edits are discarded."}
        out: dict[str, Any] = {}
        bridge = None
        if not altium_closed:
            bridge = get_bridge()
            try:
                out["closed"] = await bridge.send_command_async(
                    "project.close", {"project_path": str(proj), "save": False}, timeout=60.0)
            except Exception as exc:  # noqa: BLE001 - reported, nothing restored
                return {"ok": False, "error": f"could not close the project in Altium: {exc}",
                        "hint": "Run proj_close(project_path, save=false) (it answers Altium's save prompt), "
                                "then call this again with altium_closed=true."}
        res = await asyncio.to_thread(undo_files, j, stage, proj)
        out.update(res)
        if res.get("ok") and bridge is not None:
            try:
                out["reopened"] = await bridge.send_command_async(
                    "project.open", {"project_path": str(proj)}, timeout=120.0)
            except Exception as exc:  # noqa: BLE001
                out["reopened"] = {"error": str(exc), "hint": "open it with proj_open"}
        out["state"] = {"next_stage": j.state().next_stage, "current_stage": j.state().current_stage}
        return out

    @mcp.tool()
    @_offline
    def design_status(project: str | None = None, session_id: str = "", profile: Any = None,
                      dfm_profile: str | None = None) -> dict[str, Any]:
        """Where the design stands, in one call (no Altium).

        Stage, open question and per-stage checkpoints (journal); ERC
        counts; unrouted connections; the newest DRC report's counts (and
        whether it is older than the board); the DFM verdict for the fab
        profile; departures from the house-style profile; the next action
        from the state machine; and the open issues in a list. From the
        saved files: save in Altium first.

        Args:
            project: The .PrjPcb; default the session's or the active one.
            session_id: The design session; default the active one.
            profile: House-style profile; default the session's.
            dfm_profile: Fab profile for DFM; default the session default.
        """
        from ..design.status import status

        return status(_journal(session_id), project, profile=profile, dfm_profile=dfm_profile)
