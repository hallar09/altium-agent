# SPDX-License-Identifier: Apache-2.0
"""The picture after an action: a session's board, drawn from the files.

Numbered markers sit on what the check found, ratsnest lines show what is
still to connect, and the parts or nets the caller names are ringed. The
PNG is for reading (Read it); the SVG beside it zooms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..offline.files.pcbdoc import PcbDocument
from ..units import MILS_PER_MM


def render(doc: PcbDocument, out: Path, *, markers: list[dict] | None = None, nets: set[str] | None = None,
           highlight: list[str] | None = None, side: str = "top", ratsnest: bool = True,
           width: int = 1600, layers: list[str] | None = None,
           region_mm: list[float] | None = None) -> dict[str, Any]:
    """Draw ``doc`` to ``out`` (.svg, and .png when a browser can rasterize).

    ``layers``: only these copper layers' tracks, arcs and pads (through-hole
    pads, vias, the outline and the designators stay). ``region_mm``
    [x1, y1, x2, y2]: the picture cropped to that window."""
    from ..design.gate_review import _png
    from ..placement.footprints import board_box, footprints_of
    from ..render import PcbRenderOptions, render_pcb_svg
    from ..render.file_geometry import pcb_geometry, ratsnest_lines

    g = pcb_geometry(doc)
    marks = list(markers or [])
    if highlight:
        boxes = {f.ref.upper(): board_box(f) for f in footprints_of(doc)}
        for ref in highlight:
            b = boxes.get(ref.upper())
            if b is not None:
                marks.append({"x": round((b.min_x + b.max_x) / 2 * MILS_PER_MM, 1),
                              "y": round((b.min_y + b.max_y) / 2 * MILS_PER_MM, 1),
                              "r": max(30.0, max(b.w, b.h) / 2 * MILS_PER_MM + 10), "severity": "info",
                              "what": ref})
    for i, m in enumerate(marks, 1):
        m.setdefault("label", str(i))
    g["review"] = {"markers": marks, "lines": ratsnest_lines(doc, nets) if ratsnest else []}
    if region_mm is not None:
        x1, y1, x2, y2 = (v * MILS_PER_MM for v in region_mm)
        g["bbox"] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                     "width": x2 - x1, "height": y2 - y1}
    keep = None
    if layers:
        keep = set(layers) | {"MultiLayer"}
        for k in ("tracks", "arcs", "pads", "regions"):
            if isinstance(g.get(k), list):
                g[k] = [o for o in g[k] if o.get("layer") in keep or o.get("layer") not in _COPPER_NAMES(o)]
    out.parent.mkdir(parents=True, exist_ok=True)
    opts = PcbRenderOptions(interactive_legend=False, view_side=side)
    if region_mm is not None:
        opts.margin_mils = 20
    svg = render_pcb_svg(g, opts)
    pic = _png(svg, out.with_suffix(".svg"), g.get("bbox"), width)
    if not pic.get("png"):
        # No headless browser here (or it would not run): draw the PNG directly.
        note = pic.get("png_note")
        pic["png"] = _plot(doc, out.with_suffix(".png"), marks, g["review"]["lines"], side, width,
                           layers=layers, region_mm=region_mm)
        pic["png_note"] = None if pic["png"] else note
    return {**pic, "markers": [{"label": m["label"], "what": m.get("what", "")} for m in marks][:60]}


def _COPPER_NAMES(o: dict) -> set[str]:
    """Copper layer names (the ones ``layers`` filters); anything else is kept."""
    name = str(o.get("layer") or "")
    return {name} if (name in ("TopLayer", "BottomLayer") or name.startswith("MidLayer")
                      or name.startswith("InternalPlane")) else set()


def _plot(doc: PcbDocument, png: Path, marks: list[dict], lines: list[dict], side: str, width: int,
          layers: list[str] | None = None, region_mm: list[float] | None = None) -> str | None:
    """The board drawn with matplotlib: outline, part bodies and designators,
    pads (top red, bottom blue, through-hole grey), tracks, vias, ratsnest
    (yellow) and the numbered markers. mm, board frame; viewed from ``side``."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Polygon
    except ImportError:
        return None
    import math

    from ..interactive.board_model import legacy_id
    from ..placement.footprints import board_box, footprints_of

    show = {legacy_id(n) for n in layers} if layers else None
    mil = 1 / MILS_PER_MM
    flip = -1 if side == "bottom" else 1
    poly = doc.outline_poly or []
    xs = [q[0] for q in poly] or [c.x_mm for c in doc.components] or [0.0]
    ys = [q[1] for q in poly] or [c.y_mm for c in doc.components] or [0.0]
    for c in doc.components:                     # parts staged beside the board count too
        xs.append(c.x_mm)
        ys.append(c.y_mm)
    if region_mm is not None:
        xs, ys = [region_mm[0], region_mm[2]], [region_mm[1], region_mm[3]]
    w, h = max(xs) - min(xs) + 6, max(ys) - min(ys) + 6
    fig = plt.figure(figsize=(width / 100, max(4.0, width / 100 * h / max(w, 1e-6))), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#1f2937")
    if poly:
        ax.add_patch(Polygon([(flip * x, y) for x, y in poly], closed=True, fill=False, ec="#e5e7eb", lw=1.5))
    colors = {1: "#ef4444", 32: "#3b82f6"}
    for t in doc.tracks:
        if 1 <= t.layer <= 32 and t.polygon == 0xFFFF and t.component == 0xFFFF and (show is None or t.layer in show):
            ax.plot([flip * t.x1, flip * t.x2], [t.y1, t.y2], color=colors.get(t.layer, "#a3e635"),
                    lw=max(0.6, t.width * width / max(w, 1) * 0.7), solid_capstyle="round", alpha=0.85)
    for p in doc.pads:
        if show is not None and not (p.hole > 0 or p.layer == 74) and p.layer not in show:
            continue
        c = "#9ca3af" if p.hole > 0 or p.layer == 74 else colors.get(p.layer, "#a3e635")
        a, hw, hh = math.radians(p.rotation), p.size_x / 2, p.size_y / 2
        pts = [(p.x + dx * math.cos(a) - dy * math.sin(a), p.y + dx * math.sin(a) + dy * math.cos(a))
               for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
        ax.add_patch(Polygon([(flip * x, y) for x, y in pts], closed=True, fc=c, ec="none", alpha=0.9))
    for v in doc.vias:
        ax.add_patch(Circle((flip * v.x, v.y), v.diameter / 2, fc="#fbbf24", ec="none"))
    for f in footprints_of(doc):
        b = board_box(f)
        ax.add_patch(Polygon([(flip * b.min_x, b.min_y), (flip * b.max_x, b.min_y), (flip * b.max_x, b.max_y),
                              (flip * b.min_x, b.max_y)], closed=True, fill=False,
                             ec="#f9fafb" if f.at.side == side else "#6b7280", lw=0.6, ls="-" if f.at.side == side
                             else "--"))
        ax.text(flip * (b.min_x + b.max_x) / 2, (b.min_y + b.max_y) / 2, f.ref, color="#f9fafb",
                fontsize=max(5, min(9, 5 + b.w)), ha="center", va="center")
    for ln in lines:
        ax.plot([flip * ln["x1"] * mil, flip * ln["x2"] * mil], [ln["y1"] * mil, ln["y2"] * mil], color="#facc15",
                lw=0.6, alpha=0.8)
    sev = {"error": "#f43f5e", "warn": "#f59e0b", "info": "#22d3ee"}
    for m in marks:
        x, y, r = m["x"] * mil, m["y"] * mil, max(0.6, m.get("r", 40) * mil)
        ax.add_patch(Circle((flip * x, y), r, fill=False, ec=sev.get(m.get("severity"), "#22d3ee"), lw=1.4))
        ax.text(flip * x + r, y + r, m.get("label", ""), color=sev.get(m.get("severity"), "#22d3ee"), fontsize=9,
                fontweight="bold")
    xs2 = [flip * x for x in xs]
    pad = 0.3 if region_mm is not None else 3
    ax.set_xlim(min(xs2) - pad, max(xs2) + pad)
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(png, facecolor="#1f2937")
    plt.close(fig)
    return str(png)
