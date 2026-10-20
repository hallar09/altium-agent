# SPDX-License-Identifier: Apache-2.0
"""Compare a board with a house-style profile (Altium Agent).

``ref_profile`` measures a reference board; this module measures another
board the same way and says, finding by finding, where it departs from the
reference's habits. Findings are style, not errors: the board's own rules
and DRC stay the authority, and a departure can be deliberate.
"""

from __future__ import annotations

from typing import Any

from ..offline.files.pcbdoc import PcbDocument
from ..placement.footprints import footprints_of
from ..placement.placer import is_connector
from .profile import body_gaps, decoupling, edge_gaps, profile_board

#: Track widths and via sizes match the reference within this, in mm.
SIZE_TOLERANCE_MM = 0.005
MAX_LISTED = 25


def _sizes(values: Any) -> list[float]:
    """The sizes a profile lists, whatever shape the section has."""
    if isinstance(values, dict):
        vals = values.get("sizes") or values.get("widths") or values.get("all") or []
        if isinstance(vals, dict):
            vals = list(vals.keys())
    else:
        vals = values or []
    out = []
    for v in vals:
        if isinstance(v, dict):
            v = v.get("diameter_mm") or v.get("width_mm") or v.get("size_mm")
        try:
            out.append(round(float(v), 4))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _off(values: list[float], allowed: list[float]) -> list[float]:
    if not allowed:
        return []
    return [v for v in values if min(abs(v - a) for a in allowed) > SIZE_TOLERANCE_MM]


def _finding(code: str, severity: str, message: str, items: list | None = None, **extra) -> dict:
    f: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if items is not None:
        f["count"] = len(items)
        f["items"] = items[:MAX_LISTED]
        if len(items) > MAX_LISTED:
            f["truncated"] = True
    f.update(extra)
    return f


STRICTNESS = ("range", "typical")


def compare_board(reference: dict, doc: PcbDocument, sheet_of: dict[str, str] | None = None,
                  strictness: str = "range") -> dict:
    """Findings where ``doc`` departs from the ``reference`` profile.

    ``strictness``: "range" flags only what lies outside everything the
    reference does (its min / max; a board compared with itself matches);
    "typical" flags what lies outside its usual habits (p10 / p90).
    """
    if strictness not in STRICTNESS:
        raise ValueError(f"strictness must be one of {STRICTNESS}")
    lo, hi = ("min", "max") if strictness == "range" else ("p10", "p90")
    mine = profile_board(doc, sheet_of)
    fps = footprints_of(doc, sheet_of)
    rp = reference.get("placement") or {}
    findings: list[dict] = []

    # spacing: the same measure the reference's levers came from
    copper = (rp.get("body_gap_mm") or {}).get("p25", 1) < 0.05
    key = "copper_gap_mm" if copper else "body_gap_mm"
    ref_gap = (rp.get(key) or {}).get(lo)
    if ref_gap is not None:
        tight = [{"ref": a, "other": b, "gap_mm": round(g, 3)}
                 for a, b, g in body_gaps(fps, copper=copper) if g < ref_gap - 0.01]
        if tight:
            findings.append(_finding(
                "closer_than_reference", "warn",
                f"{len(tight)} part(s) sit closer to a neighbour than the reference's {lo} "
                f"({'copper' if copper else 'body'} gap < {ref_gap:.3f} mm)",
                sorted(tight, key=lambda t: t["gap_mm"]), measure=key))

    # decoupling distance
    ref_dec = (reference.get("decoupling") or {}).get("distance_mm") or {}
    if ref_dec.get(hi) is not None:
        far = [d for d in decoupling(fps) if d["distance_mm"] > ref_dec[hi] + 0.01]
        if far:
            findings.append(_finding(
                "decap_farther_than_reference", "warn",
                f"{len(far)} decoupling cap(s) farther from their IC's supply pin than the "
                f"reference's {hi} ({ref_dec[hi]:.2f} mm)",
                sorted(far, key=lambda d: -d["distance_mm"])))

    # rotations
    if (rp.get("orthogonal_share") or 0) >= 0.99:
        skew = [{"ref": f.ref, "rotation": round(f.at.rotation, 2)} for f in fps
                if abs(((f.at.rotation % 90) + 90) % 90) > 0.01 and abs((f.at.rotation % 90) - 90) > 0.01]
        if skew:
            findings.append(_finding("non_orthogonal_rotation", "info",
                                     f"{len(skew)} part(s) at an angle the reference never uses", skew))

    # track widths and vias
    rr, mr = reference.get("routing") or {}, mine.get("routing") or {}
    for label, sect in (("signal", "signal"), ("supply", "supply")):
        allowed = _sizes((rr.get("track_widths") or {}).get(sect))
        used = _sizes((mr.get("track_widths") or {}).get(sect))
        off = _off(used, allowed)
        if off:
            findings.append(_finding("track_width_outside_reference", "info",
                                     f"{label} track widths not used on the reference: {off} mm",
                                     reference_mm=allowed))
    allowed_v = _sizes((rr.get("vias") or {}).get("sizes"))
    used_v = _sizes((mr.get("vias") or {}).get("sizes"))
    off_v = _off(used_v, allowed_v)
    if off_v:
        findings.append(_finding("via_outside_reference", "info",
                                 f"via sizes not used on the reference: {off_v} mm", reference_mm=allowed_v))

    # layer count
    rl = (reference.get("board") or {}).get("copper_layers")
    ml = (mine.get("board") or {}).get("copper_layers")
    if rl and ml and rl != ml:
        findings.append(_finding("layer_count_differs", "info",
                                 f"{ml} copper layers here, {rl} on the reference"))

    # connectors at the edge
    if (rp.get("connectors_at_edge_share") or 0) >= (1.0 if strictness == "range" else 0.5) and len(doc.outline_poly or []) >= 3:
        edge = dict(edge_gaps(fps, doc.outline_poly))
        inland = [{"ref": f.ref, "edge_gap_mm": round(edge[f.ref], 3)} for f in fps
                  if is_connector(f) and f.ref in edge and edge[f.ref] > 1.0]
        if inland:
            findings.append(_finding(
                "connector_away_from_edge", "warn",
                f"{len(inland)} connector(s) more than 1 mm from the edge; the reference puts "
                f"{rp['connectors_at_edge_share']:.0%} of its connectors at the edge", inland))

    # edge clearance
    ref_edge = (rp.get("edge_gap_mm") or {}).get(lo)
    if ref_edge is not None and len(doc.outline_poly or []) >= 3:
        near = [{"ref": r, "edge_gap_mm": round(d, 3)} for r, d in edge_gaps(fps, doc.outline_poly)
                if d < ref_edge - 0.01 and not any(f.ref == r and is_connector(f) for f in fps)]
        if near:
            findings.append(_finding("closer_to_edge_than_reference", "info",
                                     f"{len(near)} part(s) nearer the edge than the reference's "
                                     f"{lo} ({ref_edge:.2f} mm)", near))

    sev = {"warn": 0, "info": 0}
    for f in findings:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    return {
        "reference": reference.get("name"),
        "board": mine.get("name"),
        "strictness": strictness,
        "matches": not findings,
        "counts": sev,
        "findings": findings,
        "profile": mine,
        "note": "House style, not rules: a departure can be deliberate. DRC and the board's rules decide.",
    }
