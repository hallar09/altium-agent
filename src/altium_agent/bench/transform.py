# SPDX-License-Identifier: Apache-2.0
"""Take a finished board apart in memory, and put a placement back on it.

Everything here works on an offline :class:`PcbDocument` and returns a new
one; the file on disk is never written (there is no PcbDoc writer, and the
benchmark must never touch a person's board anyway).

* :func:`strip_routing` removes what a router lays down: free tracks, arcs
  and vias, poured copper, and free copper regions and fills. It keeps the
  design the router is given: footprint copper, pour boundaries (as
  ``doc.polygons``), keep-outs, board cutouts and rules. Poured copper goes
  because it was poured around the person's tracks, so it would hand an
  engine channels cut to the shape of the answer (as in layout/bench.py).
* :func:`move_components` moves parts rigidly, with every primitive they
  own, using the placer's own frame (placement/footprints.py), so a plan
  applied here sits exactly where ``place_apply`` would put it.
* :func:`stage_parts` lines the movable parts up beside the board, which is
  where Altium leaves them after Import Changes: the starting point of a
  placement task, with the person's positions out of sight.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..placement.footprints import At, footprints_of, to_board, to_local

#: Top/bottom pairs of legacy layer ids: copper, overlay, paste, solder mask.
_FLIP = {1: 32, 32: 1, 33: 34, 34: 33, 35: 36, 36: 35, 37: 38, 38: 37}

#: Gap between staged parts, and between the board and the first of them, mm.
STAGE_GAP_MM = 2.0


def _free(component: int) -> bool:
    return component == NONE16


def _copper(layer: int) -> bool:
    return 1 <= layer <= 32 or 39 <= layer <= 54


def _drawn(p) -> bool:
    """Free copper a router or a pour would lay down."""
    return _free(p.component) and (_copper(p.layer) or p.polygon != NONE16 or p.net != NONE16)


def strip_routing(doc: PcbDocument) -> PcbDocument:
    """The board without the copper a router or a pour would lay down.

    Kept: everything a footprint owns, keep-outs (the KeepOut layer and
    keep-out objects), board cutouts, named regions, and drawing on the
    non-copper layers (outline, assembly, mechanical).
    """
    def keep_region(r) -> bool:
        if not _free(r.component) or r.is_keepout or r.is_board_cutout:
            return True
        return r.polygon == NONE16 and r.kind != 0

    return replace(
        doc,
        tracks=[t for t in doc.tracks if t.keepout or not _drawn(t)],
        arcs=[a for a in doc.arcs if not _drawn(a)],
        vias=[v for v in doc.vias if not _free(v.component)],
        regions=[r for r in doc.regions if keep_region(r)],
        fills=[f for f in doc.fills if not _free(f.component) or not (_copper(f.layer) or f.net != NONE16)],
        warnings=list(doc.warnings),
    )


def _rel_angle(angle: float, old: At, new: At) -> float:
    """An angle in the old part frame, re-expressed for the new placement."""
    rel = angle - old.rotation
    if old.side != new.side:
        rel = -rel
    return (new.rotation + rel) % 360


def _layer(layer: int, flip: bool) -> int:
    return _FLIP.get(layer, layer) if flip else layer


def move_components(doc: PcbDocument, moves: dict[str, At]) -> PcbDocument:
    """``doc`` with each designator in ``moves`` (case-insensitive) moved
    there, with its pads, tracks, arcs, fills, regions and texts.

    Raises ``KeyError`` naming designators that are not on the board, so a
    plan for another board is caught rather than half applied.
    """
    by_ref = {c.designator.upper(): c for c in doc.components}
    unknown = [r for r in moves if r.upper() not in by_ref]
    if unknown:
        raise KeyError(f"not on {doc.path.name}: {', '.join(sorted(unknown)[:10])}")
    plan: dict[int, tuple[At, At]] = {}
    for ref, new in moves.items():
        c = by_ref[ref.upper()]
        plan[c.index] = (At(c.x_mm, c.y_mm, c.rotation, c.side),
                         At(new.x, new.y, new.rotation % 360, new.side))

    def pt(idx: int, x: float, y: float) -> tuple[float, float]:
        old, new = plan[idx]
        return to_board(new, *to_local(old, x, y))

    def flips(idx: int) -> bool:
        old, new = plan[idx]
        return old.side != new.side

    out = copy.copy(doc)
    out.components = [replace(c, x_mm=plan[c.index][1].x, y_mm=plan[c.index][1].y,
                              rotation=plan[c.index][1].rotation, side=plan[c.index][1].side)
                      if c.index in plan else c for c in doc.components]
    pads = []
    for p in doc.pads:
        if p.component in plan:
            x, y = pt(p.component, p.x, p.y)
            p = replace(p, x=x, y=y, layer=_layer(p.layer, flips(p.component)),
                        rotation=_rel_angle(p.rotation, *plan[p.component]))
        pads.append(p)
    out.pads = pads
    tracks = []
    for t in doc.tracks:
        if t.component in plan:
            x1, y1 = pt(t.component, t.x1, t.y1)
            x2, y2 = pt(t.component, t.x2, t.y2)
            t = replace(t, x1=x1, y1=y1, x2=x2, y2=y2, layer=_layer(t.layer, flips(t.component)))
        tracks.append(t)
    out.tracks = tracks
    arcs = []
    for a in doc.arcs:
        if a.component in plan:
            old, new = plan[a.component]
            cx, cy = pt(a.component, a.cx, a.cy)
            s, e = _rel_angle(a.start_angle, old, new), _rel_angle(a.end_angle, old, new)
            if old.side != new.side:
                s, e = e, s        # a mirror reverses the sweep
            a = replace(a, cx=cx, cy=cy, start_angle=s, end_angle=e, layer=_layer(a.layer, flips(a.component)))
        arcs.append(a)
    out.arcs = arcs
    fills = []
    for f in doc.fills:
        if f.component in plan:
            hx, hy = (f.x2 - f.x1) / 2, (f.y2 - f.y1) / 2
            cx, cy = pt(f.component, (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2)
            f = replace(f, x1=cx - hx, y1=cy - hy, x2=cx + hx, y2=cy + hy,
                        rotation=_rel_angle(f.rotation, *plan[f.component]),
                        layer=_layer(f.layer, flips(f.component)))
        fills.append(f)
    out.fills = fills
    out.regions = [replace(r, outline=[pt(r.component, x, y) for x, y in r.outline],
                           layer=_layer(r.layer, flips(r.component)))
                   if r.component in plan else r for r in doc.regions]
    out.texts = [replace(t, x=pt(t.component, t.x, t.y)[0], y=pt(t.component, t.x, t.y)[1],
                         rotation=_rel_angle(t.rotation, *plan[t.component]),
                         layer=_layer(t.layer, flips(t.component)))
                 if t.component in plan else t for t in doc.texts]
    out.warnings = list(doc.warnings)
    return out


def apply_placements(doc: PcbDocument, placements: list[dict]) -> PcbDocument:
    """``doc`` with a plan's ``placements`` (``ref``, ``x_mm``, ``y_mm``,
    ``rotation``, ``side``, as place_plan writes them) applied."""
    return move_components(doc, {
        str(p["ref"]): At(float(p["x_mm"]), float(p["y_mm"]), float(p.get("rotation", 0) or 0),
                          str(p.get("side", "top") or "top"))
        for p in placements})


def stage_parts(doc: PcbDocument, refs: list[str]) -> PcbDocument:
    """``doc`` with ``refs`` lined up in rows to the right of the board, top
    side, rotation 0, in designator order: where Import Changes leaves them.

    The rows are as tall as the board, so the staging area is roughly
    square and never overlaps the outline.
    """
    want = {r.upper() for r in refs}
    fps = [f for f in footprints_of(doc) if f.ref.upper() in want]
    poly = doc.outline_poly
    if poly:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        x0, y_lo, y_hi = max(xs) + STAGE_GAP_MM * 5, min(ys), max(ys)
    else:
        xs = [c.x_mm for c in doc.components] or [0.0]
        ys = [c.y_mm for c in doc.components] or [0.0]
        x0, y_lo, y_hi = max(xs) + 50.0, min(ys), max(ys)
    height = max(y_hi - y_lo, 20.0)
    moves: dict[str, At] = {}
    x, y, col_w = x0, y_lo, 0.0
    for f in fps:
        w, h = f.box.w, f.box.h
        if y + h > y_lo + height and y > y_lo:
            x, y, col_w = x + col_w + STAGE_GAP_MM, y_lo, 0.0
        # the body box's lower-left corner lands on (x, y)
        moves[f.ref] = At(round(x - f.box.min_x, 4), round(y - f.box.min_y, 4), 0.0, "top")
        y += h + STAGE_GAP_MM
        col_w = max(col_w, w)
    return move_components(doc, moves)


def positions(doc: PcbDocument) -> dict[str, dict]:
    """Every part's position, as a plan's placement entry."""
    return {c.designator: {"ref": c.designator, "x_mm": round(c.x_mm, 4), "y_mm": round(c.y_mm, 4),
                           "rotation": round(c.rotation % 360, 3), "side": c.side}
            for c in doc.components}


def distance(a: dict, b: dict) -> float:
    return math.hypot(float(a["x_mm"]) - float(b["x_mm"]), float(a["y_mm"]) - float(b["y_mm"]))
