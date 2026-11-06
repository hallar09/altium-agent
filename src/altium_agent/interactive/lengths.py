# SPDX-License-Identifier: Apache-2.0
"""Net lengths along the copper, the way a DDR or a pair budget counts them.

The SODIMM routing agents wrote this themselves about twenty-five times
(``lengths.py``, ``measure.py``, ``len.py``...): the length of a net from
one pad to another along its tracks, a via counted as the stack depth it
crosses, an inner layer scaled to outer-equivalent, and each net of a byte
lane against the lane's strobe. Here it is one call on the session.

Copper is a graph per net: track ends and pads are nodes, a track is an
edge of its (scaled) length, a via joins the layers it spans at the cost
of the depth between them, and a track end that lands on another track,
a pad or a via joins it there.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from typing import Any

from ..offline.errors import InvalidArgument
from ..offline.files.pcbdoc import NONE16, PcbDocument

SNAP = 0.01  # mm: track ends closer than this are one point


def _key(layer: int, x: float, y: float) -> tuple[int, int, int]:
    return layer, round(x / SNAP), round(y / SNAP)


def _on_segment(px, py, t, tol) -> float | None:
    """Distance along ``t`` from its start to the foot of (px, py), if the point lies on it."""
    dx, dy = t.x2 - t.x1, t.y2 - t.y1
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return None
    u = ((px - t.x1) * dx + (py - t.y1) * dy) / L2
    if u <= 1e-6 or u >= 1 - 1e-6:
        return None
    fx, fy = t.x1 + u * dx, t.y1 + u * dy
    if math.hypot(px - fx, py - fy) > tol:
        return None
    return u * math.sqrt(L2)


def _pad_hits(p, x: float, y: float) -> bool:
    a = math.radians(-p.rotation)
    dx, dy = x - p.x, y - p.y
    lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
    return abs(lx) <= p.size_x / 2 + 1e-6 and abs(ly) <= p.size_y / 2 + 1e-6


class NetGraph:
    """One net's copper as a weighted graph."""

    def __init__(self, doc: PcbDocument, net: str, layer_scale: dict[int, float], via_length: bool = True):
        self.doc, self.net = doc, net
        from ..bench.connectivity import copper_order

        idx = doc.nets.index(net)
        order = [lid for lid in copper_order(doc) if lid <= 32] or [1, 32]
        self.tracks = [t for t in doc.tracks if t.net == idx and 1 <= t.layer <= 32 and t.polygon == NONE16]
        self.vias = [v for v in doc.vias if v.net == idx]
        self.pads = [p for p in doc.pads if p.net == idx]
        self.edges: dict[Any, list[tuple[Any, float]]] = defaultdict(list)
        self.by_layer = defaultdict(float)
        self.via_mm = 0.0
        scale = layer_scale

        def link(a, b, w):
            self.edges[a].append((b, w))
            self.edges[b].append((a, w))

        # tracks, split where another end lands on them (a fly-by stub's tee)
        ends = [(t.layer, t.x1, t.y1) for t in self.tracks] + [(t.layer, t.x2, t.y2) for t in self.tracks]
        layers_of_via = []
        for v in self.vias:
            lo, hi = sorted((v.layer_start, v.layer_end))
            span = [lid for lid in order if lo <= lid <= hi]
            layers_of_via.append(span)
            ends += [(lid, v.x, v.y) for lid in span]
        for t in self.tracks:
            k = scale.get(t.layer, 1.0)
            cuts = [(0.0, _key(t.layer, t.x1, t.y1)), (t.length, _key(t.layer, t.x2, t.y2))]
            for lid, x, y in ends:
                if lid != t.layer:
                    continue
                d = _on_segment(x, y, t, max(SNAP, t.width / 2))
                if d is not None:
                    cuts.append((d, _key(lid, x, y)))
            cuts.sort()
            for (d0, a), (d1, b) in zip(cuts, cuts[1:]):
                if a != b:
                    link(a, b, (d1 - d0) * k)
            self.by_layer[t.layer] += t.length * k

        # vias: their layers joined at the depth between them
        for v, span in zip(self.vias, layers_of_via):
            for la, lb in zip(span, span[1:]):
                da, db = doc.layer_depth_mm(la), doc.layer_depth_mm(lb)
                w = abs(db - da) if (via_length and da is not None and db is not None) else 0.0
                link(_key(la, v.x, v.y), _key(lb, v.x, v.y), w)
            # a track end inside the via's pad joins it
            for t in self.tracks:
                if t.layer in span:
                    for x, y in ((t.x1, t.y1), (t.x2, t.y2)):
                        if math.hypot(x - v.x, y - v.y) <= v.diameter / 2 + 1e-6:
                            link(_key(t.layer, x, y), _key(t.layer, v.x, v.y), 0.0)
            if span and via_length:
                d0, d1 = doc.layer_depth_mm(span[0]), doc.layer_depth_mm(span[-1])
                self.via_mm += abs((d1 or 0) - (d0 or 0))
        # pads: a node per pad, joined to every track end or via inside it
        self.pad_nodes: dict[str, list[str]] = defaultdict(list)
        for p in self.pads:
            ref = doc.designator(p.component) if p.component != NONE16 else ""
            node = ("pad", ref, p.name, p.x, p.y)
            self.pad_nodes[ref.upper()].append(node)
            layers = None if (p.hole > 0 or p.layer == 74) else {p.layer}
            for t in self.tracks:
                if layers is None or t.layer in layers:
                    for x, y in ((t.x1, t.y1), (t.x2, t.y2)):
                        if _pad_hits(p, x, y):
                            link(node, _key(t.layer, x, y), 0.0)
            for v, span in zip(self.vias, layers_of_via):
                if (layers is None or layers & set(span)) and _pad_hits(p, v.x, v.y):
                    link(node, _key(span[0], v.x, v.y), 0.0)

    def total(self) -> float:
        return sum(self.by_layer.values()) + self.via_mm

    def between(self, ref_a: str, ref_b: str) -> float | None:
        """Shortest copper length from any of ``ref_a``'s pads on the net to any of ``ref_b``'s."""
        src = self.pad_nodes.get(ref_a.upper()) or []
        dst = set(self.pad_nodes.get(ref_b.upper()) or [])
        if not src or not dst:
            return None
        dist = {s: 0.0 for s in src}
        q = [(0.0, i, s) for i, s in enumerate(src)]
        heapq.heapify(q)
        n = len(q)
        while q:
            d, _, u = heapq.heappop(q)
            if d > dist.get(u, math.inf):
                continue
            if u in dst:
                return d
            for v, w in self.edges.get(u, ()):
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    n += 1
                    heapq.heappush(q, (nd, n, v))
        return None


def _scale_ids(doc: PcbDocument, layer_scale: dict[str, float] | None) -> dict[int, float]:
    from .board_model import legacy_id

    out: dict[int, float] = {}
    for name, k in (layer_scale or {}).items():
        lid = legacy_id(str(name))
        if lid is None:
            raise InvalidArgument(f"layer_scale: {name!r} is not a copper layer name (TopLayer, MidLayerN, "
                                  "BottomLayer)")
        out[lid] = float(k)
    return out


def measure(doc: PcbDocument, nets: list[str], *, from_ref: str | None = None, to_ref: str | None = None,
            layer_scale: dict[str, float] | None = None, via_length: bool = True,
            groups: list[dict] | None = None, pairs: list[list[str]] | None = None) -> dict[str, Any]:
    """Lengths of ``nets`` (and of every net named in ``groups``/``pairs``).

    Each net: its copper in total (``total_mm``, with vias), per layer, and,
    with ``from_ref``/``to_ref``, pad to pad along the copper
    (``path_mm``; None while the copper does not join them). A group
    ``{name, nets, target?, tolerance_mm?}`` compares each member with its
    target (a net name, "max" or "mean"; default "max") by path length
    when the refs are given, else total. A pair ``[P, N]`` gives its skew.
    """
    from .board_model import layer_token

    want = list(dict.fromkeys(list(nets or []) + [n for g in groups or [] for n in g.get("nets", [])]
                              + [n for p in pairs or [] for n in p]))
    if not want:
        raise InvalidArgument("name the nets to measure (nets, groups or pairs)")
    bad = [n for n in want if n not in doc.nets]
    if bad:
        raise InvalidArgument(f"not nets of this board: {', '.join(bad[:10])}")
    if bool(from_ref) != bool(to_ref):
        raise InvalidArgument("give both from_ref and to_ref (designators), or neither")
    scale = _scale_ids(doc, layer_scale)
    rows: dict[str, dict] = {}
    for n in want:
        g = NetGraph(doc, n, scale, via_length)
        row: dict[str, Any] = {"total_mm": round(g.total(), 3), "vias": len(g.vias),
                               "via_mm": round(g.via_mm, 3),
                               "by_layer_mm": {layer_token(k): round(v, 3) for k, v in sorted(g.by_layer.items())}}
        if from_ref:
            d = g.between(from_ref, to_ref)
            row["path_mm"] = round(d, 3) if d is not None else None
            if d is None:
                row["note"] = (f"the copper does not join {from_ref} to {to_ref} yet" if g.tracks
                               else "not routed")
        rows[n] = row

    def value(n):
        r = rows[n]
        return r.get("path_mm") if from_ref else r["total_mm"]

    out: dict[str, Any] = {"nets": rows, "measured": "pad to pad" if from_ref else "all copper of the net",
                           "via_counted_as": "the stack depth it crosses" if via_length else "nothing",
                           "layer_scale": layer_scale or {}}
    if groups:
        greps = []
        for g in groups:
            members = [n for n in g.get("nets", []) if n in rows]
            vals = {n: value(n) for n in members}
            known = [v for v in vals.values() if v is not None]
            tgt = g.get("target", "max")
            if tgt in rows:
                ref = value(tgt)
            elif tgt == "mean":
                ref = sum(known) / len(known) if known else None
            else:
                ref = max(known) if known else None
            tol = g.get("tolerance_mm")
            deltas = {n: (round(v - ref, 3) if v is not None and ref is not None else None) for n, v in vals.items()}
            outside = sorted((n for n, d in deltas.items() if d is not None and tol is not None and abs(d) > tol),
                             key=lambda n: -abs(deltas[n]))
            greps.append({"name": g.get("name", "group"), "target_mm": round(ref, 3) if ref is not None else None,
                          "target": tgt, "tolerance_mm": tol, "delta_mm": deltas,
                          "spread_mm": round(max(known) - min(known), 3) if known else None,
                          "outside_tolerance": outside,
                          "not_measured": sorted(n for n, v in vals.items() if v is None)})
        out["groups"] = greps
    if pairs:
        out["pairs"] = [{"positive": p, "negative": q,
                         "skew_mm": (round(value(p) - value(q), 3)
                                     if value(p) is not None and value(q) is not None else None)}
                        for p, q in pairs]
    return out
