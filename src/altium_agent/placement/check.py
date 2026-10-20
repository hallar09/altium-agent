# SPDX-License-Identifier: Apache-2.0
"""Check a placement plan before it touches a board (Altium Agent).

Moves each footprint, offline, to where a plan puts it, then checks what a
placement can get wrong by itself: parts overlapping on the same side,
parts outside the outline or nearer its edge than allowed, decoupling caps
far from their IC, parts inside an antenna keep-out, and connectors away
from the edge. Routing, DRC and the board's rules are later steps.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..reference.profile import body_gaps, decoupling
from .footprints import At, Box, Footprint, board_box
from .placer import is_connector
from .shape import distance_to_edge, inside

MAX_LISTED = 25


def moved(fps: list[Footprint], placements: list[dict]) -> tuple[list[Footprint], list[str]]:
    """The footprints at the plan's positions, and plan refs not on the board."""
    by = {p["ref"].upper(): p for p in placements}
    out, seen = [], set()
    for f in fps:
        p = by.get(f.ref.upper())
        if p is None:
            out.append(f)
            continue
        seen.add(f.ref.upper())
        out.append(replace(f, at=At(float(p["x_mm"]), float(p["y_mm"]),
                                    float(p.get("rotation", 0)) % 360, p.get("side", "top"))))
    missing = [p["ref"] for p in placements if p["ref"].upper() not in seen]
    return out, missing


def _overlap(a: Box, b: dict) -> bool:
    return (min(a.max_x, b["max_x_mm"]) - max(a.min_x, b["min_x_mm"]) > 1e-6
            and min(a.max_y, b["max_y_mm"]) - max(a.min_y, b["min_y_mm"]) > 1e-6)


def _listed(items: list) -> dict:
    out: dict[str, Any] = {"count": len(items), "items": items[:MAX_LISTED]}
    if len(items) > MAX_LISTED:
        out["truncated"] = True
    return out


def _key(code: str, item: dict) -> tuple:
    if code == "overlap":
        return (code,) + tuple(sorted((item["ref"], item["other"])))
    return (code, item.get("ref"))


def check_plan(fps: list[Footprint], plan: dict, *, poly: list | None = None,
               decap_max_mm: float | None = None, baseline: bool = True) -> dict:
    """Findings for ``plan`` (as place_board returns it) on these footprints.

    A finding the board already has where the plan leaves the parts
    involved where they were (locked connectors on a re-layout, say) is
    reported separately as ``inherited`` info: the plan did not cause it.

    ``poly`` is the board outline in the plan's frame (mm from the board
    origin); without it, the plan's own outline is not re-derived and the
    outline checks are skipped. ``decap_max_mm`` flags decoupling caps
    farther than that from their IC's supply pin (default: 3 mm, or the
    profile's p90 when the plan came from one).
    """
    levers = plan.get("levers") or {}
    copper = levers.get("body") == "copper"
    edge_gap = float(levers.get("edge_gap_mm", 0) or 0)
    placed, missing = moved(fps, plan.get("placements") or [])
    findings: list[dict] = []

    clashes = [{"ref": a, "other": b, "overlap_mm": round(-g, 3)}
               for a, b, g in body_gaps(placed, copper=copper) if g < -1e-3]
    # body_gaps lists each part's nearest neighbour; keep each pair once
    seen, pairs = set(), []
    for c in clashes:
        k = tuple(sorted((c["ref"], c["other"])))
        if k not in seen:
            seen.add(k)
            pairs.append(c)
    if pairs:
        findings.append({"code": "overlap", "severity": "error",
                         "message": f"{len(pairs)} pair(s) of parts overlap on the same side "
                                    f"({'copper' if copper else 'bodies'})", **_listed(pairs)})

    if poly and len(poly) >= 3:
        out_of, near = [], []
        for f in placed:
            b = board_box(f, copper)
            corners = [(b.min_x, b.min_y), (b.max_x, b.min_y), (b.max_x, b.max_y), (b.min_x, b.max_y)]
            if not all(inside(poly, x, y) for x, y in corners):
                out_of.append({"ref": f.ref})
                continue
            d = min(distance_to_edge(poly, x, y) for x, y in corners)
            if d < edge_gap - 0.01 and not is_connector(f):
                near.append({"ref": f.ref, "edge_gap_mm": round(d, 3)})
        if out_of:
            findings.append({"code": "outside_outline", "severity": "error",
                             "message": f"{len(out_of)} part(s) not wholly inside the board outline",
                             **_listed(out_of)})
        if near:
            findings.append({"code": "near_edge", "severity": "warn",
                             "message": f"{len(near)} part(s) nearer the edge than {edge_gap} mm",
                             **_listed(near)})
        inland = []
        for f in placed:
            if is_connector(f):
                b = board_box(f)
                d = min(distance_to_edge(poly, x, y) for x, y in
                        ((b.min_x, b.min_y), (b.max_x, b.min_y), (b.max_x, b.max_y), (b.min_x, b.max_y)))
                if d > 1.0:
                    inland.append({"ref": f.ref, "edge_gap_mm": round(d, 3)})
        if inland:
            findings.append({"code": "connector_away_from_edge", "severity": "warn",
                             "message": f"{len(inland)} connector(s) more than 1 mm from the edge",
                             **_listed(inland)})

    limit = decap_max_mm if decap_max_mm is not None else 3.0
    far = sorted((d for d in decoupling(placed) if d["distance_mm"] > limit), key=lambda d: -d["distance_mm"])
    if far:
        findings.append({"code": "decap_far", "severity": "warn",
                         "message": f"{len(far)} decoupling cap(s) more than {limit} mm from their IC's "
                                    "supply pin", **_listed(far)})

    intrude = []
    for k in plan.get("keepouts") or []:
        for f in placed:
            if f.ref.upper() == str(k.get("ref", "")).upper():
                continue
            if _overlap(board_box(f, copper), k):
                intrude.append({"ref": f.ref, "keepout_of": k.get("ref")})
    if intrude:
        findings.append({"code": "in_antenna_keepout", "severity": "error",
                         "message": f"{len(intrude)} part(s) inside an antenna keep-out", **_listed(intrude)})

    unplaced = (plan.get("score") or {}).get("unplaced") or []
    if unplaced:
        findings.append({"code": "unplaced", "severity": "error",
                         "message": f"the placer could not fit {len(unplaced)} part(s) without overlap",
                         "count": len(unplaced), "items": [{"ref": r} for r in unplaced]})
    if missing:
        findings.append({"code": "not_on_board", "severity": "error",
                         "message": f"{len(missing)} plan part(s) are not on this board",
                         "count": len(missing), "items": [{"ref": r} for r in missing]})

    if baseline:
        findings = _split_inherited(fps, plan, findings, poly, decap_max_mm)
    counts = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"ok": counts["error"] == 0, "counts": counts, "findings": findings,
            "parts": len(placed),
            "note": "A placement check, not DRC: routing, rules and clearances are checked later."}


def _split_inherited(fps: list[Footprint], plan: dict, findings: list[dict], poly, decap_max_mm) -> list[dict]:
    """Move findings the unmoved board already has into ``inherited`` info."""
    here = {f.ref.upper(): f.at for f in fps}

    def unmoved(ref: str) -> bool:
        p = next((q for q in plan.get("placements") or [] if q["ref"].upper() == ref.upper()), None)
        a = here.get(ref.upper())
        return p is None or (a is not None and abs(float(p["x_mm"]) - a.x) < 1e-3
                             and abs(float(p["y_mm"]) - a.y) < 1e-3
                             and abs((float(p.get("rotation", 0)) - a.rotation) % 360) < 1e-3
                             and p.get("side", "top") == a.side)

    same = {**plan, "keepouts": [], "score": {},
            "placements": [{"ref": f.ref, "x_mm": f.at.x, "y_mm": f.at.y, "rotation": f.at.rotation,
                            "side": f.at.side} for f in fps]}
    before = check_plan(fps, same, poly=poly, decap_max_mm=decap_max_mm, baseline=False)
    known = {_key(f["code"], i) for f in before["findings"] for i in f.get("items") or []}
    out, inherited = [], []
    for f in findings:
        items = f.get("items")
        if not items or f["code"] in ("unplaced", "not_on_board"):
            out.append(f)
            continue
        old = [i for i in items if _key(f["code"], i) in known
               and all(unmoved(r) for r in (i.get("ref"), i.get("other")) if r)]
        new = [i for i in items if i not in old]
        if new:
            g = {**f, **_listed(new)}
            if old:
                g["message"] = f"{f['message']}; {len(old)} of them as on the board already (see inherited)"
            out.append(g)
        for i in old:
            inherited.append({"code": f["code"], **i})
    if inherited:
        out.append({"code": "inherited", "severity": "info",
                    "message": f"{len(inherited)} finding(s) the board already has, on parts the plan "
                               "leaves where they are (locked or kept)", **_listed(inherited)})
    return out
