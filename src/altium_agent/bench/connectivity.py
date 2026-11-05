# SPDX-License-Identifier: Apache-2.0
"""Which pads a board's copper actually joins, from the saved geometry.

One model for the person's board and for an engine's result, so the two are
scored the same way:

* pads, tracks (arcs as chords) and vias of a net join when their copper
  touches on a shared layer (a through-hole pad or a via is on every
  layer);
* a pour joins whatever of its net sits inside its outline on its layer,
  and an internal plane joins every via and through-hole pad of its net.
  The pour's own copper is not read: it was poured around the person's
  tracks, and an engine's result has none. Counting the outline is
  generous (thermal relief and clearances are not modelled), and equally
  generous to both sides.

Only joins within a net are looked for. Copper of two nets touching is a
short, which is DRC's business, not this module's.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..routing.model import dist_point_rect, dist_point_seg, dist_seg_rect, dist_seg_seg

ALL = -1          # an item on every copper layer
MULTI = 74        # Altium's MultiLayer
#: Slack for coordinates rounded by a file format, mm.
EPS = 0.002


@dataclass
class Seg:
    net: str
    layer: int
    x1: float
    y1: float
    x2: float
    y2: float
    width: float


@dataclass
class Dot:
    """A via: a disc on every layer it spans (``layers`` None: every layer,
    a through via; otherwise the legacy ids of a blind or buried via's)."""
    net: str
    x: float
    y: float
    diameter: float
    layers: Optional[frozenset] = None


@dataclass
class PadShape:
    net: str
    layer: int        # ALL for through-hole
    x: float
    y: float
    hw: float
    hh: float
    rotation: float
    ref: str = ""
    name: str = ""


@dataclass
class Area:
    """A pour outline (``layer`` a copper layer) or a whole plane (``ALL``,
    joining vias and through-hole pads only; ``plane`` its legacy id, which
    decides whether a blind via reaches it)."""
    net: str
    layer: int
    outline: list[tuple[float, float]] = field(default_factory=list)
    plane: Optional[int] = None


def legacy_of(layer_id: int) -> Optional[int]:
    """The legacy id (1..32 copper, 39..54 planes) of a stack layer id."""
    if layer_id == 0x0100FFFF:
        return 32
    if layer_id >> 16 == 0x0100:
        return (layer_id & 0xFFFF)
    if layer_id >> 16 == 0x0101:
        return 38 + (layer_id & 0xFFFF)
    return None


def copper_order(doc) -> list[int]:
    """Legacy ids of the copper and plane layers, top to bottom."""
    out = []
    for s in doc.stack:
        if s.kind in ("copper", "plane"):
            lid = legacy_of(s.layer_id)
            if lid is not None:
                out.append(lid)
    return out


def via_span(order: list[int], start: int, end: int) -> Optional[frozenset]:
    """The layers a via from ``start`` to ``end`` passes, or None for one
    that passes them all (or names a layer the stack does not have)."""
    if start not in order or end not in order:
        return None
    i, j = sorted((order.index(start), order.index(end)))
    if i == 0 and j == len(order) - 1:
        return None
    return frozenset(order[i:j + 1])


def _inside(poly: list[tuple[float, float]], x: float, y: float) -> bool:
    hit = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _poly_layer(name: str) -> int | None:
    n = (name or "").upper()
    if n in ("TOP", "TOPLAYER"):
        return 1
    if n in ("BOTTOM", "BOTTOMLAYER"):
        return 32
    if n.startswith("MID"):
        digits = "".join(ch for ch in n if ch.isdigit())
        return 1 + int(digits) if digits else None
    return None


def arc_chords(cx, cy, r, start, end, max_deg: float = 10.0) -> list[tuple[float, float, float, float]]:
    sweep = (end - start) % 360.0 or 360.0
    n = max(1, int(math.ceil(sweep / max_deg)))
    pts = [(cx + r * math.cos(math.radians(start + sweep * k / n)),
            cy + r * math.sin(math.radians(start + sweep * k / n))) for k in range(n + 1)]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]


@dataclass
class Copper:
    """A board's copper, by kind, in mm from the board origin."""
    pads: list[PadShape] = field(default_factory=list)
    segs: list[Seg] = field(default_factory=list)
    dots: list[Dot] = field(default_factory=list)
    areas: list[Area] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _area(poly: list[tuple[float, float]]) -> float:
    if len(poly) < 3:
        return 0.0
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))) / 2


def copper_of(doc: PcbDocument, *, routing: bool = True) -> Copper:
    """The copper of ``doc``: its pads and pours, and (``routing``) its
    tracks, arcs and vias. Poured copper is replaced by its outline."""
    c = Copper()
    refs = {comp.index: comp.designator for comp in doc.components}
    for p in doc.pads:
        if p.net == NONE16:
            continue
        layer = ALL if (p.hole > 0 or p.layer == MULTI) else p.layer
        c.pads.append(PadShape(doc.net_name(p.net), layer, p.x, p.y, p.size_x / 2, p.size_y / 2,
                               p.rotation, refs.get(p.component, ""), p.name))
    board = [tuple(q) for q in doc.outline_poly or []]
    board_area = _area(board)
    for poly in doc.polygons or []:
        lid = _poly_layer(poly.get("layer", ""))
        if poly.get("net") and lid and len(poly.get("outline_mm") or []) >= 3 and not poly.get("shelved"):
            outline = [tuple(q) for q in poly["outline_mm"]]
            if board_area and _area(outline) < 0.02 * board_area:
                # The reader does not expand arcs in a pour outline, so a pour
                # drawn with them comes back as a sliver. Such pours are
                # (in the samples) the board's shape: use that, and say so.
                c.notes.append(f"pour {poly.get('name') or poly['net']} on {poly['layer']}: outline unreadable, "
                               "board outline used")
                outline = board
            c.areas.append(Area(poly["net"], lid, outline))
    for s in doc.stack:
        if s.kind == "plane":
            net = doc.board.get(f"PLANE{s.layer_id & 0xFFFF}NETNAME", "")
            if net and net != "(No Net)":
                c.areas.append(Area(net, ALL, plane=legacy_of(s.layer_id)))
    for r in doc.regions:
        # Netted copper regions a footprint owns (custom pad shapes) join
        # what sits on them in either case; free ones are drawn copper.
        if (r.net != NONE16 and r.polygon == NONE16 and r.kind == 0 and not r.is_keepout
                and 1 <= r.layer <= 32 and len(r.outline) >= 3 and (routing or r.component != NONE16)):
            c.areas.append(Area(doc.net_name(r.net), r.layer, [tuple(q) for q in r.outline]))
    for f in doc.fills:
        if f.net != NONE16 and 1 <= f.layer <= 32 and (routing or f.component != NONE16):
            cx, cy = (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2
            hx, hy = abs(f.x2 - f.x1) / 2, abs(f.y2 - f.y1) / 2
            co, si = math.cos(math.radians(f.rotation)), math.sin(math.radians(f.rotation))
            c.areas.append(Area(doc.net_name(f.net), f.layer,
                                [(cx + dx * co - dy * si, cy + dx * si + dy * co)
                                 for dx, dy in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]))
    if not routing:
        return c
    for t in doc.tracks:
        if t.net != NONE16 and t.polygon == NONE16 and (1 <= t.layer <= 32):
            c.segs.append(Seg(doc.net_name(t.net), t.layer, t.x1, t.y1, t.x2, t.y2, t.width))
    for a in doc.arcs:
        if a.net != NONE16 and a.polygon == NONE16 and (1 <= a.layer <= 32):
            for x1, y1, x2, y2 in arc_chords(a.cx, a.cy, a.radius, a.start_angle, a.end_angle):
                c.segs.append(Seg(doc.net_name(a.net), a.layer, x1, y1, x2, y2, a.width))
    order = copper_order(doc)
    for v in doc.vias:
        if v.net != NONE16:
            c.dots.append(Dot(doc.net_name(v.net), v.x, v.y, v.diameter,
                              via_span(order, v.layer_start, v.layer_end)))
    return c


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _bbox(kind: str, o) -> tuple[float, float, float, float]:
    if kind == "pad":
        r = math.hypot(o.hw, o.hh)
        return o.x - r, o.y - r, o.x + r, o.y + r
    if kind == "seg":
        h = o.width / 2
        return min(o.x1, o.x2) - h, min(o.y1, o.y2) - h, max(o.x1, o.x2) + h, max(o.y1, o.y2) + h
    h = o.diameter / 2
    return o.x - h, o.y - h, o.x + h, o.y + h


def _shares_layer(a, b) -> bool:
    """Layers are ALL, one legacy id, or a blind via's frozenset of them."""
    if a == ALL or b == ALL:
        return True
    if isinstance(a, frozenset):
        return bool(a & b) if isinstance(b, frozenset) else b in a
    if isinstance(b, frozenset):
        return a in b
    return a == b


def _layer_of(kind: str, o):
    if kind == "dot":
        return o.layers if o.layers else ALL
    return o.layer


def _touch(ka: str, a, kb: str, b) -> bool:
    if ka > kb:
        ka, a, kb, b = kb, b, ka, a
    la = _layer_of(ka, a)
    lb = _layer_of(kb, b)
    if not _shares_layer(la, lb):
        return False
    if ka == "dot" and kb == "dot":
        return math.hypot(a.x - b.x, a.y - b.y) <= (a.diameter + b.diameter) / 2 + EPS
    if ka == "dot" and kb == "pad":
        return dist_point_rect(a.x, a.y, b.x, b.y, b.hw, b.hh, b.rotation) <= a.diameter / 2 + EPS
    if ka == "dot" and kb == "seg":
        return dist_point_seg(a.x, a.y, b.x1, b.y1, b.x2, b.y2) <= (a.diameter + b.width) / 2 + EPS
    if ka == "pad" and kb == "pad":
        return (dist_point_rect(a.x, a.y, b.x, b.y, b.hw, b.hh, b.rotation) <= EPS
                or dist_point_rect(b.x, b.y, a.x, a.y, a.hw, a.hh, a.rotation) <= EPS)
    if ka == "pad" and kb == "seg":
        return dist_seg_rect(b.x1, b.y1, b.x2, b.y2, a.x, a.y, a.hw, a.hh, a.rotation) <= b.width / 2 + EPS
    # seg, seg
    return dist_seg_seg(a.x1, a.y1, a.x2, a.y2, b.x1, b.y1, b.x2, b.y2) <= (a.width + b.width) / 2 + EPS


@dataclass
class NetResult:
    pads: int
    islands: int           # groups of pads the copper leaves apart

    @property
    def missing(self) -> int:
        return max(0, self.islands - 1)


def connect(c: Copper) -> dict[str, NetResult]:
    """Per net with two or more pads: how many separate islands its pads
    fall into. One island is a net complete."""
    by_net: dict[str, list[tuple[str, object]]] = defaultdict(list)
    for p in c.pads:
        by_net[p.net].append(("pad", p))
    for s in c.segs:
        by_net[s.net].append(("seg", s))
    for d in c.dots:
        by_net[d.net].append(("dot", d))
    areas: dict[str, list[Area]] = defaultdict(list)
    for a in c.areas:
        areas[a.net].append(a)

    out: dict[str, NetResult] = {}
    for net, items in by_net.items():
        n_pads = sum(1 for k, _ in items if k == "pad")
        if n_pads < 2:
            continue
        net_areas = areas.get(net, [])
        uf = _UF(len(items) + len(net_areas))
        boxes = [_bbox(k, o) for k, o in items]
        order = sorted(range(len(items)), key=lambda i: boxes[i][0])
        active: list[int] = []
        for i in order:
            x0 = boxes[i][0]
            active = [j for j in active if boxes[j][2] >= x0 - EPS]
            for j in active:
                bj, bi = boxes[j], boxes[i]
                if bj[1] > bi[3] + EPS or bi[1] > bj[3] + EPS:
                    continue
                if uf.find(i) != uf.find(j) and _touch(items[i][0], items[i][1], items[j][0], items[j][1]):
                    uf.union(i, j)
            active.append(i)
        for ai, area in enumerate(net_areas):
            node = len(items) + ai
            for i, (k, o) in enumerate(items):
                layer = _layer_of(k, o)
                if area.layer == ALL:
                    if layer == ALL or (isinstance(layer, frozenset) and area.plane in layer):
                        uf.union(i, node)
                    continue
                if not _shares_layer(layer, area.layer):
                    continue
                pts = [(o.x, o.y)] if k != "seg" else [(o.x1, o.y1), (o.x2, o.y2)]
                if any(_inside(area.outline, x, y) for x, y in pts):
                    uf.union(i, node)
        roots = {uf.find(i) for i, (k, _) in enumerate(items) if k == "pad"}
        out[net] = NetResult(n_pads, len(roots))
    return out


def summary(results: dict[str, NetResult], nets: set[str] | None = None) -> dict:
    """Totals over ``nets`` (default: all): connections needed (pads - 1 per
    net), missing, and nets complete."""
    rs = {n: r for n, r in results.items() if nets is None or n in nets}
    needed = sum(r.pads - 1 for r in rs.values())
    missing = sum(r.missing for r in rs.values())
    return {"nets": len(rs), "nets_complete": sum(1 for r in rs.values() if r.missing == 0),
            "connections_needed": needed, "missing_connections": missing,
            "completion": round(1 - missing / needed, 4) if needed else 1.0,
            "incomplete_nets": sorted(n for n, r in rs.items() if r.missing)[:40]}
