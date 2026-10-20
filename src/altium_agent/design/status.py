# SPDX-License-Identifier: Apache-2.0
"""design_status: the whole state of a design in one call (Altium Agent).

Stage and open question from the session journal; ERC, unrouted connections,
DRC, DFM and the house-style comparison from the saved files; the next action
from the journal's state machine. Nothing here talks to Altium, so it is
fast and safe to call often; it says how old each number is.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict
from pathlib import Path
from typing import Any


def _age(path: Path) -> str:
    return _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.UTC).isoformat(timespec="seconds")


def status(journal, project: str | None = None, *, profile: Any = None, dfm_profile: str | None = None,
           pcb_index: int = 0) -> dict[str, Any]:
    from ..analysis.copper import ratsnest
    from ..analysis.dfm import run_dfm
    from ..analysis.erc import run_erc
    from ..analysis.model import load_model
    from ..offline.context import get_settings
    from .gate_review import latest_drc_report
    from .stage_checkpoints import project_for, session_profile, stage_checkpoints

    out: dict[str, Any] = {}
    issues: list[str] = []
    if journal is not None:
        st = journal.state()
        out["session"] = {"id": journal.session_id, "current_stage": st.current_stage, "next_stage": st.next_stage,
                          "stage_status": st.stage_status, "open_question": st.open_question,
                          "complete": st.complete, "checkpoints": stage_checkpoints(journal)}
        if st.open_question:
            issues.append(f"open question: {st.open_question}")
        try:
            from .autonomy import measured_next_action

            na = measured_next_action(st, journal.events())
            na = asdict(na) if hasattr(na, "__dataclass_fields__") else dict(na)
            out["next_action"] = {k: na.get(k) for k in ("status", "stage", "goal", "suggested_tools", "guidance")
                                  if na.get(k) is not None}
        except Exception as exc:  # noqa: BLE001 - status must still answer
            out["next_action"] = {"error": f"state machine: {exc}"}
        profile = profile or session_profile(journal)
    proj = project_for(journal, project)
    model = load_model(proj)
    out["project"] = str(proj)

    e = run_erc(model)
    out["erc"] = {k: e["summary"].get(k) for k in ("errors", "warnings", "by_check")}
    if e["summary"].get("errors"):
        issues.append(f"ERC: {e['summary']['errors']} error(s)")

    doc = model.pcb(pcb_index) if model.project.pcb_docs else None
    if doc is None:
        out["board"] = None
    else:
        rn = ratsnest(doc)
        out["board"] = {"file": str(doc.path), "saved": _age(doc.path), "components": len(doc.components),
                        "unrouted_connections": rn["unrouted_connections"],
                        "open_nets": [o["net"] for o in rn["open_nets"]][:20],
                        "routing_completion_pct": rn["routing_completion_pct"]}
        if doc.components and rn["unrouted_connections"]:
            issues.append(f"{rn['unrouted_connections']} unrouted connection(s)")
        rep = latest_drc_report(proj.parent, doc.path)
        if rep is None:
            out["drc"] = {"report": None, "note": "no DRC report found; run pcb_run_drc"}
        else:
            from ..analysis.drc_report import summarize_file

            s = summarize_file(rep)
            stale = rep.stat().st_mtime < doc.path.stat().st_mtime
            out["drc"] = {"report": str(rep), "written": _age(rep), "stale": stale,
                          **{k: s.get(k) for k in ("violations", "copper_violations", "noise_violations",
                                                   "complete", "by_rule")}}
            if s.get("copper_violations"):
                issues.append(f"DRC: {s['copper_violations']} copper violation(s)"
                              + (" (report older than the board)" if stale else ""))
        prof_name = dfm_profile or get_settings().dfm_profile
        try:
            d = run_dfm(doc, prof_name, max_examples=3)
            out["dfm"] = {"profile": prof_name, **{k: d.get(k) for k in ("passed", "pass", "errors", "warnings",
                                                                           "summary", "verdict") if k in d}}
        except Exception as exc:  # noqa: BLE001
            out["dfm"] = {"profile": prof_name, "error": str(exc)}
        if out["dfm"].get("passed") is False:
            n = (out["dfm"].get("summary") or {}).get("errors")
            issues.append(f"DFM ({prof_name}): {n if n is not None else 'some'} error(s)")
        if profile is not None:
            from ..placement.service import load_profile
            from ..reference.compare import compare_board

            c = compare_board(load_profile(profile), doc)
            out["style"] = {"profile": c["reference"], "matches": c["matches"],
                            "findings": [f"{f['code']}: {f['message']}" for f in c["findings"]]}
            if not c["matches"]:
                issues.append(f"{len(c['findings'])} departure(s) from the {c['reference']} style")
    out["open_issues"] = issues
    out["note"] = ("From the saved files and the journal; Altium's unsaved edits are not counted. "
                   "Never a statement that the design is ready to manufacture.")
    return out
