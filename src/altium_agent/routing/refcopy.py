# SPDX-License-Identifier: Apache-2.0
"""Copy routing from a reference board (Altium Agent; BoardKit's ``refcopy``).

When a re-layout keeps the reference's part positions, the reference's own
copper is the best route there is for a net the autorouter cannot do, and
for a big part's escape. This copies, from the reference's saved file:

1. every free track, arc and via of the nets named, and
2. the escape copper of a part: free copper of that part's nets lying wholly
   inside its pads' box grown by a margin.

It refuses unless every part the copied nets touch sits on the target board
exactly where it sits on the reference (same absolute position, rotation and
side, within 0.01 mm), because copper copied onto moved parts is wrong.
Copper already on the target is not written twice. Arcs are written as short
straight segments (the bridge's arc command takes whole mils and no net).

The idea and the rules (same absolute positions, the escape window, lock
what is copied, then converge by ripping clashes and copying those nets) are
BoardKit's (tools/altium/CopyRefCopper.pas); this is a new implementation on
the offline reader and the bulk track/via commands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise

from ..offline.files.pcbdoc import COPPER_LEGACY, NONE16, PcbDocument
from ..units import MILS_PER_MM, MM_PER_MIL
from .ses_import import layer_token

POS_TOL_MM = 0.01
ARC_STEP_DEG = 5.0


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


@dataclass
class RefCopy:
    tracks: list[str] = field(default_factory=list)
    vias: list[str] = field(default_factory=list)
    nets: set = field(default_factory=set)
    from_arcs: int = 0
    skipped_existing: int = 0
    moved_parts: list[dict] = field(default_factory=list)
    unknown_nets: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"tracks": len(self.tracks), "vias": len(self.vias), "nets": sorted(self.nets),
                "segments_from_arcs": self.from_arcs, "skipped_existing": self.skipped_existing,
                "moved_parts": self.moved_parts, "unknown_nets": self.unknown_nets}


def _abs(doc: PcbDocument, x: float, y: float) -> tuple[float, float]:
    return x + doc.origin_mil[0] * MM_PER_MIL, y + doc.origin_mil[1] * MM_PER_MIL


def _parts_on_nets(doc: PcbDocument, nets: set[str]) -> set[str]:
    out = set()
    for p in doc.pads:
        if p.component != NONE16 and p.net != NONE16 and doc.net_name(p.net) in nets:
            out.add(doc.designator(p.component))
    return out


def moved_parts(ref: PcbDocument, target: PcbDocument, refs: set[str]) -> list[dict]:
    """Parts in ``refs`` that are not where the reference has them (absolute, rotation, side)."""
    a = {c.designator: c for c in ref.components}
    b = {c.designator: c for c in target.components}
    out = []
    for r in sorted(refs):
        ca, cb = a.get(r), b.get(r)
        if ca is None or cb is None:
            out.append({"ref": r, "why": "not on both boards"})
            continue
        pa, pb = _abs(ref, ca.x_mm, ca.y_mm), _abs(target, cb.x_mm, cb.y_mm)
        if (math.dist(pa, pb) > POS_TOL_MM or abs((ca.rotation - cb.rotation + 180) % 360 - 180) > 0.01
                or ca.side != cb.side):
            out.append({"ref": r, "reference_mm": [round(v, 3) for v in pa], "target_mm": [round(v, 3) for v in pb],
                        "rotation": [ca.rotation, cb.rotation], "side": [ca.side, cb.side]})
    return out


def _escape_window(ref: PcbDocument, comp: str, margin_mm: float) -> tuple[set[str], tuple]:
    pads = [p for p in ref.pads if p.component != NONE16 and ref.designator(p.component) == comp]
    if not pads:
        raise ValueError(f"{comp} is not on the reference board")
    xs = [p.x - p.size_x / 2 for p in pads] + [p.x + p.size_x / 2 for p in pads]
    ys = [p.y - p.size_y / 2 for p in pads] + [p.y + p.size_y / 2 for p in pads]
    nets = {ref.net_name(p.net) for p in pads if p.net != NONE16}
    return nets, (min(xs) - margin_mm, min(ys) - margin_mm, max(xs) + margin_mm, max(ys) + margin_mm)


def plan_copy(ref: PcbDocument, target: PcbDocument, nets: list[str] | None = None,
              escape: str | None = None, margin_mm: float = 0.5) -> RefCopy:
    """Bridge rows (absolute mils) for the reference copper to copy, with the checks."""
    out = RefCopy()
    want = set(nets or [])
    known = set(ref.nets)
    out.unknown_nets = sorted(n for n in want if n not in known)
    want -= set(out.unknown_nets)
    win = None
    esc_nets: set[str] = set()
    if escape:
        esc_nets, win = _escape_window(ref, escape, margin_mm)
    out.moved_parts = moved_parts(ref, target, _parts_on_nets(ref, want | esc_nets) | ({escape} if escape else set()))
    if out.moved_parts:
        return out

    def inside(*pts) -> bool:
        return win is not None and all(win[0] <= x <= win[2] and win[1] <= y <= win[3] for x, y in pts)

    def take(net_i: int, *pts) -> str | None:
        if net_i == NONE16:
            return None
        name = ref.net_name(net_i)
        if name in want or (name in esc_nets and inside(*pts)):
            return name
        return None

    def key(x: float, y: float) -> tuple[int, int]:
        return round(x * 100), round(y * 100)

    have = set()
    for t in target.tracks:
        a, b = _abs(target, t.x1, t.y1), _abs(target, t.x2, t.y2)
        have.add((t.layer,) + key(*a) + key(*b))
        have.add((t.layer,) + key(*b) + key(*a))
    have_v = {key(*_abs(target, v.x, v.y)) for v in target.vias}

    def add_track(x1, y1, x2, y2, w, layer, net) -> None:
        a, b = _abs(ref, x1, y1), _abs(ref, x2, y2)
        if (layer,) + key(*a) + key(*b) in have:
            out.skipped_existing += 1
            return
        out.tracks.append(",".join([_num(a[0] * MILS_PER_MM), _num(a[1] * MILS_PER_MM), _num(b[0] * MILS_PER_MM),
                                    _num(b[1] * MILS_PER_MM), _num(w * MILS_PER_MM), layer_token(layer), net]))
        out.nets.add(net)

    for t in ref.tracks:
        if t.component != NONE16 or t.polygon != NONE16 or t.layer not in COPPER_LEGACY or t.layer > 32:
            continue
        net = take(t.net, (t.x1, t.y1), (t.x2, t.y2))
        if net:
            add_track(t.x1, t.y1, t.x2, t.y2, t.width, t.layer, net)
    for a in ref.arcs:
        if a.component != NONE16 or a.polygon != NONE16 or a.layer not in COPPER_LEGACY or a.layer > 32:
            continue
        sweep = (a.end_angle - a.start_angle) % 360 or 360
        n = max(1, math.ceil(sweep / ARC_STEP_DEG))
        pts = [(a.cx + a.radius * math.cos(math.radians(a.start_angle + sweep * i / n)),
                a.cy + a.radius * math.sin(math.radians(a.start_angle + sweep * i / n))) for i in range(n + 1)]
        net = take(a.net, *pts)
        if net:
            for (x1, y1), (x2, y2) in pairwise(pts):
                add_track(x1, y1, x2, y2, a.width, a.layer, net)
                out.from_arcs += 1
    for v in ref.vias:
        if v.component != NONE16:
            continue
        net = take(v.net, (v.x, v.y))
        if not net:
            continue
        x, y = _abs(ref, v.x, v.y)
        if key(x, y) in have_v:
            out.skipped_existing += 1
            continue
        out.vias.append(",".join([_num(x * MILS_PER_MM), _num(y * MILS_PER_MM), _num(v.diameter * MILS_PER_MM),
                                  _num(v.hole * MILS_PER_MM), layer_token(v.layer_start if v.layer_start <= 32 else 1),
                                  layer_token(min(v.layer_end, 32)), net]))
        out.nets.add(net)
    if any(any(ch in n for ch in ",|") for n in out.nets):
        raise ValueError("a net name with ',' or '|' cannot be sent to the bridge")
    return out
