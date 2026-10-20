# SPDX-License-Identifier: Apache-2.0
"""The routing broker (Altium Agent): which nets, in which order, by which engine.

A board is routed the way a designer routes it, not in one autorouter run:

1. **Differential pairs** first, coupled, by the in-house engine
   (``pcb_autoroute`` with ``diff_pairs``); Freerouting routes the two
   halves as unrelated nets.
2. **Critical nets**: clocks and crystal nets, and short local nets, by the
   in-house engine, which keeps bends to 45 degrees and IC pins straight out.
3. **The bulk** (power not carried by a pour, then signals) by Freerouting,
   run as a separate process when it is installed and allowed; otherwise by
   the in-house engine. Pours are left to the pours.
4. Repour, DRC, then ``route_converge`` until the clashes are gone or only
   ones that need a person remain.

Altium's own routers are offered, never driven: Situs (Route > Auto Route)
has no scripting interface that does not stop at its dialog, and the
interactive router is interactive. The plan names when they are the better
tool (a few nets left over, length tuning) and what to do in Altium.

Copper already on the board is protected: a fully routed net is left alone,
and a partly routed one keeps its copper (Freerouting gets it as fixed
wiring; nothing here unroutes it). A net is only ever ripped up by
``route_converge`` when the broker routed it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..analysis.copper import ratsnest
from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..placement.placer import is_ground, is_supply_net

PLAN_VERSION = 1
CLOCK_RE = re.compile(r"XTAL|OSC|CLK|CLOCK|MCO|\bXI\b|\bXO\b|XIN|XOUT", re.IGNORECASE)
CRYSTAL_REF = re.compile(r"^(Y|X|XTAL|OSC)\d", re.IGNORECASE)
PAIR_SUFFIX = re.compile(r"^(.*?)(_?)(P|N|\+|-|DP|DM)$", re.IGNORECASE)
#: A net whose pins all lie within this (half-perimeter of their box) is local.
LOCAL_MM = 3.0

ENGINES = ("inhouse", "freerouting", "altium")


def _net_pins(doc: PcbDocument) -> dict[str, list[tuple[float, float, str]]]:
    refs = {c.index: c.designator for c in doc.components}
    out: dict[str, list] = defaultdict(list)
    for p in doc.pads:
        if p.net == NONE16 or p.net >= len(doc.nets):
            continue
        out[doc.nets[p.net]].append((p.x, p.y, refs.get(p.component, "")))
    return out


def _hpwl(pts) -> float:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def pair_nets(doc: PcbDocument) -> list[tuple[str, str, str]]:
    """Differential pairs: (name, positive, negative). Defined pairs, else by name."""
    out, used = [], set()
    for p in doc.diff_pairs or []:
        pos, neg = p.get("positive"), p.get("negative")
        if pos and neg:
            out.append((p.get("name") or pos, pos, neg))
            used.update((pos, neg))
    names = set(doc.nets)
    by_base: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for n in names - used:
        m = PAIR_SUFFIX.match(n)
        if not m or not m.group(1):
            continue
        s = m.group(3).upper()
        pol = "p" if s in ("P", "+", "DP") else "n"
        family = "pm" if s in ("+", "-") else "dpdm" if s in ("DP", "DM") else "pn"
        by_base[(m.group(1) + m.group(2), family)][pol] = n
    for (base, _), d in sorted(by_base.items()):
        if "p" in d and "n" in d and not is_supply_net(d["p"], 0):
            out.append((base.rstrip("_") or base, d["p"], d["n"]))
    return out


def poured_nets(doc: PcbDocument) -> set[str]:
    """Nets that own a polygon pour (connected by the pour and vias, not tracks)."""
    out = set()
    for poly in doc.polygons or []:
        net = poly.get("net") if isinstance(poly, dict) else getattr(poly, "net", None)
        if isinstance(net, int):
            net = doc.nets[net] if 0 <= net < len(doc.nets) else None
        if net:
            out.add(net)
    return out


def classify(doc: PcbDocument) -> dict[str, Any]:
    """Every net with 2+ pads, by tier, and the routing already on the board."""
    pins = _net_pins(doc)
    rn = ratsnest(doc)
    open_by: dict[str, int] = {}
    for item in rn.get("open_nets") or []:
        if isinstance(item, dict) and item.get("net"):
            open_by[item["net"]] = int(item.get("unrouted_connections", item.get("open", 0)) or 0)
    routed_copper: dict[str, int] = defaultdict(int)
    for t in doc.tracks:
        if t.component == NONE16 and t.net != NONE16 and t.polygon == NONE16 and t.net < len(doc.nets):
            routed_copper[doc.nets[t.net]] += 1
    pairs = pair_nets(doc)
    pair_set = {n for _, p, q in pairs for n in (p, q)}
    poured = poured_nets(doc)
    crystal_refs = {c.designator for c in doc.components
                    if CRYSTAL_REF.match(c.designator) or re.search(r"crystal|xtal|oscillator", c.comment or "", re.IGNORECASE)}
    tiers: dict[str, list[str]] = {"pair": [], "critical": [], "power": [], "signal": [], "poured": []}
    done, partial = [], []
    for net, pts in sorted(pins.items()):
        if len(pts) < 2:
            continue
        if not open_by.get(net, 0):
            # already connected: by tracks (protected) or by a pour or fill alone
            if routed_copper.get(net):
                done.append(net)
            continue
        if routed_copper.get(net):
            partial.append(net)
        if net in pair_set:
            tiers["pair"].append(net)
        elif net in poured or is_ground(net):
            tiers["poured" if net in poured else "power"].append(net)
        elif is_supply_net(net, len(pts)):
            tiers["power"].append(net)
        elif CLOCK_RE.search(net) or any(r in crystal_refs for _, _, r in pts) or _hpwl(pts) <= LOCAL_MM:
            tiers["critical"].append(net)
        else:
            tiers["signal"].append(net)
    return {"tiers": tiers, "pairs": pairs, "protected": {"routed": done, "partial": partial},
            "unrouted_connections": rn.get("unrouted_connections"),
            "hpwl_mm": {n: round(_hpwl(p), 2) for n, p in pins.items() if len(p) >= 2}}


def _batches(nets: list[str], size: int) -> list[list[str]]:
    if not nets:
        return []
    if size <= 0:
        return [nets]
    return [nets[i:i + size] for i in range(0, len(nets), size)]


def plan(doc: PcbDocument, *, engines: list[str] | None = None, freerouting_ready: bool = False,
         batch_size: int = 0, profile: dict | None = None, max_passes: int = 50) -> dict:
    """The broker's plan: steps, each an exact tool call with its reason."""
    engines = [e for e in (engines or list(ENGINES)) if e in ENGINES]
    use_fr = "freerouting" in engines and freerouting_ready
    c = classify(doc)
    t = c["tiers"]
    src = str(doc.path)
    hp = c["hpwl_mm"]
    steps: list[dict] = []
    notes: list[str] = []

    def step(tool: str, args: dict, why: str, **extra) -> None:
        steps.append({"n": len(steps) + 1, "tool": tool, "args": args, "why": why, **extra})

    to_route = t["pair"] + t["critical"] + t["power"] + t["signal"]
    if not to_route:
        return {"plan_version": PLAN_VERSION, "source": src, "origin_mil": list(doc.origin_mil),
                "engines": engines, "freerouting": use_fr, "tiers": t, "pairs": [], "protected": c["protected"],
                "unrouted_connections": c["unrouted_connections"], "routed_by_plan": [], "steps": [],
                "notes": ["Nothing to route: every connection is made (by tracks, pours or fills)."],
                "counts": {k: len(v) for k, v in t.items()}}
    step("app_checkpoint", {"label": "before routing", "save_first": True},
         "a restore point before any copper is written")
    protected = c["protected"]["routed"] + c["protected"]["partial"]
    if protected:
        step("pcb_lock_net_routing", {"nets": protected, "lock": True},
             f"{len(protected)} net(s) already carry routing: lock it so no engine moves it",
             engine="altium")
    if t["pair"]:
        step("pcb_autoroute", {"expect_file": src, "nets": t["pair"], "diff_pairs": True},
             f"{len(c['pairs'])} differential pair(s) first, coupled", engine="inhouse", tier="pair",
             then="poll design_job_status, then pcb_autoroute_apply with the job id")
    crit = sorted(t["critical"], key=lambda n: hp.get(n, 0))
    if crit:
        step("pcb_autoroute", {"expect_file": src, "nets": crit, "diff_pairs": False},
             f"{len(crit)} clock, crystal and short local net(s) before the bulk, so they get the "
             "direct paths", engine="inhouse", tier="critical",
             then="poll design_job_status, then pcb_autoroute_apply with the job id")
    bulk = sorted(t["power"], key=lambda n: -hp.get(n, 0)) + sorted(t["signal"], key=lambda n: hp.get(n, 0))
    if bulk:
        if use_fr:
            for i, b in enumerate(_batches(bulk, batch_size), 1):
                step("route_freerouting", {"expect_file": src, "route_only": b, "max_passes": max_passes},
                     f"bulk batch {i}: {len(b)} net(s) by Freerouting (an external process), with every "
                     "other net's copper fixed", engine="freerouting", tier="bulk",
                     then="poll route_freerouting_status, then route_import_ses with the job id")
        else:
            step("pcb_autoroute", {"expect_file": src, "nets": bulk, "diff_pairs": False},
                 f"{len(bulk)} power and signal net(s) by the in-house engine"
                 + ("" if "freerouting" not in engines else " (Freerouting is not installed or not usable)"),
                 engine="inhouse", tier="bulk",
                 then="poll design_job_status, then pcb_autoroute_apply with the job id")
    if t["poured"]:
        notes.append(f"{len(t['poured'])} net(s) are carried by pours and left to them: "
                     + ", ".join(t["poured"][:12]) + ("..." if len(t["poured"]) > 12 else ""))
    step("pcb_repour_polygons", {}, "pours are rebuilt round the new copper and join their nets")
    step("pcb_run_drc", {"answer_dialogs": True}, "Altium's own rules judge the result")
    step("route_converge", {"plan": "<this plan's path>", "drc": "<the pcb_run_drc result or report>"},
         "rip up and re-route only what this plan routed; clashes on protected copper go to you")
    if profile:
        step("route_via_style", {"profile": profile.get("name"), "pcb": src},
             "via sizes and tenting compared with the house style, read back from the saved file")
        step("ref_compare", {"profile": profile.get("name"), "pcb": src},
             "the routed board compared with the reference")
    if "altium" in engines:
        notes.append("Situs (Route > Auto Route > Net/Connection) and the interactive router are not "
                     "driven by the broker (Situs stops at its dialog; the interactive router is "
                     "interactive). Use them for the last few connections or for length tuning, then "
                     "save; route_converge reads what they did from the saved file.")
    if protected:
        notes.append("Locked routing is unlocked again only by you (pcb_lock_net_routing lock=false).")
    return {"plan_version": PLAN_VERSION, "source": src, "origin_mil": list(doc.origin_mil),
            "engines": engines, "freerouting": use_fr, "tiers": {k: v for k, v in t.items()},
            "pairs": [{"name": n, "positive": p, "negative": q} for n, p, q in c["pairs"]],
            "protected": c["protected"], "unrouted_connections": c["unrouted_connections"],
            "routed_by_plan": sorted(set(t["pair"]) | set(t["critical"]) | set(bulk)),
            "steps": steps, "notes": notes,
            "counts": {k: len(v) for k, v in t.items()} | {"protected": len(protected)}}

