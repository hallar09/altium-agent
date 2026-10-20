# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/routing.py (_apply_ses). Modified for Altium Agent
# (2026): pure conversion to the eda-agent bridge's pcb.place_tracks /
# pcb.place_vias rows, in absolute mils; see docs/PROVENANCE.md.
"""Turn a Freerouting session (SES) into bridge rows, without touching Altium.

The SES holds the whole board's wiring, the copper that was already there
(exported as protected wiring) included. Rows are made only for copper that
is new: a track or via that matches existing copper within 10 um is
skipped, so importing never doubles hand routing. Freerouting necks tracks
down at pads; a track narrower than its net's minimum width is widened back
to it. Tracks on internal plane layers are dropped (in Altium a track on a
plane layer is a split line).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..offline.files.pcbdoc import PcbDocument
from ..units import MILS_PER_MM
from .specctra import parse_ses


def layer_token(legacy_id: int) -> str:
    """The bridge's canonical token for a legacy copper layer id."""
    if legacy_id == 1:
        return "TopLayer"
    if legacy_id == 32:
        return "BottomLayer"
    return f"MidLayer{legacy_id - 1}"


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


@dataclass
class SesRows:
    tracks: list[str] = field(default_factory=list)   # x1,y1,x2,y2,width,layer,net (mils)
    vias: list[str] = field(default_factory=list)     # x,y,size,hole,low,high,net (mils)
    nets: set = field(default_factory=set)
    skipped_existing: int = 0
    widths_clamped: int = 0
    plane_tracks_dropped: int = 0
    other_nets_dropped: int = 0
    bad_net_names: list = field(default_factory=list)

    def summary(self) -> dict:
        return {"tracks": len(self.tracks), "vias": len(self.vias), "nets": len(self.nets),
                "skipped_existing": self.skipped_existing, "widths_clamped": self.widths_clamped,
                "plane_tracks_dropped": self.plane_tracks_dropped, "other_nets_dropped": self.other_nets_dropped,
                "bad_net_names": self.bad_net_names}


def ses_rows(ses: Path, layer_map: dict[str, int], rules: dict, origin_mil: tuple[float, float],
             existing: PcbDocument | None = None, only_nets: set[str] | None = None) -> SesRows:
    """Rows for the copper the SES adds. ``layer_map`` and ``rules`` are the DsnExport's."""
    via_hole = float(rules.get("via_hole_mm") or 0.2)
    r = parse_ses(ses, via_hole)
    out = SesRows()
    ox, oy = origin_mil

    def mil(x: float, y: float) -> tuple[float, float]:
        return ox + x * MILS_PER_MM, oy + y * MILS_PER_MM

    def key(x: float, y: float) -> tuple[int, int]:  # 10 um cells; neighbours are checked too
        return round(x * 100), round(y * 100)

    have_v: set = set()
    have_t: dict[int, set] = {}
    if existing is not None:
        have_v = {key(v.x, v.y) for v in existing.vias}
        for t in existing.tracks:
            have_t.setdefault(t.layer, set()).update(
                (key(t.x1, t.y1) + key(t.x2, t.y2), key(t.x2, t.y2) + key(t.x1, t.y1)))

    def near(k, cells) -> bool:
        return any((k[0] + dx, k[1] + dy) in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    def dup_track(t: dict, lid: int) -> bool:
        a, b = key(t["x1"], t["y1"]), key(t["x2"], t["y2"])
        cells = have_t.get(lid, set())
        return any((a[0] + i, a[1] + j, b[0] + k, b[1] + m) in cells
                   for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1) for m in (-1, 0, 1))

    floor_all = float(rules.get("min_width_mm") or 0.0)
    net_min = rules.get("net_min_width_mm") or {}
    for t in r.tracks:
        if only_nets is not None and t["net"] not in only_nets:
            out.other_nets_dropped += 1
            continue
        lid = layer_map.get(t["layer"])
        if lid is None or lid > 32:
            out.plane_tracks_dropped += 1
            continue
        if existing is not None and dup_track(t, lid):
            out.skipped_existing += 1
            continue
        if any(ch in t["net"] for ch in ",|"):
            out.bad_net_names.append(t["net"])
            continue
        w = t["width"]
        floor = max(floor_all, float(net_min.get(t["net"], 0.0)))
        if w < floor - 1e-6:
            w = floor
            out.widths_clamped += 1
        x1, y1 = mil(t["x1"], t["y1"])
        x2, y2 = mil(t["x2"], t["y2"])
        out.tracks.append(",".join([_num(x1), _num(y1), _num(x2), _num(y2), _num(w * MILS_PER_MM),
                                    layer_token(lid), t["net"]]))
        out.nets.add(t["net"])
    for v in r.vias:
        if only_nets is not None and v["net"] not in only_nets:
            out.other_nets_dropped += 1
            continue
        if existing is not None and near(key(v["x"], v["y"]), have_v):
            out.skipped_existing += 1
            continue
        if any(ch in v["net"] for ch in ",|"):
            out.bad_net_names.append(v["net"])
            continue
        x, y = mil(v["x"], v["y"])
        out.vias.append(",".join([_num(x), _num(y), _num(v["diameter"] * MILS_PER_MM),
                                  _num(v["hole"] * MILS_PER_MM), "TopLayer", "BottomLayer", v["net"]]))
        out.nets.add(v["net"])
    out.bad_net_names = sorted(set(out.bad_net_names))
    return out
