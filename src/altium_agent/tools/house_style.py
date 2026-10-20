# SPDX-License-Identifier: Apache-2.0
"""House style and placement tools (Altium Agent, phase c).

ref_profile, ref_compare, place_plan and place_check read files only and
work without Altium. place_apply is the one live tool: it moves the parts
of the focused board to a checked plan, and only on the user's own copy.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from typing import Any


def _offline(fn: Callable) -> Callable:
    """Run file work in a thread; typed errors become ``ok: false``."""
    from ..offline.errors import OfflineError

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

    return wrapper


def register_house_style_tools(mcp) -> None:
    from ..placement import service as svc

    @mcp.tool()
    @_offline
    def ref_profile(project: str | None = None, pcb: str | None = None,
                    name: str | None = None, save: bool = True) -> dict[str, Any]:
        """Measure a reference board's house style from its files (no Altium).

        Spacing between parts (bodies and copper), edge clearance, which
        parts go on the bottom, rotations, decoupling-cap distances, track
        widths and via sizes in use, clearance rules and silkscreen
        heights. Also derives the placer levers that reproduce those habits,
        for place_plan(profile=...).

        Args:
            project: The reference .PrjPcb (or the active project).
            pcb: A .PcbDoc to measure instead of the project's first.
            name: Name to save the profile under (default: the board name).
            save: Save it as JSON in the house-style profile folder.

        Returns:
            ``profile`` (with ``levers``) and ``saved_to``.
        """
        return svc.ref_profile(project, pcb, name, save)

    @mcp.tool()
    @_offline
    def ref_compare(profile: Any, project: str | None = None, pcb: str | None = None,
                    strictness: str = "range") -> dict[str, Any]:
        """Where a board departs from a house-style profile (no Altium).

        Flags parts closer together or nearer the edge than the reference,
        decoupling caps farther from their IC, rotations the reference never
        uses, track widths and via sizes it does not use, a different layer
        count, and connectors away from the edge. Style, not rules: DRC and
        the board's own rules stay the authority.

        Args:
            profile: A profile from ref_profile: its saved name, a JSON
                path, or the profile itself.
            project: The board to compare (.PrjPcb, or the active project).
            pcb: A .PcbDoc instead of the project's first.
            strictness: "range" (outside anything the reference does; a
                board matches itself) or "typical" (outside its p10..p90).
        """
        return svc.ref_compare(profile, project, pcb, strictness)

    @mcp.tool()
    @_offline
    def place_plan(project: str | None = None, pcb: str | None = None,
                   levers: dict[str, Any] | None = None, profile: Any = None,
                   antennas: dict[str, Any] | None = None, keep_outline: bool = True,
                   name: str | None = None) -> dict[str, Any]:
        """Place a board's parts offline and save the plan (no Altium).

        The Brass placer: sheets kept together, decoupling caps pulled to
        their IC's supply pin, connectors and mounting holes to the edge,
        both sides when allowed, then net length minimised. Reads the
        PcbDoc (parts must be on the board: Import Changes first). Nothing
        on the board changes; place_apply applies a plan.

        An RF module with a printed antenna needs its keep-out from the
        datasheet; the first call returns ``needs_answers`` with the
        questions instead of guessing.

        Args:
            project: The .PrjPcb (or the active project).
            pcb: A .PcbDoc instead of the project's first.
            levers: Placer settings, lengths in mm: spacing_mm,
                edge_gap_mm, escape, bottom ("none"/"passives"/"all"),
                body ("full"/"copper"), density, effort (0..1), seed,
                decap_weight, fixed [{ref, x_mm, y_mm, rotation, side}],
                keep [refs], edges [{ref, edge}], shape.
            profile: A house-style profile whose levers are the defaults;
                ``levers`` override them.
            antennas: {ref: "top 6 5" | "none"} answers to the questions.
            keep_outline: Place inside the board's existing outline (a
                re-layout); false sizes a new board from ``shape``.
            name: File name for the plan (default: board and time).

        Returns:
            ``plan_path``, ``score``, ``levers``, ``notes``, ``check``.
        """
        return svc.place_plan(project, pcb, levers, profile, antennas, keep_outline, name)

    @mcp.tool()
    @_offline
    def place_check(plan: str, pcb: str | None = None) -> dict[str, Any]:
        """Check a placement plan against its board (no Altium).

        Overlaps on the same side, parts outside the outline or nearer the
        edge than the plan's edge gap, decoupling caps far from their IC,
        parts inside an antenna keep-out, connectors away from the edge,
        parts the placer could not fit. Says when the board file changed
        after the plan was made. A placement check, not DRC.

        Args:
            plan: The plan JSON path from place_plan.
            pcb: Check against this .PcbDoc instead of the plan's source.
        """
        return svc.place_check(plan, pcb)

    @mcp.tool()
    async def place_apply(plan: str, confirm: bool = False, accept_findings: bool = False,
                          checkpoint: bool = True) -> dict[str, Any]:
        """Move the focused board's parts to a checked placement plan (live).

        Refuses a sample or installed design (only your own copies are
        edited), a plan whose check found errors (unless
        ``accept_findings``), a focused board that is not the plan's board,
        and a board whose origin moved since the plan. Without ``confirm``
        it returns what it would do. Positions are sent as absolute mils
        (board origin + plan mm). Save in Altium afterwards; undo with
        Altium's Undo or the checkpoint.

        Args:
            plan: The plan JSON path from place_plan.
            confirm: Must be True to move anything.
            accept_findings: Apply although the plan's check found errors.
            checkpoint: Snapshot the project folder first.
        """
        from pathlib import Path

        from ..bridge import get_bridge
        from ..layout.read_altium import _same_file
        from ..offline.errors import OfflineError

        try:
            p = await asyncio.to_thread(svc.load_plan, plan)
            why = svc.refuse_reason(p, accept_findings)
            if why:
                return {"ok": False, "refused": why}
            ops = svc.apply_ops(p)
        except OfflineError as exc:
            return {"ok": False, **exc.to_dict()}
        preview = {"parts": len(ops), "first": ops[:5], "source": p["source"],
                   "origin_mil": p["origin_mil"]}
        if not confirm:
            return {"ok": True, "applied": False, "preview": preview,
                    "hint": "Pass confirm=true to move the parts on the focused board."}
        bridge = get_bridge()
        head = await bridge.send_command_async("pcb.get_layout_model", {"section": "board"}, timeout=120.0)
        focused = str((head or {}).get("file", ""))
        if not _same_file(focused, p["source"]):
            return {"ok": False, "refused": f"the focused board is {focused or '(none)'}, not {p['source']}; "
                                           "nothing was moved"}
        org = (head or {}).get("origin")
        if isinstance(org, (list, tuple)) and len(org) == 2:
            live = (float(org[0]) / 10000.0, float(org[1]) / 10000.0)
        elif isinstance(org, dict):
            live = (float(org.get("x", 0)) / 10000.0, float(org.get("y", 0)) / 10000.0)
        else:
            live = None
        if live is None or abs(live[0] - p["origin_mil"][0]) > 1 or abs(live[1] - p["origin_mil"][1]) > 1:
            return {"ok": False, "refused": f"the board origin is {live} mil, the plan was made at "
                                           f"{p['origin_mil']}; save the board and plan again"}
        out: dict[str, Any] = {"preview": preview}
        if checkpoint:
            from ..checkpoint import CheckpointStore
            from ..config import get_config
            cp = CheckpointStore(get_config().workspace_dir / "checkpoints")
            out["checkpoint"] = cp.create(Path(p["source"]).parent, label="before place_apply").summary()
        res = await bridge.send_command_async("pcb.import_placement", {"placements": "|".join(ops)},
                                              timeout=300.0)
        out.update(res if isinstance(res, dict) else {"reply": res})
        out["applied"] = True
        out["next"] = "Check the board in Altium, save it, then route (and run DRC)."
        return out
