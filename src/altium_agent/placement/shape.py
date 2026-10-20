# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Brass contributors
# Adapted from Brass src/board/shape.ts (TypeScript, Apache-2.0) for Altium
# Agent (2026): see docs/PROVENANCE.md.
"""Board outlines: a rounded rectangle, a circle, a polygon, or a form factor.

Every outline is a closed chain of lines and counter-clockwise arcs in mm with
its lower-left bounding corner on the board origin, plus the chain the edge
keep-out follows (a card edge's gold fingers sit on the edge, so a form factor
leaves that stretch out), plus a polyline for inside tests.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

Pt = tuple[float, float]


def _r4(v: float) -> float:
    return round(v * 10_000) / 10_000


@dataclass
class Seg:
    kind: str  # line | arc
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    r: float = 0.0
    a1: float = 0.0
    a2: float = 0.0

    @staticmethod
    def line(x1, y1, x2, y2) -> Seg:
        return Seg("line", x1=x1, y1=y1, x2=x2, y2=y2)

    @staticmethod
    def arc(cx, cy, r, a1, a2) -> Seg:
        return Seg("arc", cx=cx, cy=cy, r=r, a1=a1, a2=a2)

    def to_dict(self) -> dict:
        if self.kind == "line":
            return {"kind": "line", "x1_mm": self.x1, "y1_mm": self.y1, "x2_mm": self.x2, "y2_mm": self.y2}
        return {"kind": "arc", "cx_mm": self.cx, "cy_mm": self.cy, "r_mm": self.r,
                "start_deg": self.a1, "end_deg": self.a2}


@dataclass
class CardEdge:
    x: float
    y: float
    rotation: float
    match: re.Pattern


@dataclass
class Outline:
    name: str
    segs: list[Seg]
    keepout: list[Seg]
    width: float
    height: float
    corner_radius: float
    poly: list[Pt]
    area: float
    rect: bool = False
    card_edge: CardEdge | None = None
    max_height: dict | None = None   # {"top": mm, "bottom": mm}
    thickness: dict | None = None    # {"mm": .., "source": ..}
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {"name": self.name, "width_mm": self.width, "height_mm": self.height,
               "corner_radius_mm": self.corner_radius, "area_mm2": round(self.area, 3),
               "segments": [s.to_dict() for s in self.segs]}
        if self.max_height:
            out["max_height_mm"] = self.max_height
        if self.thickness:
            out["thickness"] = self.thickness
        return out


def _poly_of(segs: list[Seg]) -> list[Pt]:
    out: list[Pt] = []
    for s in segs:
        if s.kind == "line":
            out.append((s.x1, s.y1))
        else:
            a2 = s.a2
            while a2 <= s.a1:
                a2 += 360
            n = max(2, math.ceil((a2 - s.a1) / 10))
            for i in range(n):
                a = math.radians(s.a1 + (a2 - s.a1) * i / n)
                out.append((s.cx + s.r * math.cos(a), s.cy + s.r * math.sin(a)))
    return out


def area_of(poly: list[Pt]) -> float:
    a = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return a / 2


def _finish(name: str, segs: list[Seg], corner_radius: float, keepout=None, **extra) -> Outline:
    poly = _poly_of(segs)
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return Outline(name=name, segs=segs, keepout=keepout if keepout is not None else segs,
                   width=_r4(max(xs) - min(xs)), height=_r4(max(ys) - min(ys)),
                   corner_radius=corner_radius, poly=poly, area=abs(area_of(poly)), **extra)


def rect_outline(w: float, h: float, r: float) -> Outline:
    R = max(0.0, min(r, w / 2, h / 2))
    if R > 0:
        segs = [Seg.line(R, 0, w - R, 0), Seg.arc(w - R, R, R, 270, 360),
                Seg.line(w, R, w, h - R), Seg.arc(w - R, h - R, R, 0, 90),
                Seg.line(w - R, h, R, h), Seg.arc(R, h - R, R, 90, 180),
                Seg.line(0, h - R, 0, R), Seg.arc(R, R, R, 180, 270)]
    else:
        segs = [Seg.line(0, 0, w, 0), Seg.line(w, 0, w, h), Seg.line(w, h, 0, h), Seg.line(0, h, 0, 0)]
    o = _finish(f"rect {w:g} x {h:g} mm r{R:g}", segs, R)
    o.rect = True
    return o


def circle_outline(d: float) -> Outline:
    r = d / 2
    return _finish(f"circle d{d:g} mm", [Seg.arc(r, r, r, 270, 90), Seg.arc(r, r, r, 90, 270)], r)


def polygon_outline(points: list[Pt], r: float) -> Outline:
    """A polygon (any orientation, any offset) with every convex corner rounded to r."""
    pts = [p for i, p in enumerate(points)
           if i == 0 or math.hypot(p[0] - points[i - 1][0], p[1] - points[i - 1][1]) > 1e-6]
    if len(pts) > 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("a board polygon needs at least 3 corners")
    if area_of(pts) < 0:
        pts = list(reversed(pts))
    mx, my = min(p[0] for p in pts), min(p[1] for p in pts)
    pts = [(x - mx, y - my) for x, y in pts]
    n = len(pts)
    corners = []
    for i, p in enumerate(pts):
        a, b = pts[(i - 1) % n], pts[(i + 1) % n]
        u = (a[0] - p[0], a[1] - p[1])
        v = (b[0] - p[0], b[1] - p[1])
        lu, lv = math.hypot(*u), math.hypot(*v)
        cross = (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])
        theta = math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (lu * lv))))
        if r <= 0 or cross <= 0 or theta < 1e-3 or math.pi - theta < 1e-3:
            corners.append((p, p, None))
            continue
        rr = r
        d = rr / math.tan(theta / 2)
        dmax = min(lu, lv) / 2
        if d > dmax:
            d = dmax
            rr = d * math.tan(theta / 2)
        t1 = (p[0] + u[0] / lu * d, p[1] + u[1] / lu * d)
        t2 = (p[0] + v[0] / lv * d, p[1] + v[1] / lv * d)
        bis = (u[0] / lu + v[0] / lv, u[1] / lu + v[1] / lv)
        lb = math.hypot(*bis)
        hh = rr / math.sin(theta / 2)
        c = (p[0] + bis[0] / lb * hh, p[1] + bis[1] / lb * hh)
        a1 = math.degrees(math.atan2(t1[1] - c[1], t1[0] - c[0]))
        a2 = math.degrees(math.atan2(t2[1] - c[1], t2[0] - c[0]))
        corners.append((t1, t2, Seg.arc(_r4(c[0]), _r4(c[1]), _r4(rr), _r4((a1 + 360) % 360), _r4((a2 + 360) % 360))))
    segs: list[Seg] = []
    for i in range(n):
        _, t2, arc = corners[i]
        nt1 = corners[(i + 1) % n][0]
        if arc:
            segs.append(arc)
        segs.append(Seg.line(_r4(t2[0]), _r4(t2[1]), _r4(nt1[0]), _r4(nt1[1])))
    return _finish(f"polygon ({n} corners) r{r:g}", segs, r)


def _mini_pcie(length: float) -> Outline:
    """PCI Express Mini Card: card-edge tab with the key notch, as Altium's WiFi example."""
    L = length
    segs = [
        Seg.arc(1.35, 2.4, 0.8, 0, 90), Seg.line(1.35, 3.2, 0, 3.2), Seg.line(0, 3.2, 0, L),
        Seg.line(0, L, 30, L), Seg.line(30, L, 30, 3.2), Seg.line(30, 3.2, 28.65, 3.2),
        Seg.arc(28.65, 2.4, 0.8, 90, 180), Seg.line(27.85, 2.4, 27.85, 0), Seg.line(27.85, 0, 11.9, 0),
        Seg.line(11.9, 0, 11.9, 3.25), Seg.arc(11.15, 3.25, 0.75, 0, 180), Seg.line(10.4, 3.25, 10.4, 0),
        Seg.line(10.4, 0, 2.15, 0), Seg.line(2.15, 0, 2.15, 2.4),
    ]
    keepout = [s for i, s in enumerate(segs) if i not in (8, 9, 10, 11, 12)]
    return _finish(
        f"form minipcie-{'full' if L > 30 else 'half'} (30 x {L:g} mm)", segs, 0, keepout,
        card_edge=CardEdge(15, 0.25, 0, re.compile(r"pcie|mini.?card|card.?edge|gf-2d|edge.?conn", re.IGNORECASE)),
        max_height={"top": 2.4, "bottom": 1.35},
        thickness={"mm": 1.0, "source": "Mini Card sockets (JAE MM60-52, TE 1775838) take a 1.00 mm card"},
    )


def _named_rect(name: str, w: float, h: float, r: float) -> Outline:
    o = rect_outline(w, h, r)
    o.name = name
    return o


FORM_FACTORS = {
    "minipcie-half": ("half-size PCI Express Mini Card, 30 x 26.8 mm, 52-pin card edge at the bottom",
                      lambda: _mini_pcie(26.8)),
    "minipcie-full": ("full-size PCI Express Mini Card, 30 x 50.95 mm, 52-pin card edge",
                      lambda: _mini_pcie(50.95)),
    "feather": ("Adafruit Feather, 50.8 x 22.86 mm, corners r2.54",
                lambda: _named_rect("form feather (50.8 x 22.86 mm)", 50.8, 22.86, 2.54)),
    "rpi-hat": ("Raspberry Pi HAT, 65 x 56.5 mm, corners r3",
                lambda: _named_rect("form rpi-hat (65 x 56.5 mm)", 65, 56.5, 3)),
    "credit-card": ("credit-card size, 85.6 x 54 mm, corners r3.18",
                    lambda: _named_rect("form credit-card (85.6 x 54 mm)", 85.6, 54, 3.18)),
}


def build_form(name: str) -> Outline:
    return FORM_FACTORS[name][1]()


def inside(poly: list[Pt], x: float, y: float) -> bool:
    """Point in polygon (even-odd)."""
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def distance_to_edge(poly: list[Pt], x: float, y: float) -> float:
    best = math.inf
    j = len(poly) - 1
    for i in range(len(poly)):
        x1, y1 = poly[j]
        x2, y2 = poly[i]
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy or 1)))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
        j = i
    return best


def outline_from_points(points: list[Pt], name: str = "board outline") -> Outline:
    """An outline from a board's own outline vertices (as the PcbDoc reader gives them)."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        raise ValueError("an outline needs at least 3 points")
    segs = [Seg.line(*pts[i], *pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    o = Outline(name=name, segs=segs, keepout=segs, width=_r4(max(xs) - min(xs)),
                height=_r4(max(ys) - min(ys)), corner_radius=0, poly=pts, area=abs(area_of(pts)))
    o.extra["origin_offset"] = (min(xs), min(ys))
    return o


def seg_text(segs: list[Seg]) -> str:
    """The segment list as text: 'L x1 y1 x2 y2;A cx cy r a1 a2;...' (mm)."""
    f = lambda v: f"{_r4(v):g}"
    return ";".join(f"L {f(s.x1)} {f(s.y1)} {f(s.x2)} {f(s.y2)}" if s.kind == "line"
                    else f"A {f(s.cx)} {f(s.cy)} {f(s.r)} {f(s.a1)} {f(s.a2)}" for s in segs)
