# SPDX-License-Identifier: Apache-2.0
"""The routing broker's tools (Altium Agent, phase d).

route_engines, route_broker_plan, route_export_dsn, route_converge and
route_via_style read files only. route_freerouting saves the focused board
and starts Freerouting as an external process (a job); route_import_ses
writes its tracks and vias; route_set_via_tenting tents vias and confirms it
from the saved file. Each live tool refuses a sample or installed design and
a focused board that is not the one named.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .house_style import _offline

CHUNK = 400


async def _focused(bridge, expect_file: str) -> tuple[str | None, str | None]:
    """(focused file, error)."""
    from ..layout.read_altium import _same_file

    head = await bridge.send_command_async("pcb.get_layout_model", {"section": "board"}, timeout=120.0)
    focused = str((head or {}).get("file", ""))
    if not _same_file(focused, expect_file):
        return None, f"the focused board is {focused or '(none)'}, not {expect_file}; nothing was changed"
    return focused, None


def _protected(expect_file: str) -> str | None:
    from ..placement.service import is_protected_design

    if is_protected_design(expect_file):
        return (f"{expect_file} is a sample or installed design; Altium Agent edits only your own copies. "
                "Copy the project to a folder of yours and open the copy.")
    return None


def register_routing_broker_tools(mcp) -> None:
    from ..routing import service as svc

    @mcp.tool()
    @_offline
    def route_engines() -> dict[str, Any]:
        """Which routing engines the broker can use here, and why not when not.

        in-house (always), Freerouting (when you installed it: GPL-3.0, run
        as a separate process, never shipped; the Java it needs is checked),
        and Altium's own Situs and interactive router (used by you; the
        broker reads the saved result).
        """
        return svc.engines_status()

    @mcp.tool()
    @_offline
    def route_broker_plan(project: str | None = None, pcb: str | None = None, profile: Any = None,
                          engines: list[str] | None = None, batch_size: int = 0, max_passes: int = 50,
                          name: str | None = None) -> dict[str, Any]:
        """Plan how to route a board: which nets, in which order, by which engine (no Altium).

        Differential pairs first (in-house, coupled); then clocks, crystal
        and short local nets (in-house); then power and signals (Freerouting
        when installed, else in-house); pours are left to the pours. Copper
        already on the board is protected: its nets are locked first and
        never ripped up. Ends with repour, DRC, route_converge and, with a
        profile, the via style and ref_compare. Each step is an exact tool
        call with its reason. Saves the plan JSON.

        Args:
            project: The .PrjPcb (or the active project).
            pcb: A .PcbDoc instead of the project's first.
            profile: A house-style profile (ref_profile) for the via style and
                the final comparison.
            engines: Subset of ["inhouse", "freerouting", "altium"].
            batch_size: Freerouting nets per run; 0 = the whole bulk at once.
            max_passes: Freerouting passes per run.
            name: File name for the plan.
        """
        return svc.route_broker_plan(project, pcb, profile, engines, batch_size, max_passes, name)

    @mcp.tool()
    @_offline
    def route_export_dsn(project: str | None = None, pcb: str | None = None,
                         route_only: list[str] | None = None, keep_existing: bool = True,
                         track_width_mm: float | None = None, clearance_mm: float | None = None,
                         via_diameter_mm: float | None = None, via_hole_mm: float | None = None,
                         skip_nets: list[str] | None = None,
                         net_widths_mm: dict[str, float] | None = None) -> dict[str, Any]:
        """Write a Specctra DSN of the saved board (no Altium).

        Pads, outline, nets, rules (widths, clearance, via from the board's
        rules unless given), pours as planes, and the copper already there
        as fixed wiring. With a list of nets to route, every other net is
        exported as fixed (a batch). For any Specctra router; route_freerouting runs Freerouting.
        """
        from ..placement.service import load_board

        b = load_board(project, pcb)
        opts = svc.dsn_options(route_only, keep_existing, track_width_mm, clearance_mm, via_diameter_mm,
                               via_hole_mm, None, skip_nets, net_widths_mm)
        return svc.export(str(b.doc.path), opts)

    @mcp.tool()
    async def route_freerouting(expect_file: str, route_only: list[str] | None = None,
                                max_passes: int = 50, timeout_s: int = 1200, optimize: bool = False,
                                save_first: bool = True) -> dict[str, Any]:
        """Save the focused board and start Freerouting on it, as a job.

        Freerouting (GPL-3.0) is yours to install and runs as a separate
        process with no window and no analytics; nothing of it is in Altium
        Agent. The DSN is exported from the SAVED board (so save_first).
        Returns a job id at once; poll route_freerouting_status, then
        route_import_ses writes the result. Nothing on the board changes
        until then.

        Args:
            expect_file: Full path of the board; refused if another is focused.
            route_only: Route only these nets (every other net stays as it
                is, as an obstacle); default every unrouted net.
            max_passes: Freerouting's routing passes.
            timeout_s: Stop Freerouting after this long.
            optimize: Run Freerouting's optimizer after a complete route
                (slow).
            save_first: Save the board in Altium first (default True).
        """
        from ..bridge import get_bridge
        from ..design.jobs import get_job_store
        from ..routing.freerouting import find

        why = _protected(expect_file)
        if why:
            return {"ok": False, "refused": why}
        f = find()
        if f is None or not f.usable:
            return {"ok": False, "error": "Freerouting is not usable here",
                    "detail": svc.engines_status()["freerouting"]}
        bridge = get_bridge()
        focused, err = await _focused(bridge, expect_file)
        if err:
            return {"ok": False, "refused": err}
        if save_first:
            saved = await bridge.send_command_async("application.save_document", {"file_path": focused},
                                                    timeout=60.0)
            if isinstance(saved, dict) and saved.get("saved") is False:
                return {"ok": False, "error": "the board was not saved", "save": saved}
        tag = "batch" if route_only else "all"
        opts = svc.dsn_options(route_only, True)
        try:
            ex = await asyncio.to_thread(svc.export, expect_file, opts, tag)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return {"ok": False, "error": f"DSN export failed: {exc}"}
        extra = ["--router.fanout.enabled=false",
                 "--router.autorouter.ignore_net_classes=AGENT_FIXED"] if route_only else []
        params = {**ex, "route_only": list(route_only or []), "max_passes": max_passes,
                  "timeout_s": timeout_s, "optimize": optimize, "extra": extra}
        job_id = get_job_store().submit("freerouting", svc.freerouting_job, params)
        return {"ok": True, "job_id": job_id, "dsn": ex["dsn"], "nets": ex["nets"], "rules": ex["rules"],
                "engine": f.to_dict(),
                "next": "poll route_freerouting_status; when done, route_import_ses with this job id"}

    @mcp.tool()
    async def route_freerouting_status(job_id: str) -> dict[str, Any]:
        """A Freerouting job's state, and the end of Freerouting's log."""
        from pathlib import Path

        from ..design.jobs import get_job_store

        store = get_job_store()
        rec = store.get(job_id)
        if rec is None or rec.kind != "freerouting":
            return {"ok": False, "error": f"no freerouting job {job_id!r}"}
        out: dict[str, Any] = {"ok": True, **rec.summary()}
        if rec.status == "done":
            out["freerouting"] = {k: v for k, v in (rec.result or {}).get("freerouting", {}).items()
                                  if k != "log_tail"}
        try:
            log = Path((rec.result or {}).get("ses") or "").with_suffix(".log")
            if log.is_file():
                out["log_tail"] = log.read_text(encoding="utf-8", errors="replace")[-1200:]
        except OSError:
            pass
        return out

    @mcp.tool()
    async def route_import_ses(job_id: str, expect_file: str, checkpoint: bool = True,
                               repour: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Write a finished Freerouting job's new tracks and vias to the focused board.

        Only copper that is not already there is written (hand routing is
        never doubled); tracks narrower than their net's minimum width are
        widened back; with a batch, only the batch's nets. Positions are
        absolute mils (board origin + SES); refused if the origin moved.

        Args:
            job_id: A done route_freerouting job.
            expect_file: Full path of the board the job routed.
            checkpoint: Snapshot the board folder first.
            repour: Repour the polygons afterwards.
            dry_run: Count what would be written; write nothing.
        """
        from pathlib import Path

        from ..bridge import get_bridge
        from ..design.jobs import get_job_store
        from ..layout.read_altium import _same_file
        from ..offline.errors import OfflineError

        why = _protected(expect_file)
        if why:
            return {"ok": False, "refused": why}
        rec = get_job_store().get(job_id)
        if rec is None or rec.kind != "freerouting":
            return {"ok": False, "error": f"no freerouting job {job_id!r}"}
        if rec.status != "done":
            return {"ok": False, "error": f"job {job_id} is {rec.status}", "job": rec.summary()}
        job = rec.result or {}
        if not _same_file(job.get("source", ""), expect_file):
            return {"ok": False, "refused": f"job {job_id} routed {job.get('source')}, not {expect_file}"}
        try:
            rows = await asyncio.to_thread(svc.ses_rows_for, job)
        except OfflineError as exc:
            return {"ok": False, **exc.to_dict()}
        out: dict[str, Any] = {"ok": True, "rows": rows.summary()}
        if dry_run:
            out["applied"] = False
            return out
        bridge = get_bridge()
        _, err = await _focused(bridge, expect_file)
        if err:
            return {"ok": False, "refused": err}
        if checkpoint:
            from ..checkpoint import CheckpointStore
            from ..config import get_config
            cp = CheckpointStore(get_config().workspace_dir / "checkpoints")
            out["checkpoint"] = cp.create(Path(expect_file).parent, label=f"before route_import_ses {job_id}").summary()
        placed = {"tracks": {"placed": 0, "failed": 0}, "vias": {"placed": 0, "failed": 0}}
        unknown: set[str] = set()
        for kind, cmd, key in (("tracks", "pcb.place_tracks", "tracks"), ("vias", "pcb.place_vias", "vias")):
            items = getattr(rows, kind)
            for i in range(0, len(items), CHUNK):
                r = await bridge.send_command_async(cmd, {key: "|".join(items[i:i + CHUNK])}, timeout=300.0)
                placed[kind]["placed"] += int((r or {}).get("placed", 0))
                placed[kind]["failed"] += int((r or {}).get("failed", 0))
                if (r or {}).get("unknown_layers"):
                    unknown.update(str(r["unknown_layers"]).split(", "))
        out.update(placed)
        out["expected"] = {"tracks": len(rows.tracks), "vias": len(rows.vias)}
        if unknown:
            out["unknown_layers"] = sorted(unknown)
        if repour:
            await bridge.send_command_async("pcb.repour_polygons", {}, timeout=600.0)
            out["repoured"] = True
        out["applied"] = True
        out["next"] = "pcb_run_drc, then route_converge with the plan and the DRC result; save when content."
        return out

    @mcp.tool()
    @_offline
    def route_converge(plan: str, drc: Any, max_rounds: int = 3) -> dict[str, Any]:
        """The next round of clash repairs after routing (no Altium).

        From pcb_run_drc's violations: rip up and re-route only nets the
        broker's plan routed, and only their unlocked copper (hand routing
        stays); a net goes to the other engine than last time; clashes on
        protected copper, nets ripped twice, and rounds beyond max_rounds
        are escalated to you. Returns the steps of the round, or done.

        Args:
            plan: The plan JSON path from route_broker_plan.
            drc: The pcb_run_drc result (or its violations list).
            max_rounds: Rounds before giving up to a person.
        """
        from ..routing.converge import converge

        p, path = svc.load_plan(plan)
        return converge(p, drc, plan_path=path, max_rounds=max_rounds)

    @mcp.tool()
    @_offline
    def route_via_style(project: str | None = None, pcb: str | None = None,
                        profile: Any = None) -> dict[str, Any]:
        """Via sizes and tenting on the saved board, against the house style (no Altium).

        The style is the profile's (ref_profile), else the board's
        RoutingVias rule. Tenting is read from the saved file's via records,
        so it shows what Altium saved.
        """
        return svc.via_style(project, pcb, profile)

    @mcp.tool()
    async def route_set_via_tenting(expect_file: str, top: bool | None = None,
                                    bottom: bool | None = None, net: str = "",
                                    diameter_mm: float | None = None, dry_run: bool = True,
                                    save_and_verify: bool = True) -> dict[str, Any]:
        """Tent (or untent) the focused board's free vias, and confirm it from the saved file.

        Sets each matching via's mask mode to manual (AD26 ignores tenting
        otherwise), sets the tenting, reads it back in Altium, then saves
        and reads the via records of the saved file. dry_run (the default)
        changes nothing and reports the current state.

        Args:
            expect_file: Full path of the board; refused if another is focused.
            top: Tent the top side (True), untent (False), leave (None).
            bottom: The same for the bottom side.
            net: Only vias on this net.
            diameter_mm: Only vias of this diameter (within 0.0127 mm).
            dry_run: Report only (default True).
            save_and_verify: Save the board and check the file afterwards.
        """
        from ..bridge import get_bridge
        from ..units import MILS_PER_MM

        why = _protected(expect_file)
        if why and not dry_run:
            return {"ok": False, "refused": why}
        if not dry_run and top is None and bottom is None:
            return {"ok": False, "error": "say top and/or bottom"}
        bridge = get_bridge()
        focused, err = await _focused(bridge, expect_file)
        if err:
            return {"ok": False, "refused": err}
        params: dict[str, str] = {"dry_run": "true" if dry_run else "false"}
        if top is not None:
            params["top"] = "true" if top else "false"
        if bottom is not None:
            params["bottom"] = "true" if bottom else "false"
        if net.strip():
            params["net"] = net.strip()
        if diameter_mm is not None:
            params["diameter_mils"] = f"{diameter_mm * MILS_PER_MM:.3f}"
        res = await bridge.send_command_async("pcb.set_via_tenting", params, timeout=300.0)
        out: dict[str, Any] = {"ok": True, "altium": res}
        if dry_run or not save_and_verify:
            return out
        saved = await bridge.send_command_async("application.save_document", {"file_path": focused},
                                                timeout=60.0)
        out["save"] = saved
        out["file"] = await asyncio.to_thread(svc.tenting_read_back, expect_file, net=net.strip() or None,
                                              diameter_mm=diameter_mm, top=top, bottom=bottom)
        out["verified"] = bool(out["file"].get("verified"))
        if not out["verified"]:
            out["ok"] = False
            out["note"] = "The saved file does not carry the tenting asked for; see file.differ_at_mm."
        return out
