# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/copper.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Offline copper connectivity: which pads of each net are actually joined by copper.

Per net, pads, tracks, arcs, vias, poured polygon regions and internal planes are joined by geometric
contact on shared copper layers. A net whose pads fall into more than one island has unrouted
connections (Altium's ratsnest). Same-net only: shorts between nets are the DRC's job.
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..offline.files.pcbdoc import COPPER_LEGACY, NONE16, PcbDocument

MULTILAYER = 74
PLANE_LAYERS = set(range(39, 55))
SIGNAL_LAYERS = set(range(1, 33))
TOL = 0.005  # mm


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
            self.p[rb] = ra


def _seg_point_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_seg_dist(a, b, c, d) -> float:
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])

    d1, d2, d3, d4 = ccw(c, d, a), ccw(c, d, b), ccw(a, b, c), ccw(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(_seg_point_dist(*a, *c, *d), _seg_point_dist(*b, *c, *d),
               _seg_point_dist(*c, *a, *b), _seg_point_dist(*d, *a, *b))


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


class _Item:
    __slots__ = ("kind", "layers", "geom", "r", "label", "bbox")

    def __init__(self, kind, layers, geom, r, label, bbox):
        self.kind, self.layers, self.geom, self.r, self.label, self.bbox = kind, layers, geom, r, label, bbox


def _pad_layers(layer: int) -> set[int]:
    return set(COPPER_LEGACY) if layer == MULTILAYER else {layer}


def _via_layers(start: int, end: int) -> set[int]:
    lo, hi = min(start, end), max(start, end)
    return {lyr for lyr in SIGNAL_LAYERS if lo <= lyr <= hi} | (PLANE_LAYERS if lo == 1 and hi == 32 else set())


def _arc_points(a, n: int = 8):
    sweep = (a.end_angle - a.start_angle) % 360.0 or 360.0
    return [(a.cx + a.radius * math.cos(math.radians(a.start_angle + sweep * i / n)),
             a.cy + a.radius * math.sin(math.radians(a.start_angle + sweep * i / n))) for i in range(n + 1)]


_EDGE_CELL = 1.0
_edge_index: dict[int, dict[tuple[int, int], list]] = {}


def _region_edges_near(reg: _Item, bbox, r: float) -> list:
    """Region edges whose grid cells overlap bbox (index built once per region item)."""
    idx = _edge_index.get(id(reg))
    if idx is None:
        idx = defaultdict(list)
        pts = reg.geom
        for e1, e2 in zip(pts, pts[1:] + pts[:1]):
            for gx in range(int(math.floor(min(e1[0], e2[0]) / _EDGE_CELL)), int(math.floor(max(e1[0], e2[0]) / _EDGE_CELL)) + 1):
                for gy in range(int(math.floor(min(e1[1], e2[1]) / _EDGE_CELL)), int(math.floor(max(e1[1], e2[1]) / _EDGE_CELL)) + 1):
                    idx[(gx, gy)].append((e1, e2))
        _edge_index[id(reg)] = idx
    found = {}
    x0, y0, x1, y1 = bbox[0] - r, bbox[1] - r, bbox[2] + r, bbox[3] + r
    for gx in range(int(math.floor(x0 / _EDGE_CELL)), int(math.floor(x1 / _EDGE_CELL)) + 1):
        for gy in range(int(math.floor(y0 / _EDGE_CELL)), int(math.floor(y1 / _EDGE_CELL)) + 1):
            for e in idx.get((gx, gy), ()):
                found[id(e)] = e
    return list(found.values())


def _touch(a: _Item, b: _Item) -> bool:
    if not (a.layers & b.layers):
        return False
    if a.bbox[0] > b.bbox[2] + TOL or b.bbox[0] > a.bbox[2] + TOL or a.bbox[1] > b.bbox[3] + TOL or b.bbox[1] > a.bbox[3] + TOL:
        return False
    if a.kind == "region" or b.kind == "region":
        reg, other = (a, b) if a.kind == "region" else (b, a)
        if other.kind == "region":
            return any(_point_in_poly(x, y, reg.geom) for x, y in other.geom) or \
                any(_point_in_poly(x, y, other.geom) for x, y in reg.geom)
        pts = other.geom if other.kind in ("seg", "poly") else [other.geom]
        # Item overlapping a region edge (only edges in grid cells near the item are tested).
        edges = _region_edges_near(reg, other.bbox, other.r)
        segs = list(zip(pts, pts[1:])) if len(pts) > 1 else [(pts[0], pts[0])]
        if any(_seg_seg_dist(p, q, e1, e2) <= other.r + TOL for p, q in segs for e1, e2 in edges):
            return True
        return _point_in_poly(pts[0][0], pts[0][1], reg.geom)
    ga = a.geom if a.kind in ("seg", "poly") else [a.geom]
    gb = b.geom if b.kind in ("seg", "poly") else [b.geom]
    sa = list(zip(ga, ga[1:])) if len(ga) > 1 else [(ga[0], ga[0])]
    sb = list(zip(gb, gb[1:])) if len(gb) > 1 else [(gb[0], gb[0])]
    reach = a.r + b.r + TOL
    return any(_seg_seg_dist(p, q, s, t) <= reach for p, q in sa for s, t in sb)


def net_islands(pcb: PcbDocument, *, include_planes: bool = True, with_vias: bool = False) -> dict[str, dict]:
    """net -> {'islands': [[pad labels], ...], 'unrouted_connections': n}. Only nets with >= 2 pads.
    with_vias: islands also list their vias as 'VIA@x,y' (which pads already reach an inner layer)."""
    _edge_index.clear()  # keyed by id(); never reuse across calls
    by_net: dict[int, list[_Item]] = defaultdict(list)
    for p in pcb.pads:
        if p.net == NONE16:
            continue
        label = f"{pcb.designator(p.component)}-{p.name}" if p.component != NONE16 else f"FreePad-{p.name}"
        r = max(p.size_x, p.size_y, p.hole) / 2.0
        by_net[p.net].append(_Item("pad", _pad_layers(p.layer), (p.x, p.y), r, label, (p.x - r, p.y - r, p.x + r, p.y + r)))
    for t in pcb.tracks:
        if t.net == NONE16 or t.layer not in COPPER_LEGACY:
            continue
        h = t.width / 2
        by_net[t.net].append(_Item("seg", {t.layer}, [(t.x1, t.y1), (t.x2, t.y2)], h, None,
                                   (min(t.x1, t.x2) - h, min(t.y1, t.y2) - h, max(t.x1, t.x2) + h, max(t.y1, t.y2) + h)))
    for a in pcb.arcs:
        if a.net == NONE16 or a.layer not in COPPER_LEGACY:
            continue
        pts = _arc_points(a)
        h = a.width / 2
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        by_net[a.net].append(_Item("poly", {a.layer}, pts, h, None, (min(xs) - h, min(ys) - h, max(xs) + h, max(ys) + h)))
    for v in pcb.vias:
        if v.net == NONE16:
            continue
        r = v.diameter / 2
        by_net[v.net].append(_Item("via", _via_layers(v.layer_start, v.layer_end), (v.x, v.y), r,
                                   f"VIA@{v.x:.3f},{v.y:.3f}" if with_vias else None,
                                   (v.x - r, v.y - r, v.x + r, v.y + r)))
    for rg in pcb.regions:
        if rg.net == NONE16 or rg.layer not in COPPER_LEGACY or rg.is_keepout or rg.kind != 0 or len(rg.outline) < 3:
            continue
        xs, ys = [x for x, _ in rg.outline], [y for _, y in rg.outline]
        by_net[rg.net].append(_Item("region", {rg.layer}, rg.outline, 0.0, None, (min(xs), min(ys), max(xs), max(ys))))
    for f in pcb.fills:
        if f.net == NONE16 or f.layer not in COPPER_LEGACY:
            continue
        poly = [(f.x1, f.y1), (f.x2, f.y1), (f.x2, f.y2), (f.x1, f.y2)]
        by_net[f.net].append(_Item("region", {f.layer}, poly, 0.0, None,
                                   (min(f.x1, f.x2), min(f.y1, f.y2), max(f.x1, f.x2), max(f.y1, f.y2))))

    plane_net_layers: dict[str, set[int]] = defaultdict(set)
    if include_planes:
        for i in range(1, 17):
            name = pcb.board.get(f"PLANE{i}NETNAME", "")
            if name and name != "(No Net)":
                plane_net_layers[name].add(38 + i)

    result: dict[str, dict] = {}
    cell = 2.0
    for net_idx, items in by_net.items():
        name = pcb.net_name(net_idx)
        pads = [i for i, it in enumerate(items) if it.kind == "pad"]
        if len(pads) < 2:
            continue
        uf = _UF(len(items) + 1)
        plane_node = len(items)
        planes = plane_net_layers.get(name, set())
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, it in enumerate(items):
            x0, y0, x1, y1 = it.bbox
            if (x1 - x0) / cell * (y1 - y0) / cell > 400:  # large regions: test against everything
                grid[("big",)].append(i)  # type: ignore[index]
                continue
            for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
                for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                    grid[(gx, gy)].append(i)
            if planes and it.kind in ("via", "pad") and it.layers & planes:
                uf.union(i, plane_node)
        big = grid.pop(("big",), [])  # type: ignore[arg-type]
        checked: set[tuple[int, int]] = set()
        for members in grid.values():
            for ai in range(len(members)):
                for bi in range(ai + 1, len(members)):
                    a, b = members[ai], members[bi]
                    key = (a, b) if a < b else (b, a)
                    if key in checked:
                        continue
                    checked.add(key)
                    if uf.find(a) != uf.find(b) and _touch(items[a], items[b]):
                        uf.union(a, b)
        for g in big:
            for j in range(len(items)):
                if j != g and uf.find(g) != uf.find(j) and _touch(items[g], items[j]):
                    uf.union(g, j)
        groups: dict[int, list[str]] = defaultdict(list)
        for i in pads:
            groups[uf.find(i)].append(items[i].label)
        if with_vias:
            for i, it in enumerate(items):
                if it.kind == "via" and uf.find(i) in groups:  # only vias on a pad's island
                    groups[uf.find(i)].append(it.label)
        islands = sorted((sorted(v) for v in groups.values()), key=len, reverse=True)
        result[name] = {"islands": islands, "unrouted_connections": len(islands) - 1}
    return result


def ratsnest(pcb: PcbDocument) -> dict:
    nets = net_islands(pcb)
    open_nets = {n: v for n, v in nets.items() if v["unrouted_connections"] > 0}
    total = sum(v["unrouted_connections"] for v in nets.values())
    return {
        "nets_checked": len(nets),
        "fully_routed_nets": len(nets) - len(open_nets),
        "nets_with_unrouted_connections": len(open_nets),
        "unrouted_connections": total,
        "routing_completion_pct": round(100.0 * (1 - len(open_nets) / len(nets)), 1) if nets else 100.0,
        "open_nets": [{"net": n, "unrouted_connections": v["unrouted_connections"],
                       "islands": [isl[:12] for isl in v["islands"][:6]]}
                      for n, v in sorted(open_nets.items(), key=lambda kv: -kv[1]["unrouted_connections"])],
        "method": "offline geometric connectivity (pads, tracks, arcs, vias, poured regions, fills, planes); "
                  "confirm with live_pcb_drc (UnRoutedNet)",
    }
