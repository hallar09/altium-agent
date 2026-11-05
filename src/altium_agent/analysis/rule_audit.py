# SPDX-License-Identifier: Apache-2.0
# Altium Agent (2026): see docs/PROVENANCE.md
"""Audit a board's design rules against what the design needs.

Every blank Altium board arrives with a set of rules scoped to ``All``:
Width 10 mil, RoutingVias 50/28 mil, DiffPairsRouting 10 mil, a 10 mil
solder-mask sliver, a 4 mil mask expansion and the rest. They are the
FALLBACK for every object no other rule covers, so a design that adds
its own class rules and leaves these alone gets:

* every track thinner than 10 mil outside a class flagged by DRC,
* 50 mil vias dropped by the router on any net no via rule covers,
* pair rules whose width was never set (the router uses the factory width),
* hundreds of mask-sliver violations that bury the copper problems under
  the batch DRC's 500 cap.

Agents kept missing this (SODIMM, MiniPC, MacroStick, 2026-10/11): the
class rules were right and the defaults beat them on everything else.
This reads the rules (offline record format, ``PcbDocument.rules``) and
returns each problem with the call that fixes it. Pure and testable.
"""

from __future__ import annotations

from typing import Any, Optional

from ..units import MM_PER_MIL as MIL

#: Altium's blank-board rule names, by kind. A rule with one of these names
#: and an ``All`` scope is a factory default.
DEFAULT_NAMES = {
    "Width": "Width", "Clearance": "Clearance", "RoutingVias": "RoutingVias",
    "DiffPairsRouting": "DiffPairsRouting", "SolderMaskExpansion": "SolderMaskExpansion",
    "PasteMaskExpansion": "PasteMaskExpansion", "MinimumSolderMaskSliver": "MinimumSolderMaskSliver",
    "HoleSize": "HoleSize", "HoleToHoleClearance": "HoleToHoleClearance",
    "PlaneClearance": "PlaneClearance", "PlaneConnect": "PlaneConnect",
    "PolygonConnect": "PolygonConnect", "SilkToSolderMaskClearance": "SilkToSolderMaskClearance",
    "SilkToSilkClearance": "SilkToSilkClearance", "ComponentClearance": "ComponentClearance",
    "MinimumAnnularRing": "MinimumAnnularRing", "LayerPairs": "LayerPairs",
}

#: Rules that are routinely switched off while routing and must be back on
#: before sign-off.
SIGNOFF_RULES = ("UnRoutedNet", "SilkToSilkClearance", "SilkToSolderMaskClearance",
                 "MinimumSolderMaskSliver", "NetAntennae", "ShortCircuit", "Clearance", "Width",
                 "HoleToHoleClearance")

_TOL = 0.0005  # mm; Altium stores mils to 4 places


def _is_all(scope: str) -> bool:
    return (scope or "All").strip().lower() in ("all", "")


def _is_default(rule: dict) -> bool:
    return (rule.get("name") == DEFAULT_NAMES.get(rule.get("kind", ""))
            and _is_all(rule.get("scope1", "")) and _is_all(rule.get("scope2", "")))


def _v(rule: dict, key: str) -> Optional[float]:
    return (rule.get("values") or {}).get(key)


def _mm(v: Optional[float]) -> str:
    return "?" if v is None else f"{v:.4g} mm ({v / MIL:.3g} mil)"


def _finding(sev: str, rule: dict, issue: str, fix: Optional[dict] = None,
             alternative: Optional[dict] = None) -> dict[str, Any]:
    out: dict[str, Any] = {"severity": sev, "rule": rule.get("name", ""),
                           "kind": rule.get("kind", ""), "issue": issue}
    if fix:
        out["fix"] = fix
    if alternative:
        out["alternative"] = alternative
    return out


def _call(tool: str, **arguments) -> dict:
    return {"tool": tool, "arguments": arguments}


def audit_rules(rules: list[dict[str, Any]], *,
                min_width_mm: Optional[float] = None,
                default_width_mm: Optional[float] = None,
                min_clearance_mm: Optional[float] = None,
                via_diameter_mm: Optional[float] = None,
                via_hole_mm: Optional[float] = None,
                mask_sliver_mm: Optional[float] = None,
                mask_expansion_mm: Optional[float] = None,
                diff_pair_width_mm: Optional[float] = None,
                diff_pair_gap_mm: Optional[float] = None,
                has_blind_vias: bool = False,
                signoff: bool = False) -> dict[str, Any]:
    """Findings for one board's rules. Every limit is optional; a check
    whose limit is not given reports what the rule holds instead."""
    findings: list[dict[str, Any]] = []
    enabled = [r for r in rules if r.get("enabled", True)]
    by_kind: dict[str, list[dict]] = {}
    for r in enabled:
        by_kind.setdefault(r.get("kind", ""), []).append(r)

    # 1. The factory defaults, one by one.
    for r in enabled:
        if not _is_default(r):
            continue
        kind, name = r["kind"], r["name"]
        if kind == "Width":
            lo, pref, hi = _v(r, "minlimit_mm"), _v(r, "preferedwidth_mm"), _v(r, "maxlimit_mm")
            want_min = min_width_mm
            want_pref = default_width_mm or min_width_mm
            if want_min is not None and lo is not None and lo > want_min + _TOL:
                findings.append(_finding(
                    "fail", r,
                    f"the fallback Width rule's minimum is {_mm(lo)}, above the design's "
                    f"{_mm(want_min)}: every narrower track outside a class rule is a DRC "
                    "violation, and interactive routing starts unclassed nets at "
                    f"{_mm(pref)}.",
                    _call("pcb_set_rule_properties", name=name, min_width_mm=want_min,
                          favored_width_mm=want_pref, max_width_mm=max(hi or 0, want_pref or 0) or None)))
            elif want_min is None:
                findings.append(_finding(
                    "info", r, f"fallback Width rule: min {_mm(lo)}, preferred {_mm(pref)}, "
                               f"max {_mm(hi)}. It governs every net no class Width rule covers."))
        elif kind == "RoutingVias":
            dia, hole = _v(r, "width_mm"), _v(r, "holewidth_mm")
            if via_diameter_mm is not None and dia is not None and abs(dia - via_diameter_mm) > _TOL \
                    or via_hole_mm is not None and hole is not None and abs(hole - via_hole_mm) > _TOL:
                d_mil = (via_diameter_mm or dia or 0) / MIL
                h_mil = (via_hole_mm or hole or 0) / MIL
                findings.append(_finding(
                    "fail", r,
                    f"the fallback Routing Via rule is {_mm(dia)} / {_mm(hole)} hole. Routers and "
                    "interactive routing drop that via on every net no other via rule covers "
                    f"(the design uses {_mm(via_diameter_mm)} / {_mm(via_hole_mm)}).",
                    _call("pcb_set_rule_properties", name=name,
                          min_via_size_mils=round(d_mil, 3), preferred_via_size_mils=round(d_mil, 3),
                          max_via_size_mils=round(max(d_mil, (dia or 0) / MIL), 3),
                          min_via_hole_mils=round(h_mil, 3), preferred_via_hole_mils=round(h_mil, 3),
                          max_via_hole_mils=round(max(h_mil, (hole or 0) / MIL), 3)),
                    _call("pcb_create_design_rule", name="Vias_Board", rule_type="routing_via",
                          scope="All", values_mm={"via_min": via_diameter_mm, "via_pref": via_diameter_mm,
                                                  "via_max": via_diameter_mm, "hole_min": via_hole_mm,
                                                  "hole_pref": via_hole_mm, "hole_max": via_hole_mm})))
            elif via_diameter_mm is None:
                findings.append(_finding(
                    "warn" if (dia or 0) >= 1.0 else "info", r,
                    f"fallback Routing Via rule: {_mm(dia)} / {_mm(hole)} hole"
                    + (" - Altium's 50 mil blank-board via, too big for fine-pitch escape. "
                       "Pass via_diameter_mm/via_hole_mm to get the fix." if (dia or 0) >= 1.0 else ".")))
        elif kind == "MinimumSolderMaskSliver":
            v = _v(r, "minsoldermaskwidth_mm")
            if mask_sliver_mm is not None and v is not None and v > mask_sliver_mm + _TOL:
                findings.append(_finding(
                    "fail", r,
                    f"the sliver rule is {_mm(v)}, above the fab's {_mm(mask_sliver_mm)} dam. On a "
                    "fine-pitch board it fills the batch DRC's 500 slots and hides copper "
                    "problems. Disabling it does not reliably stick on AD26: set the value.",
                    _call("pcb_set_rule_properties", name=name, gap_mm=mask_sliver_mm)))
            elif mask_sliver_mm is None and v is not None and v >= 0.2:
                findings.append(_finding(
                    "warn", r, f"sliver rule {_mm(v)} is Altium's blank-board value; most fabs hold "
                               "0.1 mm or less. Pass mask_sliver_mm with the fab's dam width."))
        elif kind == "SolderMaskExpansion":
            v = _v(r, "expansion_mm")
            if mask_expansion_mm is not None and v is not None and abs(v - mask_expansion_mm) > _TOL:
                findings.append(_finding(
                    "warn", r, f"mask expansion {_mm(v)}; the design wants {_mm(mask_expansion_mm)}.",
                    _call("pcb_delete_design_rule", name=name),
                    _call("pcb_create_design_rule", name="SolderMask_Board",
                          rule_type="solder_mask_expansion", scope="All",
                          values_mm={"expansion": mask_expansion_mm})))
        elif kind == "Clearance":
            v = _v(r, "gap_mm")
            if min_clearance_mm is not None and v is not None and abs(v - min_clearance_mm) > _TOL:
                sev = "fail" if v < min_clearance_mm - _TOL else "warn"
                findings.append(_finding(
                    sev, r,
                    f"fallback Clearance is {_mm(v)}; the design's board-wide clearance is "
                    f"{_mm(min_clearance_mm)}."
                    + (" Copper closer than the fab allows would pass DRC." if sev == "fail" else
                       " Every pair of objects no other clearance rule covers is held to the larger gap."),
                    _call("pcb_set_rule_properties", name=name, gap_mm=min_clearance_mm)))
        elif kind == "HoleSize":
            lo = _v(r, "minlimit_mm")
            if via_hole_mm is not None and lo is not None and lo > via_hole_mm + _TOL:
                findings.append(_finding(
                    "fail", r, f"HoleSize minimum {_mm(lo)} is above the via hole {_mm(via_hole_mm)}: "
                               "every via is a violation.",
                    _call("pcb_set_rule_properties", name=name,
                          min_hole_size_mils=max(1, int(via_hole_mm / MIL)))))
        elif kind == "DiffPairsRouting":
            pass  # with the pair rules below
        elif kind == "LayerPairs" and has_blind_vias:
            findings.append(_finding(
                "info", r, "Layer Pairs is enforced: every blind/buried via needs its drill pair "
                           "(pcb_get_drill_pairs, pcb_add_drill_pair) or DRC flags it."))

    # 2. Pair rules: the width was never written by the old create call.
    for r in by_kind.get("DiffPairsRouting", []):
        w_min, w_pref, w_max = _v(r, "dp_min_width_mm"), _v(r, "dp_pref_width_mm"), _v(r, "dp_max_width_mm")
        g_min, g_max = _v(r, "minlimit_mm"), _v(r, "maxlimit_mm")
        label = "the fallback pair rule" if _is_default(r) else f"pair rule {r['name']}"
        factory = (w_min is not None and w_min == w_pref == w_max
                   and any(abs(w_min - x * MIL) < _TOL for x in (10, 15)))
        if diff_pair_width_mm is not None and w_pref is not None and abs(w_pref - diff_pair_width_mm) > _TOL:
            findings.append(_finding(
                "fail", r,
                f"{label} routes pairs at {_mm(w_pref)} (min {_mm(w_min)}, max {_mm(w_max)}); the "
                f"design's pair width is {_mm(diff_pair_width_mm)}. The pair router uses this width, "
                "not the Width rule.",
                _call("pcb_set_rule_properties", name=r["name"], dp_min_width_mm=diff_pair_width_mm,
                      dp_pref_width_mm=diff_pair_width_mm, dp_max_width_mm=diff_pair_width_mm)))
        elif diff_pair_width_mm is None and factory:
            findings.append(_finding(
                "warn", r,
                f"{label} still has a factory width of {_mm(w_min)} on every layer (pair rules made "
                "before 2026-11 never had a width written). Pass diff_pair_width_mm to get the fix."))
        if diff_pair_gap_mm is not None and g_min is not None and g_max is not None \
                and not (g_min - _TOL <= diff_pair_gap_mm <= g_max + _TOL):
            findings.append(_finding(
                "fail", r, f"{label} allows a gap of {_mm(g_min)}..{_mm(g_max)}; the design's "
                           f"gap is {_mm(diff_pair_gap_mm)}.",
                _call("pcb_set_rule_properties", name=r["name"], dp_min_gap_mm=diff_pair_gap_mm,
                      dp_pref_gap_mm=diff_pair_gap_mm, dp_max_gap_mm=max(g_max, diff_pair_gap_mm))))
        # A pair wider than every Width rule's maximum is a DRC failure on
        # every pair track (the SODIMM board: 15 mil pairs, 8 mil max).
        scoped_max = [(_v(w, "maxlimit_mm"), w["name"]) for w in by_kind.get("Width", [])
                      if not _is_default(w) and _v(w, "maxlimit_mm") is not None]
        if diff_pair_width_mm is None and w_min is not None and scoped_max                 and all(w_min > mx + _TOL for mx, _n in scoped_max):
            narrowest = min(scoped_max)
            findings.append(_finding(
                "fail", r, f"{label}'s minimum width {_mm(w_min)} is wider than every class Width "
                           f"rule's maximum (e.g. {narrowest[1]}: {_mm(narrowest[0])}): every pair "
                           "track the pair router lays fails the Width check.",
                _call("pcb_set_rule_properties", name=r["name"], dp_min_width_mm=narrowest[0],
                      dp_pref_width_mm=narrowest[0], dp_max_width_mm=narrowest[0])))

    # 3. Rules that can never apply: same kind and scope, lower priority.
    seen: dict[tuple, dict] = {}
    for r in sorted(enabled, key=lambda r: (r.get("kind", ""), r.get("priority") or 0)):
        key = (r.get("kind", ""), (r.get("scope1") or "All").strip().lower(),
               (r.get("scope2") or "All").strip().lower())
        if key in seen:
            winner = seen[key]
            findings.append(_finding(
                "warn", r, f"same scope as {winner['name']} (priority {winner.get('priority')}), so "
                           f"this rule (priority {r.get('priority')}) never applies. Delete one.",
                _call("pcb_delete_design_rule", name=r["name"])))
        else:
            seen[key] = r

    # 4. Switched off and needed back for sign-off.
    disabled = [r for r in rules if not r.get("enabled", True)]
    for r in disabled:
        if r.get("kind") in SIGNOFF_RULES or r.get("name") in SIGNOFF_RULES:
            findings.append(_finding(
                "fail" if signoff else "info", r,
                "disabled. Fine while routing; it must be back on before sign-off. AD26 does not "
                "always keep a scripted enable: if it does not read back, delete the rule and "
                "recreate it.",
                _call("pcb_set_rules_enabled", names=[r["name"]], enabled=True)))

    order = {"fail": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["kind"], f["rule"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in order}
    defaults = sorted(r["name"] for r in enabled if _is_default(r))
    return {
        "ok": counts["fail"] == 0,
        "counts": counts,
        "findings": findings,
        "defaults_present": defaults,
        "rule_count": len(rules),
        "note": ("Altium uses the highest-priority rule whose scope matches. The blank-board rules "
                 "scoped to All are the fallback for everything else: change their values to the "
                 "design's, or delete them once a board-wide rule of your own covers All. Rules "
                 "that cannot be made by script (matched length, max via count, routing "
                 "topology): see tool_guide('matched length')."),
    }
