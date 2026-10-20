# SPDX-License-Identifier: Apache-2.0
"""Render geometry from the saved design files, without Altium (Altium Agent).

The inherited SVG renderers (``pcb_svg``, ``sch_svg``) draw the geometry
payload the Pascal side sends from a live board or sheet. These functions
build the same payload from the offline readers, so a gate review can show
the board and the schematic as saved, with Altium closed, and look the same
as a live render. Units are mils; the board is in its own frame (relative to
the board origin), the schematic in sheet coordinates.

A ``review`` entry carries what the review wants marked on the picture:
``markers`` ({x, y, label, severity}) and ``lines`` (ratsnest, {x1, y1, x2,
y2, net}); both renderers draw it on top.
"""

from __future__ import annotations

import math
from collections import defaultdict
from html import escape
from typing import Any

from ..offline.files.pcbdoc import NONE16, PcbDocument, layer_name
from ..offline.files.schdoc import SchDocument
from ..units import MILS_PER_MM

_PAD_SHAPE = {1: "Round", 2: "Rectangular", 3: "Octagonal", 9: "RoundedRectangle"}
_RENDER_LAYER = {"KeepOut": "KeepOutLayer"}


def _mil(v: float) -> float:
    return round(v * MILS_PER_MM, 3)


def _layer(lid: int) -> str:
    n = layer_name(lid)
    return _RENDER_LAYER.get(n, n)


def pcb_geometry(doc: PcbDocument, *, pours: bool = False) -> dict[str, Any]:
    """The live geometry payload, built from a saved .PcbDoc.

    Poured polygons are left out unless ``pours`` (a solid pour hides the
    tracks under it); component texts are left out (the renderer labels
    parts itself, and comments are usually hidden in Altium); free text on
    copper and overlay layers stays.
    """
    net = doc.net_name
    comp = {i: c.designator for i, c in enumerate(doc.components)}
    outline = []
    for s in doc.outline_mm or []:
        seg: dict[str, Any] = {"x": _mil(s["x"]), "y": _mil(s["y"])}
        if s.get("arc") and s.get("center") is not None:
            seg.update({"kind": "arc", "cx": _mil(s["center"][0]), "cy": _mil(s["center"][1]),
                        "radius": _mil(s["radius"]), "angle1": s["start_angle"], "angle2": s["end_angle"]})
        outline.append(seg)
    poly = doc.outline_poly or []
    if poly:
        xs, ys = [_mil(x) for x, _ in poly], [_mil(y) for _, y in poly]
    else:
        xs = [_mil(p.x) for p in doc.pads] or [0.0]
        ys = [_mil(p.y) for p in doc.pads] or [0.0]
    bbox = {"x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys),
            "width": max(xs) - min(xs), "height": max(ys) - min(ys)}

    def nn(i: int) -> str:
        return net(i) if i != NONE16 else ""

    tracks = [{"layer": _layer(t.layer), "x1": _mil(t.x1), "y1": _mil(t.y1), "x2": _mil(t.x2), "y2": _mil(t.y2),
               "width": _mil(t.width), "net": nn(t.net)} for t in doc.tracks]
    arcs = [{"layer": _layer(a.layer), "cx": _mil(a.cx), "cy": _mil(a.cy), "r": _mil(a.radius),
             "start": a.start_angle, "end": a.end_angle, "width": _mil(a.width), "net": nn(a.net)} for a in doc.arcs]
    pads = [{"layer": _layer(p.layer), "x": _mil(p.x), "y": _mil(p.y), "x_size": _mil(p.size_x),
             "y_size": _mil(p.size_y), "shape": _PAD_SHAPE.get(p.shape, "Rectangular"), "rotation": p.rotation,
             "hole_size": _mil(p.hole), "net": nn(p.net), "name": p.name,
             "comp": comp.get(p.component, "") if p.component != NONE16 else ""} for p in doc.pads]
    vias = [{"x": _mil(v.x), "y": _mil(v.y), "size": _mil(v.diameter), "hole_size": _mil(v.hole),
             "net": nn(v.net)} for v in doc.vias]
    regions = [{"layer": _layer(r.layer), "net": nn(r.net), "pts": [[_mil(x), _mil(y)] for x, y in r.outline]}
               for r in doc.regions if len(r.outline) >= 3 and not r.is_board_cutout and r.kind == 0
               and (pours or r.polygon == NONE16)]
    for f in doc.fills:  # a fill is a rotated rectangle
        cx, cy = (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2
        hw, hh = abs(f.x2 - f.x1) / 2, abs(f.y2 - f.y1) / 2
        t = math.radians(f.rotation)
        pts = [[_mil(cx + dx * math.cos(t) - dy * math.sin(t)), _mil(cy + dx * math.sin(t) + dy * math.cos(t))]
               for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
        regions.append({"layer": _layer(f.layer), "net": nn(f.net), "pts": pts})
    texts = [{"layer": _layer(t.layer), "x": _mil(t.x), "y": _mil(t.y), "size": _mil(t.height),
              "rotation": t.rotation, "text": t.text} for t in doc.texts
             if t.text and not t.text.startswith(".") and t.component == NONE16
             and (t.layer <= 34 or t.layer == 74)]
    components = [{"des": c.designator, "x": _mil(c.x_mm), "y": _mil(c.y_mm), "rotation": c.rotation,
                   "layer": "BottomLayer" if c.side == "bottom" else "TopLayer"} for c in doc.components]
    return {"doc": doc.path.name, "units": "mil, board frame", "outline": outline, "bbox": bbox,
            "tracks": tracks, "arcs": arcs, "pads": pads, "vias": vias, "regions": regions, "texts": texts,
            "components": components}


def ratsnest_lines(doc: PcbDocument, nets: set[str] | None = None, limit: int = 400) -> list[dict]:
    """Unrouted connections as lines between the nearest pads of a net's copper islands."""
    from ..analysis.copper import net_islands

    where = {}
    for p in doc.pads:
        label = f"{doc.designator(p.component)}-{p.name}" if p.component != NONE16 else f"FreePad-{p.name}"
        where[label] = (p.x, p.y)
    out: list[dict] = []
    for name, info in net_islands(doc).items():
        if info["unrouted_connections"] <= 0 or (nets is not None and name not in nets):
            continue
        groups = [[where[x] for x in isl if x in where] for isl in info["islands"]]
        groups = [g for g in groups if g]
        joined = groups[:1]
        rest = groups[1:]
        while rest and joined:  # Prim's over islands, nearest pad pairs
            best = None
            for i, g in enumerate(rest):
                for a in (p for j in joined for p in j):
                    for b in g:
                        d = math.dist(a, b)
                        if best is None or d < best[0]:
                            best = (d, i, a, b)
            _, i, a, b = best
            out.append({"x1": _mil(a[0]), "y1": _mil(a[1]), "x2": _mil(b[0]), "y2": _mil(b[1]), "net": name})
            joined.append(rest.pop(i))
            if len(out) >= limit:
                return out
    return out


_POWER_STYLE = {"circle": 0, "arrow": 1, "bar": 2, "wave": 3, "power_ground": 4, "signal_ground": 5,
                "earth": 6}


def sch_geometry(sheet: SchDocument) -> dict[str, Any]:
    """The live schematic geometry payload, built from a saved .SchDoc (symbols as boxes)."""
    components, pins = [], []
    for c in sheet.components:
        des = c.designator or "?"
        bases = [p.base for p in c.pins if not p.hidden] or [c.location]
        xs, ys = [b[0] for b in bases], [b[1] for b in bases]
        x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
        if x2 - x1 < 100:
            x1, x2 = (x1 + x2) / 2 - 50, (x1 + x2) / 2 + 50
        if y2 - y1 < 100:
            y1, y2 = (y1 + y2) / 2 - 50, (y1 + y2) / 2 + 50
        components.append({"des": des, "lib_ref": c.lib_ref or c.comment, "x": c.location[0], "y": c.location[1],
                           "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
        for p in c.pins:
            if p.hidden:
                continue
            dx, dy = p.hotspot[0] - p.base[0], p.hotspot[1] - p.base[1]
            rot = 0 if dx > 0 else 180 if dx < 0 else 90 if dy > 0 else 270
            pins.append({"comp": des, "des": p.designator, "name": p.name, "electrical": p.electrical,
                         "x": p.base[0], "y": p.base[1], "rot": rot, "len": math.hypot(dx, dy)})
    labels: dict[str, list] = defaultdict(list)
    for lab in sheet.labels:
        if not lab.points:
            continue
        x, y = lab.points[0]
        if lab.kind == "net_label":
            labels["net_labels"].append({"text": lab.text, "x": x, "y": y})
        elif lab.kind == "power_port":
            style = _POWER_STYLE.get(str(lab.extra.get("style", "")), 2)
            labels["power_ports"].append({"text": lab.text, "x": x, "y": y, "style": style,
                                          "rot": 270 if style in (4, 5, 6) else 90})
        elif lab.kind == "port":
            labels["ports"].append({"text": lab.text, "x": x, "y": y, "w": float(lab.extra.get("width", 600) or 600)})
    symbols = [{"name": s.name, "filename": s.file_name, "x": s.location[0], "y": s.location[1],
                "w": s.size[0], "h": s.size[1],
                "entries": [{"name": e.text, "x": e.points[0][0], "y": e.points[0][1]} for e in s.entries if e.points]}
               for s in sheet.sheet_symbols]
    return {"doc": sheet.path.name, "units": "mil, sheet", "components": components, "pins": pins,
            "wires": [{"verts": [list(p) for p in w]} for w in sheet.wires],
            "buses": [{"verts": [list(p) for p in b]} for b in sheet.buses],
            "junctions": [{"x": j[0], "y": j[1]} for j in sheet.junctions],
            "sheet_symbols": symbols, **labels}


def review_overlay_svg(review: dict[str, Any], flip_text=None, *, flip_y: bool = True, bottom: bool = False) -> str:
    """The review layer: ratsnest lines and numbered markers, drawn last.

    Labels are drawn upright in both views (counter-flipped like the
    designators); a marker with a ``side`` shows only in that side's view.
    """
    out = ['<g class="review" data-layer="Review">']
    for ln in review.get("lines") or []:
        out.append(f'<line class="ratsnest" x1="{ln["x1"]}" y1="{ln["y1"]}" x2="{ln["x2"]}" y2="{ln["y2"]}" '
                   f'stroke="#ffd400" stroke-width="6" stroke-dasharray="14 8"/>')
    colours = {"error": "#ff2d55", "warn": "#ff9f0a", "info": "#64d2ff"}
    view = "bottom" if bottom else "top"
    for i, m in enumerate(review.get("markers") or [], 1):
        if m.get("side") and m["side"] != view:
            continue
        c = colours.get(m.get("severity", "warn"), "#ff9f0a")
        x, y = float(m["x"]), float(m["y"])
        r = min(150.0, float(m.get("r", 40)))
        out.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="none" stroke="{c}" stroke-width="8"/>')
        lx, ly = x + r * 0.75, y + r * 0.75
        sx = -1 if bottom else 1
        sy = -1 if flip_y else 1
        out.append(f'<g transform="translate({lx},{ly}) scale({sx},{sy})"><text x="0" y="0" font-size="48" '
                   f'font-weight="700" fill="{c}" font-family="sans-serif">{escape(str(m.get("label") or i))}'
                   f'</text></g>')
    out.append("</g>")
    return "".join(out)
