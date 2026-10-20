# SPDX-License-Identifier: Apache-2.0
"""Via styles and tenting, read from the saved board (Altium Agent).

A via style is its diameter, hole and tenting. The house style comes from a
profile (``ref_profile``) or, without one, from the board's own routing-via
rule. Tenting is read from the via records of the .PcbDoc (the first flags
byte: 0x20 tented on top, 0x40 on the bottom), so a write made in Altium is
confirmed by what Altium saved, not by what the script believed it did.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..offline.files.pcbdoc import NONE16, PcbDocument

TOL_MM = 0.005
MAX_LISTED = 25


def via_styles(doc: PcbDocument, include_component_vias: bool = False) -> dict:
    """The via styles on the board, with counts and tenting."""
    vias = [v for v in doc.vias if include_component_vias or v.component == NONE16]
    styles = Counter((round(v.diameter, 3), round(v.hole, 3), v.tent_top, v.tent_bottom) for v in vias)
    return {
        "count": len(vias),
        "styles": [{"diameter_mm": d, "hole_mm": h, "tent_top": tt, "tent_bottom": tb, "count": n}
                   for (d, h, tt, tb), n in styles.most_common()],
        "tented_top": sum(bool(v.tent_top) for v in vias),
        "tented_bottom": sum(bool(v.tent_bottom) for v in vias),
    }


def _net(doc: PcbDocument, i: int) -> str | None:
    return doc.nets[i] if i != NONE16 and 0 <= i < len(doc.nets) else None


def house_via_style(doc: PcbDocument, profile: dict | None = None) -> dict:
    """The sizes and tenting a via should have: from the profile, else the board's rules."""
    if profile:
        v = (profile.get("routing") or {}).get("vias") or {}
        sizes = [(float(s["diameter_mm"]), float(s["hole_mm"])) for s in v.get("sizes") or []
                 if s.get("diameter_mm") and s.get("hole_mm")]
        tent = v.get("tenting") if isinstance(v.get("tenting"), dict) else {}
        return {"source": f"profile {profile.get('name')}", "sizes": sizes,
                "tent_top": None if tent.get("top_share") is None else tent["top_share"] >= 0.5,
                "tent_bottom": None if tent.get("bottom_share") is None else tent["bottom_share"] >= 0.5}
    from .specctra import _rule_defaults

    r = _rule_defaults(doc)
    d, h = r.get("via_d"), r.get("via_h")
    return {"source": "the board's RoutingVias rule", "sizes": [(d, h)] if d and h else [],
            "tent_top": None, "tent_bottom": None}


def check(doc: PcbDocument, profile: dict | None = None) -> dict:
    """Vias whose size or tenting departs from the house style."""
    want = house_via_style(doc, profile)
    vias = [v for v in doc.vias if v.component == NONE16]
    findings: list[dict] = []
    if want["sizes"]:
        off = [v for v in vias if not any(abs(v.diameter - d) <= TOL_MM and abs(v.hole - h) <= TOL_MM
                                          for d, h in want["sizes"])]
        if off:
            c = Counter((round(v.diameter, 3), round(v.hole, 3)) for v in off)
            findings.append({"code": "via_size_outside_style", "severity": "warn", "count": len(off),
                             "message": f"{len(off)} via(s) not in the style's sizes "
                                        f"{[f'{d}/{h} mm' for d, h in want['sizes']]}",
                             "sizes": [{"diameter_mm": d, "hole_mm": h, "count": n} for (d, h), n in c.items()]})
    for side in ("top", "bottom"):
        target = want[f"tent_{side}"]
        if target is None:
            continue
        off = [v for v in vias if getattr(v, f"tent_{side}") is not None and getattr(v, f"tent_{side}") != target]
        if off:
            nets = Counter(_net(doc, v.net) or "(no net)" for v in off)
            findings.append({"code": f"tenting_{side}_differs", "severity": "warn", "count": len(off),
                             "message": f"{len(off)} via(s) {'not ' if target else ''}tented on the {side}, "
                                        f"unlike the style", "by_net": dict(nets.most_common(MAX_LISTED))})
    return {"style": {**want, "sizes": [{"diameter_mm": d, "hole_mm": h} for d, h in want["sizes"]]},
            "vias": via_styles(doc), "matches": not findings, "findings": findings}


def read_back(doc: PcbDocument, *, net: str | None = None, diameter_mm: float | None = None,
              top: bool | None = None, bottom: bool | None = None) -> dict[str, Any]:
    """After a save: do the matching vias in the file carry the tenting asked for?"""
    sel = [v for v in doc.vias if v.component == NONE16
           and (net is None or (_net(doc, v.net) or "").upper() == net.upper())
           and (diameter_mm is None or abs(v.diameter - diameter_mm) <= 0.0127)]
    bad = [v for v in sel if (top is not None and v.tent_top != top) or (bottom is not None and v.tent_bottom != bottom)]
    return {"vias": len(sel), "as_asked": len(sel) - len(bad), "differ": len(bad),
            "differ_at_mm": [[round(v.x, 3), round(v.y, 3)] for v in bad[:MAX_LISTED]],
            "verified": bool(sel) and not bad}
