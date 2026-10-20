# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Brass contributors
# Adapted from Brass src/board/footprints.ts (TypeScript, Apache-2.0) for
# Altium Agent (2026): see docs/PROVENANCE.md.
"""Footprints as the placer needs them, read from a board file.

Each part's body box and pins in its own frame (rotation 0, top side,
origin at the component's reference point), so the placer can rotate,
flip and move it without Altium. Read from the .PcbDoc with the offline
reader (offline/files/pcbdoc.py), so a board that has had Import Changes
is enough: no Altium needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..offline.files.pcbdoc import NONE16, PcbDocument

#: Legacy (V6) layer ids of the binary primitives.
TOP_COPPER, BOTTOM_COPPER, MULTI_LAYER = 1, 32, 74


@dataclass
class Pin:
    name: str
    net: str | None
    x: float              # local frame, mm
    y: float
    w: float = 0.0        # pad size in the local frame (rotation 0), mm
    h: float = 0.0
    layer: str = "top"    # top | bottom | multi, seen from the footprint


@dataclass
class Box:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def w(self) -> float:
        return self.max_x - self.min_x

    @property
    def h(self) -> float:
        return self.max_y - self.min_y

    def to_dict(self) -> dict:
        return {"min_x_mm": round(self.min_x, 4), "min_y_mm": round(self.min_y, 4),
                "max_x_mm": round(self.max_x, 4), "max_y_mm": round(self.max_y, 4)}


@dataclass
class At:
    x: float
    y: float
    rotation: float
    side: str  # top | bottom


@dataclass
class Footprint:
    ref: str
    footprint: str
    comment: str
    box: Box                       # pads, silkscreen and mechanical primitives, local frame
    pins: list[Pin]
    locked: bool
    through_hole: bool
    at: At
    sheet: str | None = None    # the schematic sheet, for grouping
    pad_box: Box | None = None  # Altium Agent: copper only (pads), local frame
    height: float | None = None # mm above the board, when known
    extra: dict = field(default_factory=dict)


def _r4(v: float) -> float:
    return round(v * 10_000) / 10_000


def to_local(c: At, px: float, py: float) -> tuple[float, float]:
    """Board point -> the component's local frame.

    Altium flips a bottom-side part about its X axis (y -> -y), then rotates it
    counter-clockwise; undo the translation, the rotation, then the flip.
    (Brass measured this on Altium's WiFi card: only this order gives standard
    footprints for both bottom parts.)
    """
    dx, dy = px - c.x, py - c.y
    t = math.radians(c.rotation)
    x = dx * math.cos(t) + dy * math.sin(t)
    y = -dx * math.sin(t) + dy * math.cos(t)
    if c.side == "bottom":
        y = -y
    return _r4(x), _r4(y)


def to_board(c: At, lx: float, ly: float) -> tuple[float, float]:
    """Local point -> board (flip, rotate, translate): the inverse of to_local."""
    y0 = -ly if c.side == "bottom" else ly
    t = math.radians(c.rotation)
    return c.x + lx * math.cos(t) - y0 * math.sin(t), c.y + lx * math.sin(t) + y0 * math.cos(t)


def footprints_of(board: PcbDocument, sheet_of: dict[str, str] | None = None) -> list[Footprint]:
    """Every component on the board, in its own frame.

    ``sheet_of`` maps a designator to its schematic sheet (from the design
    model), so the placer can keep each sheet together.
    """
    sheet_of = {k.upper(): v for k, v in (sheet_of or {}).items()}
    entries: dict[int, dict] = {}
    for comp in board.components:
        at = At(comp.x_mm, comp.y_mm, comp.rotation, comp.side)
        entries[comp.index] = {"comp": comp, "at": at, "pts": [], "pad_pts": [], "pins": [], "th": False}

    def extend(e: dict, x: float, y: float, hx: float, hy: float) -> None:
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            e["pts"].append(to_local(e["at"], x + sx * hx, y + sy * hy))

    for pad in board.pads:
        e = entries.get(pad.component)
        if e is None or pad.component == NONE16:
            continue
        r = ((pad.rotation % 180) + 180) % 180
        swap = abs(r - 90) < 1
        odd = not swap and 1 < r < 179
        m = max(pad.size_x, pad.size_y)
        bx = (m if odd else pad.size_y if swap else pad.size_x) / 2
        by = (m if odd else pad.size_x if swap else pad.size_y) / 2
        extend(e, pad.x, pad.y, bx, by)
        n0 = len(e["pts"])
        e["pad_pts"].extend(e["pts"][n0 - 4:n0])
        if pad.hole > 0:
            e["th"] = True
        net = None if pad.net == NONE16 else (board.nets[pad.net] if pad.net < len(board.nets) else None)
        lx, ly = to_local(e["at"], pad.x, pad.y)
        rel = (((pad.rotation - e["at"].rotation) % 180) + 180) % 180
        across = abs(rel - 90) < 1
        odd_rel = not across and 1 < rel < 179
        w = m if odd_rel else pad.size_y if across else pad.size_x
        h = m if odd_rel else pad.size_x if across else pad.size_y
        if pad.hole > 0 or pad.layer == MULTI_LAYER:
            own = "multi"
        else:
            own = "bottom" if (pad.layer == BOTTOM_COPPER) != (e["at"].side == "bottom") else "top"
        if pad.name:
            e["pins"].append(Pin(pad.name, net, lx, ly, _r4(w), _r4(h), own))

    for t in board.tracks:
        e = entries.get(t.component)
        if e is None or t.component == NONE16:
            continue
        hw = t.width / 2
        extend(e, t.x1, t.y1, hw, hw)
        extend(e, t.x2, t.y2, hw, hw)
    for a in board.arcs:
        e = entries.get(a.component)
        if e is None or a.component == NONE16:
            continue
        pts = [a.start_angle, a.end_angle]
        end = a.end_angle
        while end <= a.start_angle:
            end += 360
        q = math.ceil(a.start_angle / 90) * 90
        while q < end:
            pts.append(q)
            q += 90
        hw = a.width / 2
        for deg in pts:
            extend(e, a.cx + a.radius * math.cos(math.radians(deg)),
                   a.cy + a.radius * math.sin(math.radians(deg)), hw, hw)

    out: list[Footprint] = []
    for e in entries.values():
        comp, pts = e["comp"], e["pts"]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            box = Box(min(xs), min(ys), max(xs), max(ys))
        else:
            box = Box(-0.5, -0.5, 0.5, 0.5)
        pp = e["pad_pts"]
        pad_box = Box(min(q[0] for q in pp), min(q[1] for q in pp), max(q[0] for q in pp), max(q[1] for q in pp)) \
            if pp else None
        out.append(Footprint(
            pad_box=pad_box,
            ref=comp.designator, footprint=comp.footprint, comment=comp.comment, box=box,
            pins=e["pins"], locked=comp.locked, through_hole=e["th"], at=e["at"],
            sheet=sheet_of.get(comp.designator.upper()),
            height=comp.height_mm or None,
        ))
    return sorted(out, key=lambda f: _natural(f.ref))


def _natural(s: str) -> list:
    import re

    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def board_box(f: Footprint, copper: bool = False) -> Box:
    """The part's body box on the board as it sits now (``copper``: its pads only)."""
    b = (f.pad_box or f.box) if copper else f.box
    pts = [to_board(f.at, x, y) for x, y in ((b.min_x, b.min_y), (b.max_x, b.min_y),
                                             (b.max_x, b.max_y), (b.min_x, b.max_y))]
    return Box(min(p[0] for p in pts), min(p[1] for p in pts),
               max(p[0] for p in pts), max(p[1] for p in pts))


def pin_on_board(f: Footprint, p: Pin) -> tuple[float, float]:
    return to_board(f.at, p.x, p.y)
