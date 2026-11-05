# SPDX-License-Identifier: Apache-2.0
"""The routing broker's tools (Altium Agent, phase d).

route_engines, route_broker_plan, route_converge, route_via_style and
route_power_fanout read files only. route_import_ses writes a fan-out's or a
routing session's tracks and vias to the board; route_set_via_tenting tents
vias and confirms it from the saved file. Each live tool refuses a sample or
installed design and a focused board that is not the one named.

Routing itself is the agent's, net by net, in a routing session
(tools/interactive.py): no external autorouter is run.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .house_style import _offline

CHUNK = 400
#: Jobs whose result is an SES that route_import_ses writes and after_jobs builds on.
ROUTED_JOBS = ("fanout", "session")


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
        """Which routing engines the broker can use here.

        The in-house engine (always; route_session_route drives it net by
        net), and Altium's own Situs and interactive router (used in Altium;
        the broker reads the saved result).
        """
        return svc.engines_status()

    @mcp.tool()
    @_offline
    def route_broker_plan(project: str | None = None, pcb: str | None = None, profile: Any = None,
                          engines: list[str] | None = None, name: str | None = None,
                          reference: str | None = None) -> dict[str, Any]:
        """Plan how to route a board: which nets, in which order, by which engine (no Altium).

        Differential pairs first (coupled); then clocks, crystal and short
        local nets; then the power fan-out (a via from each power and ground
        pad to its plane); then the rest; stitching last. Copper already on
        the board is protected: its nets are locked first and never ripped
        up. Ends with repour, DRC, route_converge and, with a profile, the
        via style and ref_compare. Each step is an exact tool call with its
        reason. Saves the plan JSON. Read it as the order to work in; the
        routing skill does the work net by net in a routing session.

        Args:
            project: The .PrjPcb (or the active project).
            pcb: A .PcbDoc instead of the project's first.
            profile: A house-style profile (ref_profile) for the via style and
                the final comparison.
            engines: Subset of ["inhouse", "altium"].
            name: File name for the plan.
            reference: The reference board of a re-layout (default the
                profile's source). With the reference placement kept, the
                big part's escape is copied from it first and clashing nets
                are copied from it in route_converge (refcopy).
        """
        return svc.route_broker_plan(project, pcb, profile, engines, name, reference)

    @mcp.tool()
    async def route_power_fanout(expect_file: str, nets: list[str] | None = None,
                                 via_diameter_mm: float | None = None, via_hole_mm: float | None = None,
                                 stub_width_mm: float | None = None, clearance_mm: float | None = None,
                                 max_reach_mm: float = 1.5, pads: bool = True,
                                 stitch_spacing_mm: float | None = None,
                                 after_jobs: list[str] | None = None) -> dict[str, Any]:
        """A short via from each power and ground pad to its plane (no Altium).

        Power and ground are carried by pours and planes. A surface-mount
        pad on a layer its net is poured on is joined by the pour; every
        other pad gets a via beside it, with a stub, on the net's plane:
        the nearest spot that keeps the board's clearance to every other
        net on every layer and to the edge. A same-net via already in reach
        is shared. Run it after the critical nets and BEFORE the bulk of the
        signals, so the vias get their spots; route_import_ses writes them.

        Reads the SAVED board, so save first in Altium. Pads it cannot reach
        are listed (unresolved), never forced: route those nets afterwards
        (route_session_route).

        Args:
            expect_file: Full path of the board.
            nets: Nets to fan out. Default: every net carried by an internal
                plane or by pours covering a tenth of the board or more.
            via_diameter_mm, via_hole_mm: Default: the RoutingVias rule.
            stub_width_mm: Pad-to-via stub. Default: the Width rule.
            clearance_mm: Default: the Clearance rule.
            max_reach_mm: Farthest a via sits from its pad's edge.
            pads: Fan out the pads (default). Pads a track or via of their
                net already reaches are left alone, so running it again
                adds nothing twice.
            stitch_spacing_mm: Also stitch the net's pours on different
                layers together, a via every this many mm where two of them
                overlap (and clearance allows). Do it AFTER the signals:
                pads=false, stitch_spacing_mm=5.
            after_jobs: Finished routing jobs whose copper is on the board
                first, when it has not been imported yet.
        """
        from ..design.jobs import get_job_store

        why = _protected(expect_file)
        if why:
            return {"ok": False, "refused": why}
        store = get_job_store()
        prior = []
        for jid in after_jobs or []:
            rec = store.get(jid)
            if rec is None or rec.kind not in ROUTED_JOBS or rec.status != "done":
                return {"ok": False, "error": f"after_jobs: {jid!r} is not a finished routing job"}
            prior.append({**(rec.result or {}), "job_id": jid})
        params = {"source": expect_file, "nets": nets, "via_diameter_mm": via_diameter_mm,
                  "via_hole_mm": via_hole_mm, "stub_width_mm": stub_width_mm, "clearance_mm": clearance_mm,
                  "max_reach_mm": max_reach_mm, "pads": pads, "stitch_spacing_mm": stitch_spacing_mm,
                  "after": prior}
        job_id = store.submit("fanout", svc.fanout_job, params)
        await asyncio.to_thread(store.wait, job_id, 600)
        rec = store.get(job_id)
        if rec is None or rec.status != "done":
            return {"ok": False, "job_id": job_id, "error": (rec.error if rec else None) or "the fan-out did not finish"}
        out = rec.result or {}
        return {"ok": True, "job_id": job_id, "ses": out["ses"], **out["fanout"],
                "next": "route_import_ses with this job id (in Altium), then route the signals"}

    @mcp.tool()
    async def route_import_ses(job_id: str, expect_file: str, checkpoint: bool = True,
                               repour: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Write a finished fan-out's or routing session's tracks and vias to the focused board.

        Only copper that is not already there is written (hand routing is
        never doubled); tracks narrower than their net's minimum width are
        widened back; with a batch, only the batch's nets. Positions are
        absolute mils (board origin + SES); refused if the origin moved.

        Args:
            job_id: The job route_power_fanout or route_session_export returned.
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
        if rec is None or rec.kind not in ROUTED_JOBS:
            return {"ok": False, "error": f"no routing job {job_id!r}"}
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
    async def route_refcopy(expect_file: str, reference: str, nets: list[str] | None = None,
                            escape: str | None = None, margin_mm: float = 0.5, lock: bool = True,
                            dry_run: bool = False, checkpoint: bool = True) -> dict[str, Any]:
        """Copy routing from a reference board onto the focused copy (BoardKit's refcopy).

        For a re-layout that keeps the reference's part positions: copies the
        reference's own tracks, arcs (as short segments) and vias of ``nets``,
        and the escape copper of the part ``escape`` (its nets' copper wholly
        inside its pads' box grown by ``margin_mm``), then locks those nets.
        Refuses when any part those nets touch is not where the reference has
        it. Copper already on the board is not written twice. The reference
        is only read (an Altium sample may be the reference).

        Args:
            expect_file: Full path of the focused board (your copy, saved).
            reference: The reference .PcbDoc (or its .PrjPcb).
            nets: Nets to copy whole.
            escape: A part whose escape routing to copy (e.g. the big MCU).
            margin_mm: How far round the part's pads the escape window reaches.
            lock: Lock the copied nets' routing afterwards.
            dry_run: Count only.
            checkpoint: Snapshot the board folder first.
        """
        from pathlib import Path

        from ..bridge import get_bridge
        from ..offline.errors import OfflineError
        from ..offline.files.pcbdoc import read_pcbdoc
        from ..placement.service import load_board
        from ..routing.refcopy import plan_copy

        why = _protected(expect_file)
        if why:
            return {"ok": False, "refused": why}
        if not nets and not escape:
            return {"ok": False, "error": "name nets to copy, an escape part, or both"}
        try:
            ref = (await asyncio.to_thread(load_board, reference if reference.lower().endswith(".prjpcb") else None,
                                           None if reference.lower().endswith(".prjpcb") else reference)).doc
            target = await asyncio.to_thread(read_pcbdoc, Path(expect_file))
            rows = await asyncio.to_thread(plan_copy, ref, target, nets, escape, margin_mm)
        except (OfflineError, ValueError, FileNotFoundError) as exc:
            return {"ok": False, "error": str(exc)}
        out: dict[str, Any] = {"ok": True, "copy": rows.summary()}
        if rows.unknown_nets:
            return {"ok": False, "error": f"not nets of the reference: {rows.unknown_nets}", **out}
        if rows.moved_parts:
            return {"ok": False, "refused": "parts on these nets are not where the reference has them; copied "
                                            "copper would be wrong (reference placement only)", **out}
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
            out["checkpoint"] = cp.create(Path(expect_file).parent, label="before route_refcopy").summary()
        placed = {"tracks": {"placed": 0, "failed": 0}, "vias": {"placed": 0, "failed": 0}}
        for kind, cmd in (("tracks", "pcb.place_tracks"), ("vias", "pcb.place_vias")):
            items = getattr(rows, kind)
            for i in range(0, len(items), CHUNK):
                r = await bridge.send_command_async(cmd, {kind: "|".join(items[i:i + CHUNK])}, timeout=300.0)
                placed[kind]["placed"] += int((r or {}).get("placed", 0))
                placed[kind]["failed"] += int((r or {}).get("failed", 0))
        out.update(placed)
        if lock and rows.nets:
            out["locked"] = await bridge.send_command_async(
                "pcb.lock_net_routing", {"nets": "|".join(sorted(rows.nets)), "lock": "true",
                                         "lock_components": "false"})
        out["applied"] = True
        out["next"] = "Save, then pcb_run_drc and route_converge; copied nets are locked."
        return out

    @mcp.tool()
    async def route_situs(expect_file: str, wait_s: int = 600, settle_polls: int = 3,
                          save: bool = True) -> dict[str, Any]:
        """Let Altium's Situs autorouter finish the connections still open (live).

        Every component selected, then Route > Auto Route > Connections On
        Selected Components: no dialog, the board's own routing strategy
        (Route > Auto Route > Setup), locked copper left alone. Situs routes on
        after the command returns, so this polls the copper counts until they
        stop changing (``settle_polls`` equal readings 5 s apart, or
        ``wait_s``), then saves. Use it as the finisher after the broker's
        bulk step, not as a first pass on a dense board.

        Args:
            expect_file: Full path of the board; refused if another is focused.
            wait_s: Longest wait for Situs to settle.
            settle_polls: Equal readings in a row that count as finished.
            save: Save the board afterwards.
        """
        import time as _time

        from ..bridge import get_bridge

        why = _protected(expect_file)
        if why:
            return {"ok": False, "refused": why}
        bridge = get_bridge()
        focused, err = await _focused(bridge, expect_file)
        if err:
            return {"ok": False, "refused": err}
        start = await bridge.send_command_async("pcb.situs_route", {"expect_file": focused}, timeout=300.0)
        last, same, polls = None, 0, 0
        t0 = _time.monotonic()
        while _time.monotonic() - t0 < wait_s:
            await asyncio.sleep(5)
            st = await bridge.send_command_async("pcb.route_status", {}, timeout=60.0)
            polls += 1
            now = (st or {}).get("tracks"), (st or {}).get("arcs"), (st or {}).get("vias")
            same = same + 1 if now == last else 0
            last = now
            if same >= settle_polls - 1:
                break
        settled = same >= settle_polls - 1
        out: dict[str, Any] = {"ok": True, "started": start, "settled": settled, "polls": polls,
                               "copper": {"tracks": last[0], "arcs": last[1], "vias": last[2]} if last else None}
        if not settled:
            out["note"] = f"Situs was still changing the board after {wait_s} s; poll again or stop it in Altium."
        if save and settled:
            out["save"] = await bridge.send_command_async("application.save_document", {"file_path": focused},
                                                          timeout=60.0)
        out["next"] = "pcb_repour_polygons, pcb_run_drc, then route_converge."
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
