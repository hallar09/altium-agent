# SPDX-License-Identifier: Apache-2.0
"""House style, measured from a reference board.

A designer's style is in the board, not in a rules file: how close parts
sit, how far from the edge, which side the passives go, how far a
decoupling cap may be from its pin, which track widths and via sizes are
used, how big the designators are. ``profile_board`` measures those from a
.PcbDoc with the offline reader, so it needs no Altium, and
``levers_from_profile`` turns the measurements into placer levers, so a new
or re-laid-out board is placed the way the reference was.

Everything is in millimetres. Distributions are reported as percentiles
(p10, p25, median, p90) because a style is a habit, not a single number:
the comparison (reference/compare.py) flags what falls outside the habit,
not what differs from an average.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import numpy as np

from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..placement.footprints import Footprint, board_box, footprints_of, pin_on_board
from ..placement.placer import is_connector, is_ground, is_passive, is_supply_net
from ..placement.shape import distance_to_edge, inside

PROFILE_VERSION = 1


def _pct(values: list[float]) -> dict | None:
    if not values:
        return None
    a = np.array(values, dtype=float)
    return {"n": int(a.size), "min": round(float(a.min()), 3), "p10": round(float(np.percentile(a, 10)), 3),
            "p25": round(float(np.percentile(a, 25)), 3), "median": round(float(np.median(a)), 3),
            "p90": round(float(np.percentile(a, 90)), 3), "max": round(float(a.max()), 3)}


def _is_ic(f: Footprint) -> bool:
    return len(f.pins) >= 8 and not is_connector(f)


def body_gaps(fps: list[Footprint], copper: bool = False) -> list[tuple[str, str, float]]:
    """For each part, the nearest other body on a side it shares: (ref, other, gap_mm).

    Through-hole parts clash with both sides. A negative gap is an overlap.
    """
    if len(fps) < 2:
        return []
    boxes = np.array([[b.min_x, b.min_y, b.max_x, b.max_y] for b in (board_box(f, copper) for f in fps)])
    side = np.array([1 if f.at.side == "bottom" else 0 for f in fps])
    th = np.array([f.through_hole for f in fps])
    out = []
    for i, f in enumerate(fps):
        dx = np.maximum(boxes[:, 0] - boxes[i, 2], boxes[i, 0] - boxes[:, 2])
        dy = np.maximum(boxes[:, 1] - boxes[i, 3], boxes[i, 1] - boxes[:, 3])
        gap = np.where((dx > 0) & (dy > 0), np.hypot(dx, dy), np.maximum(dx, dy))
        same = (side == side[i]) | th | th[i]
        same[i] = False
        if not same.any():
            continue
        j = int(np.argmin(np.where(same, gap, np.inf)))
        out.append((f.ref, fps[j].ref, float(gap[j])))
    return out


def edge_gaps(fps: list[Footprint], poly: list[tuple[float, float]]) -> list[tuple[str, float]]:
    """Each part body's distance to the board edge (negative: past it)."""
    out = []
    for f in fps:
        b = board_box(f)
        corners = [(b.min_x, b.min_y), (b.max_x, b.min_y), (b.max_x, b.max_y), (b.min_x, b.max_y)]
        d = min(distance_to_edge(poly, x, y) * (1 if inside(poly, x, y) else -1) for x, y in corners)
        out.append((f.ref, d))
    return out


def decoupling(fps: list[Footprint]) -> list[dict]:
    """Each decoupling cap (2 pins: a supply and ground) and the nearest IC pin on its supply."""
    ic_pins: dict[str, list[tuple[str, float, float, str]]] = {}
    for f in fps:
        if not _is_ic(f):
            continue
        for p in f.pins:
            if p.net and not is_ground(p.net):
                x, y = pin_on_board(f, p)
                ic_pins.setdefault(p.net, []).append((f.ref, x, y, f.at.side))
    out = []
    for f in fps:
        if not re.match(r"^C\d", f.ref, re.IGNORECASE) or len(f.pins) != 2:
            continue
        a, b = f.pins
        if is_ground(a.net or "") and b.net and not is_ground(b.net):
            sp = b
        elif is_ground(b.net or "") and a.net and not is_ground(a.net):
            sp = a
        else:
            continue
        cands = ic_pins.get(sp.net)
        if not cands:
            continue
        x, y = pin_on_board(f, sp)
        ref, px, py, side = min(cands, key=lambda c: math.hypot(c[1] - x, c[2] - y))
        out.append({"ref": f.ref, "net": sp.net, "ic": ref, "distance_mm": round(math.hypot(px - x, py - y), 3),
                    "same_side": side == f.at.side})
    return out


def _widths(doc: PcbDocument) -> dict:
    sig, sup = Counter(), Counter()
    for t in doc.tracks:
        if t.component != NONE16 or t.net == NONE16 or t.polygon != NONE16 or t.keepout:
            continue
        name = doc.nets[t.net] if t.net < len(doc.nets) else ""
        w = round(t.width, 3)
        (sup if is_supply_net(name, 0) else sig)[w] += 1
    fmt = lambda c: [{"width_mm": w, "segments": n} for w, n in c.most_common(8)]
    return {"signal": fmt(sig), "supply": fmt(sup),
            "signal_mode_mm": sig.most_common(1)[0][0] if sig else None,
            "supply_mode_mm": sup.most_common(1)[0][0] if sup else None,
            "min_mm": min(list(sig) + list(sup)) if (sig or sup) else None}


def _vias(doc: PcbDocument) -> dict:
    free = [v for v in doc.vias if v.component == NONE16]
    sizes = Counter((round(v.diameter, 3), round(v.hole, 3)) for v in free)
    known = [v for v in free if v.tent_top is not None]
    tent = {"top_share": round(sum(bool(v.tent_top) for v in known) / len(known), 3) if known else None,
            "bottom_share": round(sum(bool(v.tent_bottom) for v in known) / len(known), 3) if known else None}
    return {"count": sum(sizes.values()),
            "sizes": [{"diameter_mm": d, "hole_mm": h, "count": n} for (d, h), n in sizes.most_common(6)],
            "tenting": tent}


def _rules(doc: PcbDocument) -> dict:
    out: dict[str, Any] = {}
    clear = [r["values"]["gap_mm"] for r in doc.rules
             if r.get("kind") == "Clearance" and r.get("enabled") and "gap_mm" in (r.get("values") or {})]
    if clear:
        out["clearance_min_mm"] = round(min(clear), 3)
        general = [r["values"]["gap_mm"] for r in doc.rules if r.get("kind") == "Clearance"
                   and r.get("scope1") == "All" and r.get("scope2") == "All" and "gap_mm" in (r.get("values") or {})]
        if general:
            out["clearance_general_mm"] = round(min(general), 3)
    for kind, key in (("Width", "preferedwidth_mm"), ("RoutingVias", "preferedwidth_mm")):
        vals = [r["values"].get(key) for r in doc.rules if r.get("kind") == kind and r.get("values")]
        vals = [v for v in vals if v]
        if vals:
            out[f"{kind.lower()}_preferred_mm"] = sorted({round(v, 3) for v in vals})
    out["rule_count"] = len(doc.rules)
    return out


def _silk(doc: PcbDocument) -> dict:
    heights = [t.height for t in doc.texts if t.is_designator]
    return {"designator_height": _pct(heights)}


def profile_board(doc: PcbDocument, sheet_of: dict[str, str] | None = None, *, name: str = "") -> dict:
    """The house style of one board, measured from its file."""
    fps = footprints_of(doc, sheet_of)
    poly = doc.outline_poly if hasattr(doc, "outline_poly") else []
    bbox = doc.outline_bbox() if hasattr(doc, "outline_bbox") else None
    area = doc.outline_area_mm2() if hasattr(doc, "outline_area_mm2") else None
    copper = [l.name for l in doc.copper_layers]
    gaps = body_gaps(fps)
    copper_gaps = body_gaps(fps, copper=True)
    edges = edge_gaps(fps, poly) if len(poly) >= 3 else []
    decaps = decoupling(fps)
    bottom = [f for f in fps if f.at.side == "bottom"]
    passives = [f for f in fps if is_passive(f)]
    ics = [f for f in fps if _is_ic(f)]
    conns = [f for f in fps if is_connector(f)]
    rot = Counter(round(f.at.rotation) % 360 for f in fps)
    ortho = sum(n for r, n in rot.items() if r % 90 == 0)
    edge_by = dict(edges)
    body = sum(board_box(f).w * board_box(f).h for f in fps)
    sides_used = 2 if bottom else 1
    return {
        "profile_version": PROFILE_VERSION,
        "name": name or doc.path.stem,
        "source": str(doc.path),
        "board": {
            "width_mm": bbox["width_mm"] if bbox else None, "height_mm": bbox["height_mm"] if bbox else None,
            "area_mm2": round(area, 2) if area else None, "copper_layers": len(copper), "layer_names": copper,
            "thickness_mm": round(doc.board_thickness_mm, 3) if doc.board_thickness_mm else None,
        },
        "placement": {
            "parts": len(fps), "bottom_parts": len(bottom),
            "bottom_share": {
                "all": round(len(bottom) / len(fps), 3) if fps else 0,
                "passives": round(sum(f.at.side == "bottom" for f in passives) / len(passives), 3) if passives else 0,
                "ics": round(sum(f.at.side == "bottom" for f in ics) / len(ics), 3) if ics else 0,
            },
            "rotations": dict(sorted((str(k), v) for k, v in rot.items())),
            "orthogonal_share": round(ortho / len(fps), 3) if fps else 1.0,
            "body_gap_mm": _pct([g for _, _, g in gaps]),
            "copper_gap_mm": _pct([g for _, _, g in copper_gaps]),
            "edge_gap_mm": _pct([d for _, d in edges]),
            "connectors_at_edge_share": (round(sum(edge_by.get(f.ref, 99) <= 1.0 for f in conns) / len(conns), 3)
                                         if conns and edges else None),
            "density": round(body / (area * sides_used), 3) if area else None,
        },
        "decoupling": {
            "caps": len(decaps),
            "distance_mm": _pct([d["distance_mm"] for d in decaps]),
            "same_side_share": round(sum(d["same_side"] for d in decaps) / len(decaps), 3) if decaps else None,
        },
        "routing": {"track_widths": _widths(doc), "vias": _vias(doc),
                    "diff_pairs": len(doc.diff_pairs or [])},
        "rules": _rules(doc),
        "silkscreen": _silk(doc),
        "note": ("Measured from the file with the offline reader. Bodies are pads, silkscreen and "
                 "mechanical primitives of each footprint; a courtyard on a mechanical layer counts."),
    }


def levers_from_profile(profile: dict) -> dict:
    """Placer levers that reproduce the reference's habits."""
    pl = profile.get("placement") or {}
    gap = (pl.get("body_gap_mm") or {})
    edge = (pl.get("edge_gap_mm") or {})
    share = pl.get("bottom_share") or {}
    lev: dict[str, Any] = {}
    copper = (pl.get("copper_gap_mm") or {})
    # a reference whose silkscreen and courtyards habitually overlap is spaced by its copper
    use = gap
    if gap.get("p25") is not None and gap["p25"] < 0.05 and copper.get("p25") is not None:
        lev["body"] = "copper"
        use = copper
    if use.get("p25") is not None:
        lev["spacing_mm"] = round(min(5.0, max(0.1, use["p25"])), 3)
        # a dense reference packs many-pin parts tight too: no extra escape room beyond its own gaps
        lev["escape"] = 0.0 if use["p25"] < 0.3 else 0.5 if use["p25"] < 0.6 else 1.0
    if edge.get("p10") is not None:
        lev["edge_gap_mm"] = round(min(10.0, max(0.0, edge["p10"])), 3)
    if share.get("ics", 0) >= 0.1:
        lev["bottom"] = "all"
    elif share.get("passives", 0) >= 0.2:
        lev["bottom"] = "passives"
    else:
        lev["bottom"] = "none"
    layers = (profile.get("board") or {}).get("copper_layers")
    if layers:
        lev["layers"] = layers
    if pl.get("density"):
        lev["density"] = round(min(0.7, max(0.08, pl["density"])), 3)
    dec = (profile.get("decoupling") or {}).get("distance_mm") or {}
    if dec.get("median") is not None:
        # a reference with caps right at the pins pulls them harder
        lev["decap_weight"] = 3.0 if dec["median"] <= 1.5 else 1.5 if dec["median"] <= 4 else 1.0
    return lev
