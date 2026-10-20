# SPDX-License-Identifier: Apache-2.0
"""Converge clashes after routing (Altium Agent).

One round: DRC violations in (the ``pcb_run_drc`` payload), the next steps
out. The repair policy is the inherited ``plan_drc_repairs`` (worst
offender first); the broker adds what it knows and the planner does not:

* only nets the broker's plan routed are ripped up, and only their unlocked
  copper (``pcb_unroute`` with ``include_locked=false``), so hand routing,
  locked by the plan, is never removed;
* an action on a net the plan did not route becomes an escalation: a clash
  with protected copper is for a person to settle;
* a re-route goes to the other engine than last time (in-house <-> Freerouting),
  and a net ripped in two rounds already is escalated, not ripped again;
* the rounds are bounded (``max_rounds``, default 3); the state is kept in
  a JSON beside the plan so a later call continues the count.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .repair import plan_drc_repairs

MAX_RIPS_PER_NET = 2


def _state_path(plan_path: Path) -> Path:
    return plan_path.with_name(plan_path.stem + ".converge.json")


def load_state(plan_path: Path | None) -> dict:
    if plan_path is None:
        return {"round": 0, "ripped": {}, "engine": {}}
    p = _state_path(plan_path)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"round": 0, "ripped": {}, "engine": {}}


def save_state(plan_path: Path | None, state: dict) -> None:
    if plan_path is not None:
        _state_path(plan_path).write_text(json.dumps(state, indent=1), encoding="utf-8")


def _engine_of(plan: dict, net: str) -> str:
    for s in plan.get("steps") or []:
        nets = s.get("args", {}).get("nets") or s.get("args", {}).get("route_only") or []
        if net in nets and s.get("engine") in ("inhouse", "freerouting"):
            return s["engine"]
    return "inhouse"


def converge(plan: dict, drc: Any, *, plan_path: Path | None = None, max_rounds: int = 3,
             rips_per_round: int = 5) -> dict:
    """The next round of repairs, as steps; ``done`` when nothing is left to do here."""
    state = load_state(plan_path)
    routed = set(plan.get("routed_by_plan") or [])
    prot = plan.get("protected") or {}
    full = set(prot.get("routed") or [])
    src = plan.get("source", "")
    use_fr = bool(plan.get("freerouting"))

    # The planner rips the worst offender; a protected net must never be it. Its side of each
    # violation is blanked, so the plan's own net is the one ripped, and a clash with no plan net
    # on either side is counted for a person.
    raw = drc.get("violations") if isinstance(drc, dict) else drc
    if not isinstance(raw, list):
        return {"ok": False, "reason": "drc must be the pcb_run_drc payload or its violations list",
                "round": state["round"]}
    mine = routed - full
    masked, foreign = [], 0
    for v in raw:
        if not isinstance(v, dict):
            continue
        v = dict(v)
        nets = []
        for key in ("primitive1", "primitive2"):
            prim = v.get(key)
            if isinstance(prim, dict) and prim.get("net"):
                nets.append(prim["net"])
                if prim["net"] not in mine:
                    v[key] = {**prim, "net": ""}
        if nets and not any(n in mine for n in nets):
            foreign += 1
            continue
        masked.append(v)
    rep = plan_drc_repairs({"violations": masked}, max_rounds=rips_per_round)
    if not rep.get("ok"):
        return {"ok": False, "reason": rep.get("reason"), "round": state["round"]}
    actions = rep.get("actions") or []
    if not actions and foreign:
        return {"ok": True, "done": True, "converged": False, "round": state["round"], "steps": [],
                "counts": rep.get("counts"),
                "escalations": [(f"{foreign} violation(s) involve only copper this plan did not route; "
                                 "yours to settle")]}
    if not actions:
        return {"ok": True, "done": True, "converged": True, "round": state["round"], "steps": [],
                "counts": rep.get("counts"), "note": "No violation this broker can act on is left."}
    if state["round"] >= max_rounds:
        return {"ok": True, "done": True, "converged": False, "round": state["round"], "steps": [],
                "counts": rep.get("counts"), "remaining_actions": actions,
                "escalations": [f"{max_rounds} round(s) used and violations remain; over to you"]}

    rip: list[str] = []
    other: list[dict] = []
    escalations: list[str] = []
    if foreign:
        escalations.append(f"{foreign} violation(s) involve only copper this plan did not route; yours to settle")
    for a in actions:
        net = a.get("net")
        kind = a.get("action")
        if kind == "escalate":
            escalations.append(a.get("reason", "the repair planner escalated"))
            continue
        if net and (net in full or net not in routed):
            why = "its routing was on the board before" if net in full else "the plan did not route it"
            escalations.append(f"{kind} on {net}: {why}; protected, so it is yours to settle"
                               + (f" ({a['reason']})" if a.get("reason") else ""))
            continue
        if kind == "rip_and_reroute":
            if state["ripped"].get(net, 0) >= MAX_RIPS_PER_NET:
                escalations.append(f"{net} was ripped up and re-routed {MAX_RIPS_PER_NET} times and still clashes")
                continue
            rip.append(net)
        else:
            other.append(a)

    steps: list[dict] = []

    def step(tool: str, args: dict, why: str, **extra) -> None:
        steps.append({"n": len(steps) + 1, "tool": tool, "args": args, "why": why, **extra})

    if rip:
        step("pcb_unroute", {"expect_file": src, "nets": rip, "include_locked": False, "checkpoint": True},
             f"take up the copper this plan put on {len(rip)} clashing net(s); locked (hand) copper stays")
        by_engine: dict[str, list[str]] = {"inhouse": [], "freerouting": []}
        for net in rip:
            last = state["engine"].get(net) or _engine_of(plan, net)
            nxt = "freerouting" if last == "inhouse" and use_fr else "inhouse"
            by_engine[nxt].append(net)
            state["engine"][net] = nxt
            state["ripped"][net] = state["ripped"].get(net, 0) + 1
        if by_engine["inhouse"]:
            step("pcb_autoroute", {"expect_file": src, "nets": by_engine["inhouse"], "diff_pairs": True},
                 "re-route with the in-house engine", engine="inhouse",
                 then="poll design_job_status, then pcb_autoroute_apply")
        if by_engine["freerouting"]:
            step("route_freerouting", {"expect_file": src, "route_only": by_engine["freerouting"]},
                 "re-route with Freerouting, everything else fixed", engine="freerouting",
                 then="poll route_freerouting_status, then route_import_ses")
    hints: list[str] = []
    for a in other:
        if a["action"] in ("widen", "narrow"):
            step("pcb_set_track_width", {"net_name": a["net"], "width_mils": "<the net's Width rule, preferred>"},
                 f"{a['action']} {a['net']}: {a.get('reason', 'width violation')}")
        elif a["action"] == "nudge":
            hints.append(f"move {a['net']}'s copper near ({a.get('x_mils')}, {a.get('y_mils')}) mil by "
                         f"({a.get('dx')}, {a.get('dy')}) mil, or rip it up: {a.get('reason', 'pad clearance')}")
    if steps:
        step("pcb_repour_polygons", {}, "pours rebuilt round the changed copper")
        step("pcb_run_drc", {"answer_dialogs": True}, "check again")
        step("route_converge", {"plan": str(plan_path) if plan_path else "<plan path>",
                                "drc": "<the new pcb_run_drc result>"}, "next round")
    state["round"] += 1
    save_state(plan_path, state)
    return {"ok": True, "done": not steps, "converged": False, "round": state["round"],
            "max_rounds": max_rounds, "steps": steps, "escalations": escalations, "hints": hints,
            "counts": rep.get("counts"), "ripped_total": state["ripped"]}
