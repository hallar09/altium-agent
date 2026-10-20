# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/dfm.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Design-for-manufacture checks of a PcbDoc against a fabricator capability profile.

Profile values are typical published standard-service capabilities. Fabs change them; always
confirm against the fab's current capability page before release.
"""

from __future__ import annotations

from collections import defaultdict

from ..offline.files.pcbdoc import COPPER_LEGACY, NONE16, PcbDocument, layer_name

PROFILES: dict[str, dict] = {
    "generic_standard": {"label": "Generic standard (6/6 mil, 0.3 mm drill)", "min_track_mm": 0.152,
                         "min_space_mm": 0.152, "min_drill_mm": 0.3, "min_annular_ring_mm": 0.15,
                         "min_via_diameter_mm": 0.6, "max_layers": 8, "max_board_mm": [500, 500]},
    "generic_advanced": {"label": "Generic advanced (4/4 mil, 0.2 mm drill)", "min_track_mm": 0.1,
                         "min_space_mm": 0.1, "min_drill_mm": 0.2, "min_annular_ring_mm": 0.1,
                         "min_via_diameter_mm": 0.4, "max_layers": 16, "max_board_mm": [500, 500]},
    "jlcpcb_standard": {"label": "JLCPCB standard (typical)", "min_track_mm": 0.127, "min_space_mm": 0.127,
                        "min_drill_mm": 0.3, "min_annular_ring_mm": 0.13, "min_via_diameter_mm": 0.5,
                        "max_layers": 6, "max_board_mm": [400, 500]},
    "jlcpcb_multilayer": {"label": "JLCPCB 4-6 layer (typical)", "min_track_mm": 0.09, "min_space_mm": 0.09,
                          "min_drill_mm": 0.15, "min_annular_ring_mm": 0.075, "min_via_diameter_mm": 0.25,
                          "max_layers": 20, "max_board_mm": [400, 500]},
    "pcbway_standard": {"label": "PCBWay standard (typical)", "min_track_mm": 0.1, "min_space_mm": 0.1,
                        "min_drill_mm": 0.2, "min_annular_ring_mm": 0.15, "min_via_diameter_mm": 0.45,
                        "max_layers": 14, "max_board_mm": [500, 1100]},
    "oshpark_2layer": {"label": "OSH Park 2-layer (typical)", "min_track_mm": 0.1524, "min_space_mm": 0.1524,
                       "min_drill_mm": 0.254, "min_annular_ring_mm": 0.127, "min_via_diameter_mm": 0.508,
                       "max_layers": 2, "max_board_mm": [406, 406]},
    "eurocircuits_class6": {"label": "Eurocircuits pattern class 6 / drill class C (typical)", "min_track_mm": 0.15,
                            "min_space_mm": 0.15, "min_drill_mm": 0.35, "min_annular_ring_mm": 0.15,
                            "min_via_diameter_mm": 0.65, "max_layers": 8, "max_board_mm": [425, 570]},
}


def _merge_json_profiles() -> None:
    """Fold kicad-mcp-pro's bundled fab profiles (with pricing) into PROFILES."""
    import json
    from pathlib import Path

    data_dir = Path(__file__).resolve().parent / "engines" / "data"
    for f in sorted(data_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rules = d.get("rules")
        if not isinstance(rules, dict) or "manufacturer" not in d:
            continue
        name = f.stem
        ring = rules.get("min_annular_ring_mm", 0.15)
        drill = rules.get("min_drill_mm", 0.3)
        base = PROFILES.setdefault(name, {
            "label": f"{d['manufacturer']} {d.get('tier', '')} (typical)",
            "min_track_mm": rules.get("min_trace_width_mm", 0.127),
            "min_space_mm": rules.get("min_trace_clearance_mm", 0.127),
            "min_drill_mm": drill, "min_annular_ring_mm": ring,
            "min_via_diameter_mm": round(drill + 2 * ring, 3),
            "max_layers": rules.get("max_layers", 8), "max_board_mm": [500, 500],
        })
        base.setdefault("manufacturer", d["manufacturer"])
        base.setdefault("tier", d.get("tier", "standard"))
        for k in ("min_silkscreen_text_height_mm", "copper_to_edge_mm"):
            if k in rules:
                base.setdefault(k, rules[k])
        if "pricing" in d:
            base.setdefault("pricing", d["pricing"])


_merge_json_profiles()


def estimate_cost(pcb: PcbDocument, profile: str, quantity: int) -> dict:
    """kicad-mcp-pro's fabrication cost heuristic: setup + area x layer multiplier + drills."""
    prof = PROFILES.get(profile)
    if prof is None:
        raise ValueError(f"unknown DFM profile '{profile}'; available: {sorted(PROFILES)}")
    pricing = prof.get("pricing") or PROFILES["jlcpcb_standard"]["pricing"]
    area_cm2 = (pcb.outline_area_mm2() or 2500.0) / 100.0
    layers = len(pcb.copper_layers) or 2
    drills = len(pcb.vias) + sum(1 for p in pcb.pads if p.hole > 0)
    mult = 1.0 + max(layers - 2, 0) * float(pricing["extra_layer_multiplier"])
    setup = float(pricing["setup_usd"])
    area_cost = quantity * area_cm2 * float(pricing["per_sq_cm_usd"]) * mult
    drill_cost = quantity * drills * float(pricing["per_drill_usd"])
    bbox = pcb.outline_bbox()
    return {"profile": profile, "quantity": quantity, "board_mm": [bbox["width_mm"], bbox["height_mm"]] if bbox else None,
            "area_cm2": round(area_cm2, 2), "copper_layers": layers, "drill_hits": drills,
            "setup_usd": round(setup, 2), "area_usd": round(area_cost, 2), "drill_usd": round(drill_cost, 2),
            "total_usd": round(setup + area_cost + drill_cost, 2),
            "unit_usd": round((setup + area_cost + drill_cost) / quantity, 3),
            "note": "Rough heuristic for comparison; get a real quote from the fab."}


def _loc(x: float, y: float) -> list[float]:
    return [round(x, 3), round(y, 3)]


def run_dfm(pcb: PcbDocument, profile: str | dict = "generic_standard", *, max_examples: int = 15) -> dict:
    prof = PROFILES.get(profile) if isinstance(profile, str) else profile
    if prof is None:
        raise ValueError(f"unknown DFM profile '{profile}'; available: {sorted(PROFILES)}")
    findings: list[dict] = []

    def add(severity: str, check: str, message: str, examples: list | None = None, **extra) -> None:
        f = {"severity": severity, "check": check, "message": message}
        if examples:
            f["examples"] = examples[:max_examples]
        f.update(extra)
        findings.append(f)

    # Tracks
    thin = defaultdict(list)
    for t in pcb.tracks:
        if t.layer in COPPER_LEGACY and t.polygon == NONE16 and not t.keepout and 0 < t.width < prof["min_track_mm"] - 1e-6:
            thin[round(t.width, 4)].append(t)
    if thin:
        n = sum(len(v) for v in thin.values())
        worst = min(thin)
        ex = [{"width_mm": w, "net": pcb.net_name(t.net), "layer": layer_name(t.layer), "at_mm": _loc(t.x1, t.y1)}
              for w, ts in sorted(thin.items()) for t in ts[:3]]
        add("error", "min_track_width", f"{n} track segment(s) narrower than {prof['min_track_mm']} mm (worst {worst} mm)", ex)

    # Vias
    small_hole, thin_ring, small_dia = [], [], []
    for v in pcb.vias:
        ring = (v.diameter - v.hole) / 2.0
        item = {"net": pcb.net_name(v.net), "at_mm": _loc(v.x, v.y), "hole_mm": round(v.hole, 4),
                "diameter_mm": round(v.diameter, 4), "ring_mm": round(ring, 4)}
        if v.hole < prof["min_drill_mm"] - 1e-6:
            small_hole.append(item)
        if ring < prof["min_annular_ring_mm"] - 1e-6:
            thin_ring.append(item)
        if v.diameter < prof["min_via_diameter_mm"] - 1e-6:
            small_dia.append(item)
    if small_hole:
        add("error", "min_via_drill", f"{len(small_hole)} via(s) with hole < {prof['min_drill_mm']} mm "
            f"(smallest {min(i['hole_mm'] for i in small_hole)} mm)", small_hole)
    if thin_ring:
        add("error", "via_annular_ring", f"{len(thin_ring)} via(s) with annular ring < {prof['min_annular_ring_mm']} mm", thin_ring)
    if small_dia:
        add("warning", "min_via_diameter", f"{len(small_dia)} via(s) with diameter < {prof['min_via_diameter_mm']} mm", small_dia)

    # Pad holes
    small_pad_holes, pad_rings = [], []
    for p in pcb.pads:
        if p.hole <= 0:
            continue
        item = {"pad": f"{pcb.designator(p.component)}-{p.name}" if p.component != NONE16 else p.name,
                "at_mm": _loc(p.x, p.y), "hole_mm": round(p.hole, 4)}
        if p.hole < prof["min_drill_mm"] - 1e-6:
            small_pad_holes.append(item)
        if p.plated:
            ring = (min(p.size_x, p.size_y) - p.hole) / 2.0
            if 0 < min(p.size_x, p.size_y) and ring < prof["min_annular_ring_mm"] - 1e-6:
                pad_rings.append({**item, "ring_mm": round(ring, 4)})
    if small_pad_holes:
        add("error", "min_pad_drill", f"{len(small_pad_holes)} pad hole(s) < {prof['min_drill_mm']} mm", small_pad_holes)
    if pad_rings:
        add("warning", "pad_annular_ring", f"{len(pad_rings)} plated pad(s) with annular ring < "
            f"{prof['min_annular_ring_mm']} mm (top-layer size)", pad_rings)

    # Board
    layers = len(pcb.copper_layers)
    if layers and layers > prof["max_layers"]:
        add("error", "layer_count", f"{layers} copper layers exceeds the profile limit of {prof['max_layers']}")
    bbox = pcb.outline_bbox()
    if not bbox:
        add("error", "board_outline", "no board outline found")
    else:
        dims = sorted([bbox["width_mm"], bbox["height_mm"]])
        lim = sorted(prof["max_board_mm"])
        if dims[0] > lim[0] or dims[1] > lim[1]:
            add("error", "board_size", f"board {bbox['width_mm']} x {bbox['height_mm']} mm exceeds {lim[0]} x {lim[1]} mm")

    # Silkscreen text height
    min_silk = prof.get("min_silkscreen_text_height_mm")
    if min_silk:
        small = [{"text": t.text, "height_mm": round(t.height, 3), "at_mm": _loc(t.x, t.y)}
                 for t in pcb.texts if t.layer in (33, 34) and 0 < t.height < min_silk - 1e-6]
        if small:
            add("warning", "silkscreen_text_height", f"{len(small)} silkscreen text(s) shorter than {min_silk} mm", small)

    # Copper too close to the board edge
    edge_min = prof.get("copper_to_edge_mm")
    if edge_min and pcb.outline_mm:
        from .copper import _seg_point_dist

        outline = [(p["x"], p["y"]) for p in pcb.outline_mm]
        edges = list(zip(outline, outline[1:] + outline[:1]))

        def edge_dist(x: float, y: float) -> float:
            return min(_seg_point_dist(x, y, a[0], a[1], b[0], b[1]) for a, b in edges)

        near = []
        for t in pcb.tracks:
            if t.layer in COPPER_LEGACY and t.polygon == NONE16:
                d = min(edge_dist(t.x1, t.y1), edge_dist(t.x2, t.y2)) - t.width / 2
                if d < edge_min - 1e-6:
                    near.append({"kind": "track", "net": pcb.net_name(t.net), "at_mm": _loc(t.x1, t.y1), "distance_mm": round(d, 3)})
        for p in pcb.pads:
            if p.layer in COPPER_LEGACY or p.layer == 74:
                d = edge_dist(p.x, p.y) - max(p.size_x, p.size_y) / 2
                if d < edge_min - 1e-6:
                    near.append({"kind": "pad", "pad": f"{pcb.designator(p.component)}-{p.name}", "at_mm": _loc(p.x, p.y),
                                 "distance_mm": round(d, 3)})
        if near:
            near.sort(key=lambda n: n["distance_mm"])
            add("warning", "copper_to_edge", f"{len(near)} copper object(s) closer than {edge_min} mm to the board edge "
                "(edge connectors and castellations are expected here)", near)

    # Design rules vs fab capability
    for r in pcb.rules:
        if not r["enabled"]:
            continue
        vals = r.get("values", {})
        kind = r["kind"].lower()
        if kind == "clearance" and "gap_mm" in vals and vals["gap_mm"] < prof["min_space_mm"] - 1e-6:
            add("warning", "rule_clearance", f"rule '{r['name']}' allows {vals['gap_mm']} mm clearance "
                f"(< {prof['min_space_mm']} mm)", scope=[r["scope1"], r["scope2"]])
        if kind == "width" and "minlimit_mm" in vals and 0 < vals["minlimit_mm"] < prof["min_track_mm"] - 1e-6:
            add("warning", "rule_width", f"rule '{r['name']}' allows {vals['minlimit_mm']} mm tracks "
                f"(< {prof['min_track_mm']} mm)", scope=[r["scope1"]])
        if kind == "routingvias" and "minholewidth_mm" in vals and 0 < vals["minholewidth_mm"] < prof["min_drill_mm"] - 1e-6:
            add("warning", "rule_via_hole", f"rule '{r['name']}' allows {vals['minholewidth_mm']} mm via holes "
                f"(< {prof['min_drill_mm']} mm)", scope=[r["scope1"]])

    sev = defaultdict(int)
    for f in findings:
        sev[f["severity"]] += 1
    return {
        "profile": {"name": profile if isinstance(profile, str) else "custom", **prof},
        "passed": sev["error"] == 0,
        "summary": {"errors": sev["error"], "warnings": sev["warning"],
                    "tracks_checked": len(pcb.tracks), "vias_checked": len(pcb.vias), "pads_checked": len(pcb.pads)},
        "findings": findings,
        "note": "Geometry checks only (no copper-to-copper spacing). Altium DRC remains authoritative; "
                "confirm limits with your fabricator.",
    }
