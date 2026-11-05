# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/specctra.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""The Specctra session (SES) format, as Altium Agent's routing results travel.

A power fan-out (routing/fanout.py) and a routing session (interactive/) both
write what they lay as an SES; route_import_ses turns it into the bridge's
track and via rows. This module reads the board's routing rules for those
files (widths, clearance, the via) and parses an SES back. Coordinates are mm
from the board origin, layers Altium's names (TopLayer, MidLayer1.., BottomLayer).

(The DSN export that fed an external autorouter is gone: routing is the
agent's, net by net.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..offline.files.pcbdoc import PcbDocument
from ..units import MM_PER_MIL

MULTILAYER = 74


def layer_map(doc: PcbDocument) -> dict[str, int]:
    """The board's copper layers: Altium's name -> legacy layer id."""
    out: dict[str, int] = {}
    for s in doc.stack:
        if s.kind == "copper":
            lid = 32 if s.layer_id == 0x0100FFFF else s.layer_id - 0x01000000
            out["TopLayer" if lid == 1 else "BottomLayer" if lid == 32 else f"MidLayer{lid - 1}"] = lid
    return out or {"TopLayer": 1, "BottomLayer": 32}


def import_rules(doc: PcbDocument) -> dict:
    """The rule values an SES import needs: the default and minimum width (a
    track narrower than its net's minimum is widened back), clearance, the via."""
    r = _rule_defaults(doc)
    return {"width_mm": r["width"], "min_width_mm": r["min_width"], "clearance_mm": r["clearance"],
            "outline_clearance_mm": r["outline_clearance"], "via_diameter_mm": r["via_d"],
            "via_hole_mm": r["via_h"],
            "net_min_width_mm": {n: mn for n, (_, mn) in net_width_rules(doc).items()}}


def _rule_defaults(doc: PcbDocument) -> dict:
    """Board-wide routing constraints from the PcbDoc rules (the 'All'-scoped rule of each kind).

    The via must satisfy every applicable rule at once: RoutingVias (preferred/min size), HoleSize (min hole)
    and MinimumAnnularRing; the outline clearance comes from a Clearance rule scoped to the board outline."""
    width = min_width = clearance = None
    via_d = via_h = hole_min = ring = None
    outline_gap = None
    for r in sorted(doc.rules, key=lambda r: r["priority"], reverse=True):  # priority 1 = highest, applied last
        if not r["enabled"]:
            continue
        v = r.get("values", {})
        k = r["kind"].lower()
        s1 = r["scope1"].strip().lower()
        if k == "clearance" and ("isboardoutline" in s1 or "isboardcutout" in s1):
            outline_gap = v.get("gap_mm") or outline_gap
            continue
        if s1 != "all":
            continue
        if k == "width":
            width = v.get("preferedwidth_mm") or v.get("minlimit_mm") or width
            min_width = v.get("minlimit_mm") or min_width
        elif k == "clearance" and r["scope2"].strip().lower() == "all":
            clearance = v.get("gap_mm") or clearance
        elif k == "routingvias":
            via_d = v.get("preferedwidth_mm") or v.get("minwidth_mm") or via_d
            via_h = v.get("preferedholewidth_mm") or v.get("minholewidth_mm") or via_h
        elif k == "holesize":
            hole_min = v.get("minlimit_mm") or hole_min
        elif k == "minimumannularring":
            ring = v.get("minimum_mm") or v.get("minring_mm") or ring
    via_h = max(via_h or 0.3, hole_min or 0.0)
    via_d = max(via_d or 0.6, round(via_h + 2 * (ring or 0.1), 4))
    clearance = clearance or 0.2032
    return {"width": width or 0.254, "min_width": min_width or 0.0, "clearance": clearance, "via_d": via_d,
            "via_h": via_h, "outline_clearance": max(outline_gap or clearance, clearance)}


def net_width_rules(doc: PcbDocument) -> dict[str, tuple[float, float]]:
    """Net -> (preferred, minimum) width in mm from enabled Width rules scoped InNet(...) / InNetClass(...)
    against All; the lowest priority number wins, as in Altium."""
    members = {c["name"]: c.get("members", []) for c in doc.classes if c.get("kind") == "net"}
    out: dict[str, tuple[float, float, int]] = {}
    for r in doc.rules:
        if r["kind"].lower() != "width" or not r["enabled"] or r["scope2"].strip().lower() not in ("all", ""):
            continue
        m = re.fullmatch(r"\s*In(Net|NetClass)\('([^']+)'\)\s*", r["scope1"])
        v = r["values"]
        if not m or "preferedwidth_mm" not in v:
            continue
        nets = [m.group(2)] if m.group(1) == "Net" else members.get(m.group(2), [])
        pri = int(r["priority"] or 0)
        for n in nets:
            if n not in out or pri < out[n][2]:
                out[n] = (max(v["preferedwidth_mm"], v.get("minlimit_mm", 0)), v.get("minlimit_mm", 0), pri)
    return {n: (p, mn) for n, (p, mn, _) in out.items()}


# ---------------------------------------------------------------------- SES import


def _sexpr(text: str):
    tokens = re.findall(r'"(?:[^"]*)"|\(|\)|[^\s()]+', text)
    stack: list[list] = [[]]
    for t in tokens:
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t[1:-1] if t.startswith('"') else t)
    return stack[0][0] if stack[0] else []


def _find(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


@dataclass
class SesResult:
    tracks: list[dict]  # x1,y1,x2,y2,width,layer(dsn name),net (mm)
    vias: list[dict]  # x,y,diameter,hole,net (mm)
    nets: int


def parse_ses(path: Path, via_hole_mm: float) -> SesResult:
    root = _sexpr(Path(path).read_text(encoding="utf-8", errors="replace"))
    routes = next(iter(_find(root, "routes")), [])
    res = next(iter(_find(routes, "resolution")), ["resolution", "um", "10"])
    unit, per = res[1].lower(), float(res[2])
    scale = {"um": 0.001, "mm": 1.0, "mil": MM_PER_MIL, "inch": MM_PER_MIL * 1000}.get(unit, 0.001) / per  # -> mm
    via_dia: dict[str, float] = {}
    via_layers: dict[str, list[str]] = {}
    via_hole: dict[str, float] = {}
    for lib in _find(routes, "library_out"):
        for ps in _find(lib, "padstack"):
            circles = [sh[1] for sh in _find(ps, "shape") if len(sh) > 1 and isinstance(sh[1], list)
                       and sh[1][0] == "circle"]
            dias = [float(c[2]) for c in circles]
            if dias:
                via_dia[ps[1]] = max(dias) * scale
            # The layers a padstack has copper on: a blind or buried via's
            # span (all layers, or one shape for "every layer", is through).
            via_layers[ps[1]] = [c[1] for c in circles]
            m = re.match(r"^VIA_(\d+)_(\d+)_", str(ps[1]))
            if m:
                via_hole[ps[1]] = int(m.group(2)) / 1000.0
    tracks, vias, nets = [], [], 0
    for no in _find(routes, "network_out"):
        for net in _find(no, "net"):
            nets += 1
            name = net[1]
            for wire in _find(net, "wire"):
                for pth in _find(wire, "path"):
                    layer, width = pth[1], float(pth[2]) * scale
                    xy = [float(v) * scale for v in pth[3:] if not isinstance(v, list)]
                    pts = list(zip(xy[0::2], xy[1::2]))
                    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                        if abs(x1 - x2) > 1e-6 or abs(y1 - y2) > 1e-6:
                            tracks.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": width, "layer": layer, "net": name})
            for via in _find(net, "via"):
                vias.append({"x": float(via[2]) * scale, "y": float(via[3]) * scale,
                             "diameter": via_dia.get(via[1], via_hole_mm * 2),
                             "hole": via_hole.get(via[1], via_hole_mm), "net": name,
                             "layers": via_layers.get(via[1], [])})
    return SesResult(tracks, vias, nets)
