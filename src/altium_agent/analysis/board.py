# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/board.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Board facts every layout tool needs, derived from the board itself (no design-specific names):

- supply_nets(doc): (rails, ground). Ground: GND-like names. Rails: power-like names (VCC / VDD / VBAT / VIN /
  VBUS / V+ / +3V3 ...) with at least `rail_pins` pins - a 2-pin switch node called VCC3_SW is a signal.
- routing_layers(doc): legacy ids of the copper layers the board's Routing Layers rule enables (falls back to
  every copper layer that is not an internal plane).
- layer_name(id): the bridge's name for a legacy copper layer id (Top, Mid1 .. Mid30, Bottom)."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from ..offline.files.pcbdoc import NONE16, PcbDocument

GROUND_RE = re.compile(r"^(A|D|P|S_A|S_D|CHASSIS_|SHIELD_)?(GND|VSS|GROUND)[A-Z0-9_]*$|.*_GND$|^0V$", re.I)
POWER_RE = re.compile(r"^(\+?\d+V\d*|\+[A-Z0-9_]+|(.*_)?(VCC|VDD|VBAT|VIN|VBUS|VREF|VCORE|VIO|AVCC|DVCC|PVCC|V3V3|V1V8|"
                      r"V5V|V12V|VSYS|VMOT|VUSB|VPP|VEE)[A-Z0-9_]*)$", re.I)
_cache: dict[int, tuple] = {}


def supply_nets(doc: PcbDocument, rail_pins: int = 4) -> tuple[set[str], set[str]]:
    key = id(doc)
    if key in _cache:
        return _cache[key]
    count = defaultdict(int)
    for p in doc.pads:
        if p.net != NONE16:
            count[doc.net_name(p.net)] += 1
    ground = {n for n in count if GROUND_RE.match(n)}
    rails = {n for n, k in count.items() if n not in ground and k >= rail_pins and POWER_RE.match(n)}
    _cache[key] = (rails, ground)
    return rails, ground


def power_nets(doc: PcbDocument) -> set[str]:
    rails, ground = supply_nets(doc)
    return rails | ground


def copper_ids(doc: PcbDocument) -> list[int]:
    """Legacy ids of the copper layers in stack order: Top = 1, mid layers 2.., Bottom = 32."""
    out = []
    for s in doc.stack:
        if s.kind == "copper":  # V9 layer id: 0x01000000 + legacy id, bottom = 0x0100FFFF
            out.append(32 if s.layer_id == 0x0100FFFF else s.layer_id - 0x01000000)
        elif s.kind == "plane":  # internal plane n = legacy 38 + n
            out.append(38 + (s.layer_id & 0xFFFF))
    return out


def inset_polygon(poly, d):
    """Offset a simple polygon inward by d (edges shifted along their inner normal, corners re-intersected)."""
    n = len(poly)
    area = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
    s = 1 if area > 0 else -1  # counter-clockwise: inner normal is to the left
    lines = []
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 1e-9:
            continue
        nx, ny = -s * (y2 - y1) / L, s * (x2 - x1) / L
        lines.append(((x1 + nx * d, y1 + ny * d), (x2 + nx * d, y2 + ny * d)))
    out = []
    for i in range(len(lines)):
        (a1, a2), (b1, b2) = lines[i - 1], lines[i]
        dax, day = a2[0] - a1[0], a2[1] - a1[1]
        dbx, dby = b2[0] - b1[0], b2[1] - b1[1]
        den = dax * dby - day * dbx
        if abs(den) < 1e-12:
            out.append(b1)
            continue
        t = ((b1[0] - a1[0]) * dby - (b1[1] - a1[1]) * dbx) / den
        out.append((a1[0] + t * dax, a1[1] + t * day))
    return out


def layer_name(lid: int) -> str:
    if 39 <= lid <= 54:
        return f"Plane{lid - 38}"
    return "Top" if lid == 1 else "Bottom" if lid == 32 else f"Mid{lid - 1}"


def routing_layers(doc: PcbDocument) -> list[int]:
    ids = copper_ids(doc)
    kinds = {lid: s.kind for lid, s in zip(ids, [s for s in doc.stack if s.kind in ("copper", "plane")])}
    enabled = None
    try:  # the Routing Layers rule (Rules6 stream): "TOP LAYER_V5=TRUE|MID LAYER 1_V5=FALSE|..."
        import olefile
        raw = olefile.OleFileIO(str(doc.path)).openstream("Rules6/Data").read().decode("latin-1")
        m = re.search(r"RULEKIND=RoutingLayers.*?(?=RULEKIND=|\Z)", raw, re.S)
        block = raw[max(0, raw.rfind("|NAME=", 0, m.start())) if m else 0: m.end() if m else 0]
        flags = dict(re.findall(r"\|((?:TOP|BOTTOM|MID) LAYER(?: \d+)?)_V5=(TRUE|FALSE)", block))
        if flags:
            enabled = set()
            for lid in ids:
                name = "TOP LAYER" if lid == 1 else "BOTTOM LAYER" if lid == 32 else f"MID LAYER {lid - 1}"
                if flags.get(name, "TRUE") == "TRUE":
                    enabled.add(lid)
    except Exception:  # noqa: BLE001 - no rule stream: use the stack
        enabled = None
    if enabled is None:
        enabled = {lid for lid in ids if kinds.get(lid) != "plane"}
    return [lid for lid in ids if lid in enabled]
