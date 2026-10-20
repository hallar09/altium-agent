# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/overlap.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Placement legality: parts on the same side whose pads (+ clearance) overlap, and parts whose pads leave
the board outline. Uses pad extents (no courtyard data in the PcbDoc reader)."""

from __future__ import annotations

from ..offline.files.pcbdoc import NONE16, PcbDocument


def part_boxes(doc: PcbDocument) -> dict[str, tuple[str, float, float, float, float]]:
    """{designator: (side, x0, y0, x1, y1)} from the pads (copper extent)."""
    pads: dict[int, list] = {}
    for p in doc.pads:
        if p.component != NONE16:
            pads.setdefault(p.component, []).append(p)
    out = {}
    for c in doc.components:
        ps = pads.get(c.index)
        if not ps:
            continue
        x0 = min(p.x - max(p.size_x, p.size_y) / 2 for p in ps); x1 = max(p.x + max(p.size_x, p.size_y) / 2 for p in ps)
        y0 = min(p.y - max(p.size_x, p.size_y) / 2 for p in ps); y1 = max(p.y + max(p.size_x, p.size_y) / 2 for p in ps)
        out[c.designator] = (c.side, x0, y0, x1, y1)
    return out


def _half(p) -> tuple[float, float]:
    import math
    a = math.radians(p.rotation)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return c * p.size_x / 2 + s * p.size_y / 2, s * p.size_x / 2 + c * p.size_y / 2


def pad_conflicts(doc: PcbDocument, clearance: float = 0.1, refs: set[str] | None = None,
                  only: set[str] | None = None) -> list[tuple[str, str, float]]:
    """Exact pad-to-pad check (pad rectangles with their rotation, same copper layer; through-hole pads on
    every layer): pairs of parts whose closest pads are nearer than `clearance`. refs: report pairs involving
    these; only: ignore parts outside this set. Returns (a, b, smallest gap)."""
    comp = {c.index: c.designator for c in doc.components}
    pads = []
    for p in doc.pads:
        r = comp.get(p.component)
        if r is None or (only is not None and r not in only):
            continue
        hx, hy = _half(p)
        layers = {"all"} if p.hole > 0 or p.layer == 74 else {p.layer}
        pads.append((r, p.net, layers, p.x - hx, p.y - hy, p.x + hx, p.y + hy))
    grid: dict = {}
    for i, (_, _, _, x0, y0, x1, y1) in enumerate(pads):
        for gx in range(int(x0 // 2), int(x1 // 2) + 1):
            for gy in range(int(y0 // 2), int(y1 // 2) + 1):
                grid.setdefault((gx, gy), []).append(i)
    worst: dict = {}
    for cell in grid.values():
        for ai in range(len(cell)):
            for bi in range(ai + 1, len(cell)):
                a, b = pads[cell[ai]], pads[cell[bi]]
                if a[0] == b[0] or (refs and a[0] not in refs and b[0] not in refs):
                    continue
                if not ("all" in a[2] or "all" in b[2] or a[2] & b[2]):
                    continue
                if a[1] == b[1] and a[1] != 0xFFFF:
                    continue  # same net may touch
                gap = max(b[3] - a[5], a[3] - b[5], b[4] - a[6], a[4] - b[6])
                key = tuple(sorted((a[0], b[0])))
                if gap < clearance and gap < worst.get(key, 1e9):
                    worst[key] = round(gap, 3)
    return sorted((k[0], k[1], v) for k, v in worst.items())


def overlaps(doc: PcbDocument, clearance: float = 0.15, refs: set[str] | None = None) -> list[tuple[str, str, float]]:
    """Pairs of same-side parts closer than `clearance` (pad extents); through-hole parts count on both sides.
    refs: only report pairs involving these parts."""
    boxes = part_boxes(doc)
    th = {c.designator for c in doc.components
          if any(p.hole > 0 and p.component == c.index for p in doc.pads)}
    names = sorted(boxes)
    bad = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if refs and a not in refs and b not in refs:
                continue
            sa, ax0, ay0, ax1, ay1 = boxes[a]
            sb, bx0, by0, bx1, by1 = boxes[b]
            if sa != sb and a not in th and b not in th:
                continue
            gap = max(bx0 - ax1, ax0 - bx1, by0 - ay1, ay0 - by1)
            if gap < clearance:
                bad.append((a, b, round(gap, 3)))
    return bad
