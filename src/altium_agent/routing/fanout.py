# SPDX-License-Identifier: Apache-2.0
"""Power fan-out: a short via from each power and ground pad to its plane.

Power and ground are carried by pours and planes, not tracks. Once the
signals are routed, a surface-mount pad on such a net still needs a way to
its copper: a pad on a layer the net is poured on is joined by the pour
when it is repoured, and any other pad needs a via, next to it, down (or
up) to the plane, with a stub from the pad to the via. That is what this
places, deterministically and without Altium.

An autorouter asked to do this as a fan-out pass placed far too few
vias, drawing power tracks across the board instead (Altium's WiFi example:
the person used 86 vias, most of them exactly these; the autorouter 18 to 36).

A pad just outside a pour of its own net, on its own layer (a card-edge
finger below the pour's edge), gets a stub into the pour instead.

For each pad, candidate via positions are tried outward from each side
and corner of the pad, nearest first, and the first that keeps the board's
clearance to every other net's copper on every layer (the via goes through
them all), to the board edge and to keep-outs, and lands on the net's plane,
wins. A via of the same net already within reach is shared instead (a via
costs more than a slightly longer stub). A pad that cannot be reached is
reported, never forced.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..placement.shape import distance_to_edge, inside
from .model import dist_point_rect, dist_point_seg, dist_seg_rect, dist_seg_seg

ALL = -1
MULTI = 74
KEEPOUT_LAYER = 56
#: Spatial index cell, mm.
CELL = 1.0
#: Farthest a via is put from its pad's edge, mm.
MAX_REACH_MM = 1.5


#: Share of the board a net's pours must cover, in total, for the net to count
#: as carried by them. Kame's VCC3 has a 0.3 mm2 pour on a 900 mm2 board: a
#: track-routed supply, which counting it as poured left unrouted.
PLANE_SHARE = 0.10


def _area(pts) -> float:
    pts = [tuple(p) for p in pts]
    if len(pts) < 3:
        return 0.0
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def plane_nets(doc: PcbDocument, min_share: float = PLANE_SHARE) -> set[str]:
    """Nets carried by copper area, not tracks: an internal plane, or pours
    covering at least ``min_share`` of the board (all layers together)."""
    out: set[str] = set()
    for s in doc.stack:
        if s.kind == "plane":
            n = doc.board.get(f"PLANE{s.layer_id & 0xFFFF}NETNAME", "").strip()
            if n and n != "(No Net)":
                out.add(n)
    board = _area(doc.outline_poly or []) or None
    poured: dict[str, float] = defaultdict(float)
    for p in doc.polygons or []:
        net = p.get("net") if isinstance(p, dict) else getattr(p, "net", None)
        if isinstance(net, int):
            net = doc.nets[net] if 0 <= net < len(doc.nets) else None
        shelved = p.get("shelved") if isinstance(p, dict) else getattr(p, "shelved", False)
        pts = p.get("outline_mm") if isinstance(p, dict) else getattr(p, "outline_mm", None)
        if net and not shelved:
            poured[net] += _area(pts or []) if board else 1.0
    out |= {n for n, a in poured.items() if (a >= min_share * board if board else a > 0)}
    return out


@dataclass
class _Rect:
    net: str
    layer: int
    x: float
    y: float
    hw: float
    hh: float
    rot: float


@dataclass
class _Seg:
    net: str
    layer: int
    x1: float
    y1: float
    x2: float
    y2: float
    width: float


@dataclass
class _Disc:
    net: str
    x: float
    y: float
    r: float
    layers: frozenset | None = None     # a blind or buried via's copper layers; None: all


def _layers_of(it):
    """The copper layers an obstacle is on: a set, or None for all of them."""
    spans = getattr(it, "layers", None)
    if spans is not None:
        return spans
    lay = getattr(it, "layer", ALL)
    return None if lay == ALL else frozenset((lay,))


def span_layers(order: list[int], lo: int, hi: int) -> frozenset:
    """The layers (legacy ids, in ``order``) a via from ``lo`` to ``hi`` passes."""
    if lo not in order or hi not in order:
        return frozenset(order)
    i, j = sorted((order.index(lo), order.index(hi)))
    return frozenset(order[i:j + 1])


def best_span(order: list[int], pairs, need: set[int]) -> tuple[int, int] | None:
    """The drill pair with the fewest layers that spans every layer in
    ``need`` (legacy ids); None when only a through via does."""
    best = None
    for lo, hi in pairs or ():
        if lo not in order or hi not in order:
            continue
        i, j = sorted((order.index(lo), order.index(hi)))
        if i == 0 and j == len(order) - 1:
            continue                     # that is a through via
        if all(n in order and i <= order.index(n) <= j for n in need):
            if best is None or j - i < best[0]:
                best = (j - i, order[i], order[j])
    return (best[1], best[2]) if best else None


@dataclass
class FanoutResult:
    vias: list[dict] = field(default_factory=list)      # net, x, y, diameter, hole (mm)
    tracks: list[dict] = field(default_factory=list)    # net, layer (legacy id), x1, y1, x2, y2, width
    needed: int = 0
    placed: int = 0
    reused: int = 0
    to_pour: int = 0
    already: int = 0
    on_pour: int = 0
    through_hole: int = 0
    blind: int = 0
    unresolved: list[dict] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    rules: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"nets": self.nets, "pads_needing_a_via": self.needed, "vias_placed": self.placed,
                "vias_shared": self.reused, "stubs_into_a_pour": self.to_pour,
                "joined_by_a_pour_on_their_layer": self.on_pour,
                "through_hole_pads": self.through_hole, "blind_or_buried_placed": self.blind,
                "unresolved": len(self.unresolved),
                "unresolved_pads": self.unresolved[:40], "rules": self.rules}


class _Index:
    """Obstacles by grid cell, so a candidate is checked against its
    neighbourhood only."""

    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], list] = defaultdict(list)

    def add(self, item, x0: float, y0: float, x1: float, y1: float) -> None:
        for i in range(int(math.floor(x0 / CELL)), int(math.floor(x1 / CELL)) + 1):
            for j in range(int(math.floor(y0 / CELL)), int(math.floor(y1 / CELL)) + 1):
                self.cells[(i, j)].append(item)

    def near(self, x0: float, y0: float, x1: float, y1: float):
        seen = set()
        for i in range(int(math.floor(x0 / CELL)), int(math.floor(x1 / CELL)) + 1):
            for j in range(int(math.floor(y0 / CELL)), int(math.floor(y1 / CELL)) + 1):
                for it in self.cells.get((i, j), ()):
                    if id(it) not in seen:
                        seen.add(id(it))
                        yield it


def _bbox(it) -> tuple[float, float, float, float]:
    if isinstance(it, _Rect):
        r = math.hypot(it.hw, it.hh)
        return it.x - r, it.y - r, it.x + r, it.y + r
    if isinstance(it, _Seg):
        h = it.width / 2
        return min(it.x1, it.x2) - h, min(it.y1, it.y2) - h, max(it.x1, it.x2) + h, max(it.y1, it.y2) + h
    return it.x - it.r, it.y - it.r, it.x + it.r, it.y + it.r


def _chords(cx, cy, r, sa, ea, step=10.0):
    sweep = (ea - sa) % 360.0 or 360.0
    n = max(1, int(math.ceil(sweep / step)))
    pts = [(cx + r * math.cos(math.radians(sa + sweep * k / n)), cy + r * math.sin(math.radians(sa + sweep * k / n)))
           for k in range(n + 1)]
    return list(zip(pts, pts[1:]))


def _obstacles(doc: PcbDocument) -> _Index:
    """Every piece of copper (and keep-out) on the board, with its net
    ("" for none: foreign to every net)."""
    idx = _Index()

    def put(it):
        idx.add(it, *_bbox(it))

    def net(n: int) -> str:
        return doc.net_name(n) if n != NONE16 else ""

    for p in doc.pads:
        layer = ALL if (p.hole > 0 or p.layer == MULTI) else p.layer
        put(_Rect(net(p.net), layer, p.x, p.y, max(p.size_x, p.hole) / 2, max(p.size_y, p.hole) / 2, p.rotation))
    for t in doc.tracks:
        if 1 <= t.layer <= 32 and t.polygon == NONE16:
            put(_Seg(net(t.net), t.layer, t.x1, t.y1, t.x2, t.y2, t.width))
        elif t.layer == KEEPOUT_LAYER or t.keepout:
            put(_Seg("", ALL if t.layer == KEEPOUT_LAYER else t.layer, t.x1, t.y1, t.x2, t.y2, t.width))
    for a in doc.arcs:
        if (1 <= a.layer <= 32 and a.polygon == NONE16) or a.layer == KEEPOUT_LAYER:
            layer = ALL if a.layer == KEEPOUT_LAYER else a.layer
            for (x1, y1), (x2, y2) in _chords(a.cx, a.cy, a.radius, a.start_angle, a.end_angle):
                put(_Seg(net(a.net) if a.layer != KEEPOUT_LAYER else "", layer, x1, y1, x2, y2, a.width))
    from ..bench.connectivity import copper_order

    order = copper_order(doc) or [1, 32]
    for v in doc.vias:
        lo, hi = v.layer_start, v.layer_end
        layers = None if (lo, hi) in ((1, 32), (32, 1)) else span_layers(order, lo, hi)
        put(_Disc(net(v.net), v.x, v.y, v.diameter / 2, layers))
    for f in doc.fills:
        if 1 <= f.layer <= 32:
            hw, hh = abs(f.x2 - f.x1) / 2, abs(f.y2 - f.y1) / 2
            put(_Rect(net(f.net), f.layer, (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2, hw, hh, f.rotation))
    for r in doc.regions:
        # Copper regions (a footprint's shaped pads, drawn copper) and
        # keep-outs, as their bounding box: conservative, and rare.
        if len(r.outline) < 3 or r.polygon != NONE16:
            continue
        if r.is_keepout or r.is_board_cutout or (r.kind == 0 and 1 <= r.layer <= 32):
            xs, ys = [p[0] for p in r.outline], [p[1] for p in r.outline]
            layer = ALL if (r.is_keepout or r.is_board_cutout or r.layer == MULTI) else r.layer
            put(_Rect("" if (r.is_keepout or r.is_board_cutout) else net(r.net), layer,
                      (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                      (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2, 0.0))
    return idx


def _gap_disc(d: _Disc, it) -> float:
    if isinstance(it, _Rect):
        return dist_point_rect(d.x, d.y, it.x, it.y, it.hw, it.hh, it.rot) - d.r
    if isinstance(it, _Seg):
        return dist_point_seg(d.x, d.y, it.x1, it.y1, it.x2, it.y2) - d.r - it.width / 2
    return math.hypot(d.x - it.x, d.y - it.y) - d.r - it.r


def _gap_seg(s: _Seg, it) -> float:
    if isinstance(it, _Rect):
        return dist_seg_rect(s.x1, s.y1, s.x2, s.y2, it.x, it.y, it.hw, it.hh, it.rot) - s.width / 2
    if isinstance(it, _Seg):
        return dist_seg_seg(s.x1, s.y1, s.x2, s.y2, it.x1, it.y1, it.x2, it.y2) - (s.width + it.width) / 2
    return dist_point_seg(it.x, it.y, s.x1, s.y1, s.x2, s.y2) - s.width / 2 - it.r


def _clear(idx: _Index, shape, gap, clearance: float, layer) -> bool:
    """No copper of another net within ``clearance`` of ``shape`` (on
    ``layer``; a set of layers for a blind or buried via; on any layer when
    None: a through via)."""
    x0, y0, x1, y1 = _bbox(shape)
    mine = None if layer is None else (frozenset(layer) if isinstance(layer, (set, frozenset)) else
                                       frozenset((layer,)))
    for it in idx.near(x0 - clearance, y0 - clearance, x1 + clearance, y1 + clearance):
        if it.net and it.net == shape.net:
            continue
        theirs = _layers_of(it)
        if mine is not None and theirs is not None and not (mine & theirs):
            continue
        if gap(shape, it) < clearance - 1e-6:
            return False
    return True


def _into_pour(outline: list, x: float, y: float, depth: float) -> tuple[float, float, float] | None:
    """The nearest point at least ``depth`` inside ``outline`` from (x, y),
    and its distance: where a stub from a pad just outside a pour lands."""
    best = None
    n = len(outline)
    for i in range(n):
        (x1, y1), (x2, y2) = outline[i], outline[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 <= 1e-12:
            continue
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
        px, py = x1 + t * dx, y1 + t * dy
        L = math.sqrt(L2)
        for sgn in (1, -1):                     # the inward normal is one of the two
            qx, qy = px - sgn * dy / L * depth, py + sgn * dx / L * depth
            if inside(outline, qx, qy):
                d = math.hypot(qx - x, qy - y)
                if best is None or d < best[2]:
                    best = (qx, qy, d)
                break
    return best


def plan_fanout(doc: PcbDocument, nets: list[str] | None = None, *, via_diameter_mm: float | None = None,
                via_hole_mm: float | None = None, stub_width_mm: float | None = None,
                clearance_mm: float | None = None, max_reach_mm: float = MAX_REACH_MM,
                drill_pairs=None) -> FanoutResult:
    """The vias and stubs that join each power and ground pad to its plane.

    ``nets`` defaults to every net carried by a pour or an internal plane.
    Rules default to the board's (RoutingVias, Width, Clearance, the outline
    clearance), as the DSN export reads them.

    ``drill_pairs`` [(low, high) legacy ids]: the board's blind and buried
    via types. A pad whose plane or pour is on a layer a pair reaches gets
    a via of the shortest such pair, from its own layer to that one, and
    keeps clear of other copper on those layers only: under a BGA the
    layers below stay free for signals (the via sites a SODIMM's DRAMs on
    both sides fight over).
    """
    from ..bench.connectivity import copper_order, legacy_of

    order = copper_order(doc) or [1, 32]
    plane_layer: dict[str, list[int]] = defaultdict(list)
    for s in doc.stack:
        if s.kind == "plane":
            n = doc.board.get(f"PLANE{s.layer_id & 0xFFFF}NETNAME", "").strip()
            lid = legacy_of(s.layer_id)
            if n and n != "(No Net)" and lid is not None:
                plane_layer[n].append(lid)
    from .specctra import _rule_defaults

    rules = _rule_defaults(doc)
    via_d = via_diameter_mm or rules["via_d"]
    via_h = via_hole_mm or rules["via_h"]
    width = stub_width_mm or rules["width"]
    clr = clearance_mm or rules["clearance"]
    edge_clr = rules["outline_clearance"]
    poly = [tuple(p) for p in doc.outline_poly or []]

    # Where each net's copper area is: pours by layer, and whole planes.
    pours: dict[str, list[tuple[int, list]]] = defaultdict(list)
    planes: set[str] = set()
    from ..bench.connectivity import _poly_layer  # the same reading the scorer uses

    for p in doc.polygons or []:
        lid = _poly_layer(p.get("layer", ""))
        if p.get("net") and lid and len(p.get("outline_mm") or []) >= 3 and not p.get("shelved"):
            pours[p["net"]].append((lid, [tuple(q) for q in p["outline_mm"]]))
    for s in doc.stack:
        if s.kind == "plane":
            n = doc.board.get(f"PLANE{s.layer_id & 0xFFFF}NETNAME", "").strip()
            if n and n != "(No Net)":
                planes.add(n)
    if nets is None:
        nets = sorted(plane_nets(doc))
    want = set(nets)

    res = FanoutResult(nets=sorted(want), rules={"via_diameter_mm": via_d, "via_hole_mm": via_h,
                                                 "stub_width_mm": width, "clearance_mm": clr,
                                                 "outline_clearance_mm": edge_clr})
    idx = _obstacles(doc)
    same_vias: dict[str, list[_Disc]] = defaultdict(list)
    for v in doc.vias:
        if v.net != NONE16:
            through = (v.layer_start, v.layer_end) in ((1, 32), (32, 1))
            same_vias[doc.net_name(v.net)].append(_Disc(doc.net_name(v.net), v.x, v.y, v.diameter / 2,
                                                        None if through else
                                                        span_layers(order, v.layer_start, v.layer_end)))
    refs = {c.index: c.designator for c in doc.components}

    def lands(net: str, x: float, y: float, pad_layer: int) -> bool:
        """A via here reaches ``net``'s copper on a layer other than the pad's."""
        if net in planes:
            return True
        return any(lid != pad_layer and inside(outline, x, y) for lid, outline in pours.get(net, ()))

    def on_board(x: float, y: float, r: float) -> bool:
        if len(poly) < 3:
            return True
        return inside(poly, x, y) and distance_to_edge(poly, x, y) >= edge_clr + r

    def span_for(net: str, x: float, y: float, pad_layer: int):
        """(layer_start, layer_end, layers) of the shortest drill pair from
        the pad's layer to a layer carrying ``net`` here; None: through."""
        if not drill_pairs:
            return None
        best = None
        targets = list(plane_layer.get(net, ())) + [lid for lid, outline in pours.get(net, ())
                                                     if lid != pad_layer and inside(outline, x, y)]
        for t in targets:
            s = best_span(order, drill_pairs, {pad_layer, t})
            if s is not None:
                layers = span_layers(order, *s)
                if best is None or len(layers) < len(best[2]):
                    best = (s[0], s[1], layers)
        return best

    r_via = via_d / 2
    pads = sorted((p for p in doc.pads if p.net != NONE16 and doc.net_name(p.net) in want),
                  key=lambda p: (doc.net_name(p.net), refs.get(p.component, ""), p.name))
    for p in pads:
        net = doc.net_name(p.net)
        if p.hole > 0 or p.layer == MULTI:
            res.through_hole += 1
            continue
        if any(lid == p.layer and inside(outline, p.x, p.y) for lid, outline in pours.get(net, ())):
            res.on_pour += 1
            continue
        hw, hh = p.size_x / 2, p.size_y / 2
        pad_rect = _Rect(net, p.layer, p.x, p.y, hw, hh, p.rotation)
        if any(it.net == net and not isinstance(it, _Rect) and getattr(it, "layer", ALL) in (ALL, p.layer)
               and (_gap_seg(it, pad_rect) if isinstance(it, _Seg) else _gap_disc(it, pad_rect)) <= 1e-6
               for it in idx.near(p.x - hw - 1, p.y - hh - 1, p.x + hw + 1, p.y + hh + 1)):
            res.already += 1          # a track or via of its net already reaches it (a second run, hand routing)
            continue
        res.needed += 1
        rot = math.radians(p.rotation)
        c, s = math.cos(rot), math.sin(rot)
        best = None
        # Just outside a pour of its own net on its own layer (an edge finger,
        # a pad by a pour's edge): a stub into the pour, no via.
        for lid, outline in pours.get(net, ()):
            if lid != p.layer:
                continue
            q = _into_pour(outline, p.x, p.y, width / 2 + clr)
            if q is None or q[2] - max(hw, hh) > max_reach_mm:
                continue
            stub = _Seg(net, p.layer, p.x, p.y, q[0], q[1], width)
            if on_board(q[0], q[1], width / 2) and _clear(idx, stub, _gap_seg, clr, p.layer):
                if best is None or q[2] < best[0]:
                    best = (q[2], None, stub, "pour")
        # A same-net via already within reach: a stub to it is enough.
        for v in same_vias.get(net, ()):
            if v.layers is not None and (p.layer not in v.layers or not (
                    set(plane_layer.get(net, ())) & v.layers
                    or any(lid in v.layers and lid != p.layer and inside(o, v.x, v.y)
                           for lid, o in pours.get(net, ())))):
                continue                 # a blind via that does not join this pad to its copper
            d = math.hypot(v.x - p.x, v.y - p.y)
            if d - r_via - max(hw, hh) <= max_reach_mm:
                stub = _Seg(net, p.layer, p.x, p.y, v.x, v.y, width)
                if lands(net, v.x, v.y, p.layer) and _clear(idx, stub, _gap_seg, clr, p.layer):
                    if best is None or d < best[0]:
                        best = (d, v, stub, True)
        # New vias: out from each side and corner, nearest first, with small sideways shifts.
        cands = []
        for ux, uy, extent in ((1, 0, hw), (-1, 0, hw), (0, 1, hh), (0, -1, hh),
                               (0.7071, 0.7071, math.hypot(hw, hh)), (-0.7071, 0.7071, math.hypot(hw, hh)),
                               (0.7071, -0.7071, math.hypot(hw, hh)), (-0.7071, -0.7071, math.hypot(hw, hh))):
            for step in (0.0, 0.15, 0.3, 0.5, 0.75, 1.0, 1.25, 1.5):
                if step > max_reach_mm:
                    continue
                for side in (0.0, 0.25, -0.25, 0.5, -0.5):
                    dist = extent + r_via + 0.05 + step
                    lx, ly = ux * dist - uy * side, uy * dist + ux * side
                    cands.append((math.hypot(lx, ly), p.x + lx * c - ly * s, p.y + lx * s + ly * c))
        cands.sort()
        # A stub into the pour, then a shared via, before a new one: a via
        # costs more than a slightly longer stub.
        for d, x, y in ([] if best is not None else cands):
            if not (on_board(x, y, r_via) and lands(net, x, y, p.layer)):
                continue
            span = span_for(net, x, y, p.layer)
            via = _Disc(net, x, y, r_via, span[2] if span else None)
            if not _clear(idx, via, _gap_disc, clr, span[2] if span else None):
                continue
            stub = _Seg(net, p.layer, p.x, p.y, x, y, width)
            if not _clear(idx, stub, _gap_seg, clr, p.layer):
                continue
            best = (d, via, stub, False)
            break
        if best is None:
            res.unresolved.append({"ref": refs.get(p.component, ""), "pad": p.name, "net": net,
                                   "reason": "no clear spot for a via within reach"
                                   if (net in planes or any(lid != p.layer for lid, _ in pours.get(net, ())))
                                   else "the net has no plane or pour on another layer to reach"})
            continue
        _, via, stub, shared = best
        res.tracks.append({"net": net, "layer": p.layer, "x1": stub.x1, "y1": stub.y1, "x2": stub.x2,
                           "y2": stub.y2, "width": width})
        idx.add(stub, *_bbox(stub))
        if shared == "pour":
            res.to_pour += 1
        elif shared:
            res.reused += 1
        else:
            row = {"net": net, "x": via.x, "y": via.y, "diameter": via_d, "hole": via_h}
            if via.layers is not None:
                lo, hi = sorted((min(via.layers, key=order.index), max(via.layers, key=order.index)),
                                key=order.index)
                row.update(layer_start=lo, layer_end=hi)
                res.blind += 1
            res.vias.append(row)
            idx.add(via, *_bbox(via))
            same_vias[net].append(via)
            res.placed += 1
    return res


def with_result(doc: PcbDocument, res: FanoutResult) -> PcbDocument:
    """``doc`` with a fan-out's vias and stubs on it (the board read is not edited)."""
    import copy

    from ..offline.files.pcbdoc import Track, Via

    out = copy.copy(doc)
    index = {n: i for i, n in enumerate(doc.nets)}
    out.tracks = list(doc.tracks) + [Track(t["layer"], index[t["net"]], NONE16, t["x1"], t["y1"], t["x2"], t["y2"],
                                           t["width"]) for t in res.tracks if t["net"] in index]
    # A via may carry its span (legacy ids, "layer_start"/"layer_end"): a
    # blind or buried one. Without them it is a through via, as before.
    out.vias = list(doc.vias) + [Via(index[v["net"]], v["x"], v["y"], v["diameter"], v["hole"],
                                     int(v.get("layer_start", 1)), int(v.get("layer_end", 32)))
                                 for v in res.vias if v["net"] in index]
    return out


def plan_stitching(doc: PcbDocument, nets: list[str] | None = None, *, spacing_mm: float = 5.0,
                   via_diameter_mm: float | None = None, via_hole_mm: float | None = None,
                   clearance_mm: float | None = None) -> FanoutResult:
    """Stitching vias that join a net's pours and planes on different layers.

    A pour on the top and one on the bottom are separate copper until vias
    join them. On a grid of ``spacing_mm``, a via goes wherever at least two
    of the net's pours (or a pour and its plane) overlap and it keeps the
    board's clearance to every other net on every layer. Run it after the
    signals: on a grid, stitching would take the room they need.
    """
    from .specctra import _rule_defaults

    rules = _rule_defaults(doc)
    via_d = via_diameter_mm or rules["via_d"]
    via_h = via_hole_mm or rules["via_h"]
    clr = clearance_mm or rules["clearance"]
    poly = [tuple(p) for p in doc.outline_poly or []]
    edge_clr = rules["outline_clearance"]
    from ..bench.connectivity import _poly_layer

    planes = plane_nets(doc)
    want = set(nets) if nets is not None else planes
    layers: dict[str, list[tuple[int, list]]] = defaultdict(list)
    for p in doc.polygons or []:
        lid = _poly_layer(p.get("layer", ""))
        if p.get("net") in want and lid and len(p.get("outline_mm") or []) >= 3 and not p.get("shelved"):
            layers[p["net"]].append((lid, [tuple(q) for q in p["outline_mm"]]))
    internal = {doc.board.get(f"PLANE{s.layer_id & 0xFFFF}NETNAME", "").strip()
                for s in doc.stack if s.kind == "plane"}
    res = FanoutResult(nets=sorted(want), rules={"via_diameter_mm": via_d, "via_hole_mm": via_h,
                                                 "clearance_mm": clr, "spacing_mm": spacing_mm})
    if len(poly) < 3:
        return res
    idx = _obstacles(doc)
    xs, ys = [q[0] for q in poly], [q[1] for q in poly]
    r = via_d / 2
    for net in sorted(want):
        n_internal = 1 if net in internal else 0
        y = min(ys) + spacing_mm / 2
        while y < max(ys):
            x = min(xs) + spacing_mm / 2
            while x < max(xs):
                cover = len({lid for lid, outline in layers.get(net, ()) if inside(outline, x, y)}) + n_internal
                if (cover >= 2 and inside(poly, x, y) and distance_to_edge(poly, x, y) >= edge_clr + r):
                    via = _Disc(net, x, y, r)
                    if _clear(idx, via, _gap_disc, clr, None):
                        res.vias.append({"net": net, "x": x, "y": y, "diameter": via_d, "hole": via_h})
                        idx.add(via, *_bbox(via))
                        res.placed += 1
                x += spacing_mm
            y += spacing_mm
    return res


def write_ses(res: FanoutResult, path: Path, layer_map: dict[str, int], board: str = "board") -> Path:
    """The fan-out as a Specctra session, so route_import_ses, after_jobs
    and the benchmark take it exactly as they take a routing session's."""
    name_of = {lid: n for n, lid in layer_map.items()}
    per = 10000.0                                  # resolution um 10: units per mm

    def u(v: float) -> str:
        return f"{v * per:.0f}"

    by_net: dict[str, list[str]] = defaultdict(list)
    for t in res.tracks:
        by_net[t["net"]].append(f"(wire (path {name_of[t['layer']]} {u(t['width'])} {u(t['x1'])} {u(t['y1'])} "
                                f"{u(t['x2'])} {u(t['y2'])}))")
    pad = f"FANOUT_{res.rules['via_diameter_mm'] * 1000:.0f}_{res.rules['via_hole_mm'] * 1000:.0f}"
    first = next(iter(layer_map), "Top")
    stacks = [f'(padstack "{pad}" (shape (circle {first} {u(res.rules["via_diameter_mm"])})))']
    # A blind or buried via gets a padstack of its own with a shape on each
    # layer it spans and nowhere else, the Specctra way of saying "this via
    # starts and stops here"; parse_ses reads the span back off the shapes.
    order = list(layer_map)                         # stack order, top first
    spanned: dict[tuple, str] = {}
    for v in res.vias:
        name = pad
        lo, hi = v.get("layer_start"), v.get("layer_end")
        if lo is not None and hi is not None and (lo, hi) not in ((1, 32), (32, 1)) \
                and lo in name_of and hi in name_of:
            i, j = sorted((order.index(name_of[lo]), order.index(name_of[hi])))
            key = (round(v["diameter"], 4), round(v["hole"], 4), i, j)
            if key not in spanned:
                spanned[key] = (f"VIA_{v['diameter'] * 1000:.0f}_{v['hole'] * 1000:.0f}_"
                                f"{order[i]}-{order[j]}")
                shapes = " ".join(f"(shape (circle {order[k]} {u(v['diameter'])}))" for k in range(i, j + 1))
                stacks.append(f'(padstack "{spanned[key]}" {shapes})')
            name = spanned[key]
        by_net[v["net"]].append(f'(via "{name}" {u(v["x"])} {u(v["y"])})')
    lines = [f'(session "{board}"', "  (routes", "    (resolution um 10)",
             f'    (library_out {" ".join(stacks)})',
             "    (network_out"]
    lines += [f'      (net "{n}" {" ".join(items)})' for n, items in sorted(by_net.items())]
    lines += ["    )", "  )", ")"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
