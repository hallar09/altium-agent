# SPDX-License-Identifier: Apache-2.0
"""Tools added by Altium Agent on top of the eda-agent core.

Registered last by ``tools._register_full`` so the forked core stays
importable on its own. Each phase adds its tools here or in a module this
one imports.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional


def register_agent_tools(mcp) -> None:
    """Register the Altium Agent additions."""

    @mcp.tool()
    async def app_diagnose() -> dict[str, Any]:
        """Why is Altium not answering? One call, the real cause.

        Checks, without changing anything: Altium running at all (Altium
        Agent never starts it), a second X2.EXE, the bridge script not
        started or halted, a modal dialog (with its title and buttons),
        "a command is currently active" left by a script that died
        between PreProcess and PostProcess, a dialog off every monitor or
        on another one, and the Windows text-input overlay covering a
        dialog.

        Returns:
            ``ok`` (nothing blocking), ``findings`` (each with ``code``,
            ``severity`` block/warn/info, ``message``, ``fix`` = what the
            user must do, ``auto`` = the safe recovery app_recover can
            apply, ``evidence``), ``auto_recoveries`` and ``summary``.
        """
        from ..diag.self_heal import LiveEnvironment, diagnose

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: diagnose(LiveEnvironment()))

    @mcp.tool()
    async def app_recover(
        actions: Optional[list[str]] = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Apply the SAFE recoveries app_diagnose offered, then re-check.

        Only three exist, and each is applied only when the diagnosis
        calls for it: ``dismiss_information_dialog`` (press the only
        button of an information box), ``end_active_command`` (OK the
        "command is currently active" box, then unwind the PCB/schematic
        PreProcess levels a dead handler left open) and
        ``bring_dialog_on_screen`` (move a dialog that is off every
        monitor onto the first one). Nothing here starts or kills Altium,
        answers a question, or presses a button that commits a change.

        Args:
            actions: Recoveries to apply; omit to apply every one the
                diagnosis offers.
            confirm: Must be True; without it the plan is returned and
                nothing is touched.
        """
        from ..diag.self_heal import LiveEnvironment, diagnose, recover

        env = LiveEnvironment()
        loop = asyncio.get_running_loop()
        before = await loop.run_in_executor(None, lambda: diagnose(env))
        wanted = list(actions) if actions else list(before["auto_recoveries"])
        if not confirm:
            return {"ok": False, "dry_run": True, "would_apply": wanted,
                    "diagnosis": before,
                    "reason": "pass confirm=True to apply these recoveries"}
        if not wanted:
            return {"ok": before["ok"], "applied": [],
                    "reason": "the diagnosis offers no automatic recovery",
                    "diagnosis": before}
        return await loop.run_in_executor(
            None, lambda: recover(env, wanted, before))

    @mcp.tool()
    async def analyze_drc_report(
        report_path: str,
        list_noise: bool = False,
    ) -> dict[str, Any]:
        """Summarize an Altium DRC HTML report: what matters, and whether
        it is complete.

        Counts per rule, the copper problems in full (clearance, short,
        unrouted, width, annular ring, antennae, ...) with locations in
        mm, dead/no-net copper, and silk/mask noise counted but not
        listed. Altium's batch DRC stops at 500 violations, so a report
        at the limit is flagged INCOMPLETE (``complete: false``) with the
        rules to hide while routing (``hide_while_routing``). Reads a
        file only; never runs DRC.

        Args:
            report_path: The .html report Altium wrote (Design Rule
                Verification Report).
            list_noise: Also list the silk/mask findings.
        """
        from ..analysis.drc_report import summarize_file

        return summarize_file(report_path, list_noise)

