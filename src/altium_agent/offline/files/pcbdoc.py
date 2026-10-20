# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/pcbdoc.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Reader for Altium PCB documents (.PcbDoc): no Altium installation required.

Text streams (Board6, Components6, Nets6, Classes6, Rules6, Polygons6, DifferentialPairs6) are
``|KEY=VALUE`` records. Primitive streams (Tracks6, Vias6, Arcs6, Pads6, Texts6) are binary; the
layouts follow the community-documented format (also used by KiCad's Altium importer).

All coordinates returned are millimetres relative to the board origin; Y grows upwards (Altium
convention).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import olefile

from .records import (
    BinaryReader,
    iter_records,
    mil_to_mm,
    parse_bool,
    parse_float,
    parse_int,
    parse_length_mil,
    parse_properties,
    decode_text,
    pcb_units_to_mm,
    text_records,
)

NONE16 = 0xFFFF

# Legacy (V6) layer ids used by binary primitives.
_LEGACY_LAYERS: dict[int, str] = {1: "TopLayer", 32: "BottomLayer", 33: "TopOverlay", 34: "BottomOverlay",
                                  35: "TopPaste", 36: "BottomPaste", 37: "TopSolder", 38: "BottomSolder",
                                  55: "DrillGuide", 56: "KeepOut", 73: "DrillDrawing", 74: "MultiLayer"}
_LEGACY_LAYERS.update({i: f"MidLayer{i - 1}" for i in range(2, 32)})
_LEGACY_LAYERS.update({i: f"InternalPlane{i - 38}" for i in range(39, 55)})
_LEGACY_LAYERS.update({i: f"Mechanical{i - 56}" for i in range(57, 73)})

COPPER_LEGACY = set(range(1, 33)) | set(range(39, 55))

RULE_UNITS_KEYS = ("GAP", "MINLIMIT", "MAXLIMIT", "PREFEREDWIDTH", "MINWIDTH", "MAXWIDTH",
                   "MINHOLEWIDTH", "MAXHOLEWIDTH", "PREFEREDHOLEWIDTH", "MINRING", "EXPANSION",
                   "CLEARANCE", "MINIMUM", "RELIEFAIRGAP", "RELIEFCONDUCTORWIDTH", "MINSOLDERMASKWIDTH")


def layer_name(layer_id: int) -> str:
    return _LEGACY_LAYERS.get(layer_id, f"Layer{layer_id}")


@dataclass
class StackLayer:
    index: int
    name: str
    layer_id: int
    kind: str  # copper | plane (internal plane) | dielectric | overlay | solder_mask | paste | other
    copper_thickness_mm: float | None = None
    dielectric_height_mm: float | None = None
    dielectric_constant: float | None = None
    dielectric_material: str = ""
    dielectric_type: str = ""

    def to_dict(self) -> dict:
        d = {"index": self.index, "name": self.name, "kind": self.kind}
        if self.copper_thickness_mm is not None:
            d["copper_thickness_mm"] = round(self.copper_thickness_mm, 4)
        if self.dielectric_height_mm is not None:
            d["dielectric_height_mm"] = round(self.dielectric_height_mm, 4)
        if self.dielectric_constant is not None:
            d["dielectric_constant"] = self.dielectric_constant
        if self.dielectric_material:
            d["material"] = self.dielectric_material
        if self.dielectric_type:
            d["dielectric_type"] = self.dielectric_type
        return d


@dataclass
class PcbComponent:
    index: int
    designator: str
    footprint: str
    comment: str
    x_mm: float
    y_mm: float
    rotation: float
    side: str
    locked: bool
    height_mm: float
    lib_ref: str
    description: str
    unique_id: str

    def to_dict(self) -> dict:
        return {
            "designator": self.designator, "footprint": self.footprint, "comment": self.comment,
            "x_mm": round(self.x_mm, 4), "y_mm": round(self.y_mm, 4), "rotation": round(self.rotation, 3),
            "side": self.side, "locked": self.locked, "height_mm": round(self.height_mm, 3),
            "lib_ref": self.lib_ref, "description": self.description,
        }


@dataclass
class Track:
    layer: int
    net: int
    component: int
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    polygon: int = NONE16
    keepout: bool = False

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


@dataclass
class Arc:
    layer: int
    net: int
    component: int
    cx: float
    cy: float
    radius: float
    start_angle: float
    end_angle: float
    width: float
    polygon: int = NONE16

    @property
    def length(self) -> float:
        sweep = (self.end_angle - self.start_angle) % 360.0
        if sweep == 0:
            sweep = 360.0
        return math.radians(sweep) * self.radius


@dataclass
class Via:
    net: int
    x: float
    y: float
    diameter: float
    hole: float
    layer_start: int
    layer_end: int
    component: int = NONE16


@dataclass
class Pad:
    name: str
    layer: int
    net: int
    component: int
    x: float
    y: float
    size_x: float
    size_y: float
    hole: float
    plated: bool
    shape: int  # 1 round, 2 rectangle, 3 octagonal, 9 rounded rectangle (from the size-and-shape subrecord)
    rotation: float
    corner_pct: int = 0  # rounded rectangle corner radius, % of half the smaller side


@dataclass
class Fill:
    layer: int
    net: int
    component: int
    x1: float
    y1: float
    x2: float
    y2: float
    rotation: float


@dataclass
class Text:
    layer: int
    component: int
    x: float
    y: float
    height: float
    rotation: float
    mirrored: bool
    text: str
    is_designator: bool
    is_comment: bool


@dataclass
class Region:
    layer: int
    net: int
    polygon: int
    component: int
    kind: int  # 0 copper, 1 polygon cutout, 2 dashed outline, 3 named region, 4 board cutout ...
    is_keepout: bool
    is_board_cutout: bool
    name: str
    outline: list[tuple[float, float]]
    holes: int

    @property
    def area_mm2(self) -> float:
        pts = self.outline
        if len(pts) < 3:
            return 0.0
        a = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            a += x1 * y2 - x2 * y1
        return abs(a) / 2.0


@dataclass
class PcbDocument:
    path: Path
    board: dict[str, str]
    origin_mil: tuple[float, float]
    outline_mm: list[dict]
    stack: list[StackLayer]
    components: list[PcbComponent]
    nets: list[str]
    classes: list[dict]
    rules: list[dict]
    polygons: list[dict]
    diff_pairs: list[dict]
    tracks: list[Track] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    pads: list[Pad] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def copper_for_legacy(self, layer: int) -> StackLayer | None:
        """Stack-up copper layer for a primitive's legacy layer id (1..31 -> 0x01000000+n, 32 -> 0x0100FFFF)."""
        target = 0x0100FFFF if layer == 32 else 0x01000000 + layer
        return next((s for s in self.stack if s.kind == "copper" and s.layer_id == target), None)

    @property
    def board_thickness_mm(self) -> float:
        t = sum((s.copper_thickness_mm or 0) + (s.dielectric_height_mm or 0) for s in self.stack
                if s.kind in ("copper", "plane", "dielectric"))
        return round(t, 4) if t > 0 else 1.6

    def layer_depth_mm(self, layer: int) -> float | None:
        """Distance from the top copper surface to the middle of this copper layer."""
        target = self.copper_for_legacy(layer)
        if target is None:
            return None
        z = 0.0
        for s in self.stack:
            if s.kind not in ("copper", "plane", "dielectric"):
                continue
            if s is target:
                return round(z + (s.copper_thickness_mm or 0) / 2, 4)
            z += (s.copper_thickness_mm or 0) + (s.dielectric_height_mm or 0)
        return None

    def layer_label(self, layer_id: int) -> str:
        """User-facing layer name (Board6 LAYERnNAME), falling back to the generic name."""
        return self.board.get(f"LAYER{layer_id}NAME") or layer_name(layer_id)

    # ------------------------------------------------------------------ derived views
    def net_name(self, idx: int) -> str:
        return self.nets[idx] if 0 <= idx < len(self.nets) else ""

    def designator(self, idx: int) -> str:
        return self.components[idx].designator if 0 <= idx < len(self.components) else ""

    @property
    def copper_layers(self) -> list[StackLayer]:
        return [lyr for lyr in self.stack if lyr.kind == "copper"]

    @property
    def outline_poly(self) -> list[tuple[float, float]]:
        """The board outline as a polygon, arcs expanded into short segments (<= 5 degrees). Use this, not the
        raw vertices: a round or rounded board joined vertex to vertex is a different shape."""
        out = []
        verts = self.outline_mm
        for i, v in enumerate(verts):
            out.append((v["x"], v["y"]))
            if v.get("arc") and "start_angle" in v and v.get("radius"):
                cx, cy = v["center"]
                r, sa, ea = v["radius"], v["start_angle"], v["end_angle"]
                sweep = (ea - sa) % 360.0 or 360.0  # Altium: counter-clockwise from sa to ea
                # the outline may run either way round: walk the arc from this vertex towards the next one
                a0 = math.degrees(math.atan2(v["y"] - cy, v["x"] - cx))
                nxt = verts[(i + 1) % len(verts)]
                a1 = math.degrees(math.atan2(nxt["y"] - cy, nxt["x"] - cx))

                def near(a, b):
                    return abs((a - b + 180.0) % 360.0 - 180.0) < 1.0
                if near(a0, ea) and near(a1, sa):  # traversed clockwise: from ea back to sa
                    start, step = ea, -sweep
                elif near(a0, sa):
                    start, step = sa, sweep
                else:  # angles not stored consistently: go the short way from this vertex to the next
                    d = (a1 - a0 + 180.0) % 360.0 - 180.0
                    start, step = a0, d
                n = max(2, int(abs(step) / 5.0))
                for k in range(1, n):
                    a = math.radians(start + step * k / n)
                    out.append((round(cx + r * math.cos(a), 4), round(cy + r * math.sin(a), 4)))
        return out

    def outline_bbox(self) -> dict | None:
        if not self.outline_mm:
            return None
        xs = [p[0] for p in self.outline_poly]
        ys = [p[1] for p in self.outline_poly]
        return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys),
                "width_mm": round(max(xs) - min(xs), 4), "height_mm": round(max(ys) - min(ys), 4)}

    def outline_area_mm2(self) -> float | None:
        pts = self.outline_poly
        if len(pts) < 3:
            return None
        a = 0.0
        for p, q in zip(pts, pts[1:] + pts[:1]):
            a += p[0] * q[1] - q[0] * p[1]
        return round(abs(a) / 2.0, 3)

    def pad_netlist(self) -> dict[str, list[str]]:
        """Net -> sorted ['U1-3', ...] from pad assignments (what the board actually has)."""
        nets: dict[str, set[str]] = defaultdict(set)
        for pad in self.pads:
            if pad.net == NONE16 or pad.component == NONE16:
                continue
            name = self.net_name(pad.net)
            ref = self.designator(pad.component)
            if name and ref:
                nets[name].add(f"{ref}-{pad.name}")
        return {n: sorted(v, key=_natural) for n, v in sorted(nets.items(), key=lambda kv: _natural(kv[0]))}

    def routed_length_by_net(self) -> dict[str, float]:
        lengths: dict[str, float] = defaultdict(float)
        for t in self.tracks:
            if t.net != NONE16 and t.polygon == NONE16 and t.layer in COPPER_LEGACY:
                lengths[self.net_name(t.net)] += t.length
        for a in self.arcs:
            if a.net != NONE16 and a.polygon == NONE16 and a.layer in COPPER_LEGACY:
                lengths[self.net_name(a.net)] += a.length
        return {k: round(v, 4) for k, v in sorted(lengths.items(), key=lambda kv: _natural(kv[0])) if k}

    def via_count_by_net(self) -> dict[str, int]:
        c = Counter(self.net_name(v.net) for v in self.vias if v.net != NONE16)
        return dict(sorted(c.items(), key=lambda kv: _natural(kv[0])))

    def summary(self) -> dict:
        copper = self.copper_layers
        return {
            "path": str(self.path),
            "board_outline": self.outline_bbox(),
            "board_area_mm2": self.outline_area_mm2(),
            "copper_layer_count": len(copper),
            "copper_layers": [lyr.name for lyr in copper],
            "total_thickness_mm": round(sum((lyr.copper_thickness_mm or 0) + (lyr.dielectric_height_mm or 0)
                                            for lyr in self.stack), 4) if self.stack else None,
            "components": len(self.components),
            "components_top": sum(1 for c in self.components if c.side == "top"),
            "components_bottom": sum(1 for c in self.components if c.side == "bottom"),
            "nets": len(self.nets),
            "pads": len(self.pads),
            "tracks": len([t for t in self.tracks if t.polygon == NONE16]),
            "arcs": len([a for a in self.arcs if a.polygon == NONE16]),
            "vias": len(self.vias),
            "polygons": len(self.polygons),
            "net_classes": [c["name"] for c in self.classes if c["kind"] == "net"],
            "differential_pairs": len(self.diff_pairs),
            "rules": len(self.rules),
            "warnings": self.warnings,
        }


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------------- stream helpers

def _stream(ole: olefile.OleFileIO, name: str) -> bytes | None:
    if ole.exists(name):
        return ole.openstream(name).read()
    return None


def _board_props(raw: bytes) -> dict[str, str]:
    # Board6 is one long record holding several concatenated property groups.
    texts = [decode_text(r.payload) for r in iter_records(raw) if r.kind == 0]
    return parse_properties("|".join(texts))


def _stack(board: dict[str, str]) -> list[StackLayer]:
    idx_re = re.compile(r"^V9_STACK_LAYER(\d+)_NAME$")
    layers: list[StackLayer] = []
    indices = sorted(int(m.group(1)) for k in board if (m := idx_re.match(k)))
    for i in indices:
        g = lambda key: board.get(f"V9_STACK_LAYER{i}_{key}")  # noqa: E731
        layer_id = parse_int(g("LAYERID"))
        name = g("NAME") or f"Layer{i}"
        group = layer_id >> 16
        if group == 0x0100:
            kind = "copper"
        elif group == 0x0101:  # internal plane n: legacy layer 38 + n
            kind = "plane"
        elif g("DIELHEIGHT") is not None and g("COPTHICK") is None:
            kind = {"16973834": "solder_mask", "16973835": "solder_mask"}.get(str(layer_id), "dielectric")
            low = name.lower()
            if "solder" in low or "mask" in low:
                kind = "solder_mask"
            elif "paste" in low:
                kind = "paste"
        elif "overlay" in name.lower() or "silk" in name.lower():
            kind = "overlay"
        elif "paste" in name.lower():
            kind = "paste"
        elif "solder" in name.lower():
            kind = "solder_mask"
        else:
            kind = "other"
        lyr = StackLayer(i, name, layer_id, kind)
        if g("COPTHICK") is not None:
            lyr.copper_thickness_mm = mil_to_mm(parse_length_mil(g("COPTHICK")))
        if g("DIELHEIGHT") is not None:
            lyr.dielectric_height_mm = mil_to_mm(parse_length_mil(g("DIELHEIGHT")))
            lyr.dielectric_constant = parse_float(g("DIELCONST"), 0.0) or None
            lyr.dielectric_material = g("DIELMATERIAL") or ""
            lyr.dielectric_type = {"0": "core", "1": "prepreg", "2": "surface", "3": "surface"}.get(
                g("DIELTYPE") or "", g("DIELTYPE") or "")
        layers.append(lyr)
    return layers


def _outline(board: dict[str, str], ox: float, oy: float) -> list[dict]:
    pts = []
    i = 0
    while f"VX{i}" in board:
        x = mil_to_mm(parse_length_mil(board[f"VX{i}"]) - ox)
        y = mil_to_mm(parse_length_mil(board[f"VY{i}"]) - oy)
        v = {"x": round(x, 4), "y": round(y, 4)}
        if parse_int(board.get(f"KIND{i}")) == 1:
            v["arc"] = True
            v["center"] = [round(mil_to_mm(parse_length_mil(board.get(f"CX{i}")) - ox), 4),
                           round(mil_to_mm(parse_length_mil(board.get(f"CY{i}")) - oy), 4)]
            v["radius"] = round(mil_to_mm(parse_length_mil(board.get(f"R{i}"))), 4)
            try:  # arc from this vertex, counter-clockwise from start to end angle, to the next vertex
                v["start_angle"] = float(board.get(f"SA{i}", "0").strip())
                v["end_angle"] = float(board.get(f"EA{i}", "0").strip())
            except ValueError:
                pass
        pts.append(v)
        i += 1
    # Altium repeats the first vertex at the end.
    if len(pts) > 1 and pts[0]["x"] == pts[-1]["x"] and pts[0]["y"] == pts[-1]["y"]:
        pts.pop()
    return pts


def _binary_records(raw: bytes, expected_type: int):
    r = BinaryReader(raw)
    while r.remaining() >= 5:
        rtype = r.u8()
        length = r.u32()
        payload = r.take(length)
        if rtype == expected_type:
            yield payload


class _Origin:
    def __init__(self, ox_mil: float, oy_mil: float) -> None:
        self.ox = ox_mil * 10_000.0
        self.oy = oy_mil * 10_000.0

    def x(self, v: int) -> float:
        return pcb_units_to_mm(v - self.ox)

    def y(self, v: int) -> float:
        return pcb_units_to_mm(v - self.oy)


def _tracks(raw: bytes, o: _Origin) -> list[Track]:
    out = []
    for p in _binary_records(raw, 4):
        r = BinaryReader(p)
        layer = r.u8(); r.u8(); flags2 = r.u8()  # noqa: E702
        net = r.u16(); polygon = r.u16(); comp = r.u16()  # noqa: E702
        r.skip(4)
        x1, y1, x2, y2 = r.i32(), r.i32(), r.i32(), r.i32()
        width = r.i32()
        out.append(Track(layer, net, comp, o.x(x1), o.y(y1), o.x(x2), o.y(y2), pcb_units_to_mm(width),
                         polygon, flags2 == 2))
    return out


def _arcs(raw: bytes, o: _Origin) -> list[Arc]:
    out = []
    for p in _binary_records(raw, 1):
        r = BinaryReader(p)
        layer = r.u8(); r.u8(); r.u8()  # noqa: E702
        net = r.u16(); polygon = r.u16(); comp = r.u16()  # noqa: E702
        r.skip(4)
        cx, cy = r.i32(), r.i32()
        radius = r.i32()
        sa, ea = r.f64(), r.f64()
        width = r.i32()
        out.append(Arc(layer, net, comp, o.x(cx), o.y(cy), pcb_units_to_mm(radius), sa, ea,
                       pcb_units_to_mm(width), polygon))
    return out


def _vias(raw: bytes, o: _Origin) -> list[Via]:
    out = []
    for p in _binary_records(raw, 3):
        r = BinaryReader(p)
        r.skip(3)
        net = r.u16(); r.u16(); comp = r.u16()  # noqa: E702  (net, polygon, component - as tracks)
        r.skip(4)
        x, y = r.i32(), r.i32()
        dia, hole = r.i32(), r.i32()
        ls, le = r.u8(), r.u8()
        out.append(Via(net, o.x(x), o.y(y), pcb_units_to_mm(dia), pcb_units_to_mm(hole), ls, le, comp))
    return out


def _pads(raw: bytes, o: _Origin) -> list[Pad]:
    out = []
    r = BinaryReader(raw)
    while r.remaining() > 5:
        rtype = r.u8()
        if rtype != 2:
            break
        sub1 = r.take(r.u32())
        name = ""
        if sub1:
            n = sub1[0]
            name = sub1[1 : 1 + n].decode("cp1252", errors="replace")
        for _ in range(3):  # subrecords 2..4
            r.skip(r.u32())
        main = r.take(r.u32())
        sizes = r.take(r.u32())  # subrecord 6: size and shape per layer
        m = BinaryReader(main)
        try:
            layer = m.u8(); m.u8(); m.u8()  # noqa: E702
            net = m.u16(); m.skip(2); comp = m.u16(); m.skip(4)  # noqa: E702
            x, y = m.i32(), m.i32()
            tx, ty = m.i32(), m.i32()
            m.skip(16)  # mid, bottom sizes
            hole = m.i32()
            top_shape = m.u8(); m.skip(2)  # noqa: E702
            rotation = m.f64()
            plated = m.u8() != 0
        except EOFError:
            continue
        # Size-and-shape: 29 inner sizes x/y (i32), 29 inner shapes, 1, hole shape, slot size i32, slot rotation f64,
        # 32 hole offsets x/y (i32), 1, then the alternative shape and corner radius % for each of the 32 layers.
        # A rounded rectangle is stored as shape "round" in the main record and 9 here.
        corner = 0
        alt_at = 29 * 8 + 29 + 1 + 1 + 4 + 8 + 32 * 8 + 1
        if len(sizes) >= alt_at + 64 and top_shape == 1 and sizes[alt_at] == 9:
            top_shape, corner = 9, sizes[alt_at + 32]
        out.append(Pad(name, layer, net, comp, o.x(x), o.y(y), pcb_units_to_mm(tx), pcb_units_to_mm(ty),
                       pcb_units_to_mm(hole), plated, top_shape, rotation, corner))
    return out


def _fills(raw: bytes, o: _Origin) -> list[Fill]:
    out = []
    for p in _binary_records(raw, 6):
        r = BinaryReader(p)
        layer = r.u8(); r.skip(2)  # noqa: E702
        net = r.u16(); r.skip(2); comp = r.u16(); r.skip(4)  # noqa: E702
        x1, y1, x2, y2 = r.i32(), r.i32(), r.i32(), r.i32()
        rot = r.f64()
        out.append(Fill(layer, net, comp, o.x(x1), o.y(y1), o.x(x2), o.y(y2), rot))
    return out


def _texts(raw: bytes, o: _Origin) -> list[Text]:
    out = []
    r = BinaryReader(raw)
    while r.remaining() > 5:
        if r.u8() != 5:
            break
        sub1 = r.take(r.u32())
        sub2 = r.take(r.u32())
        if len(sub1) < 42:
            continue
        m = BinaryReader(sub1)
        layer = m.u8(); m.skip(6); comp = m.u16(); m.skip(4)  # noqa: E702
        x, y, h = m.i32(), m.i32(), m.i32()
        m.skip(2)
        rot = m.f64()
        mirrored = m.u8() != 0
        text = sub2[1 : 1 + sub2[0]].decode("cp1252", errors="replace") if sub2 else ""
        out.append(Text(layer, comp, o.x(x), o.y(y), pcb_units_to_mm(h), rot % 360.0, mirrored, text,
                        bool(sub1[41]), bool(sub1[40])))
    return out


def _regions(raw: bytes, o: _Origin) -> list[Region]:
    """Regions6: solid regions, cutouts, keepouts and poured polygon fragments."""
    out = []
    for p in _binary_records(raw, 11):
        r = BinaryReader(p)
        try:
            layer = r.u8(); r.u8(); flags2 = r.u8()  # noqa: E702
            net = r.u16(); polygon = r.u16(); comp = r.u16()  # noqa: E702
            r.skip(5)
            holes = r.u16()
            r.skip(2)
            props = parse_properties(decode_text(r.take(r.u32())))
            outline = []
            for _ in range(r.u32()):
                x, y = r.f64(), r.f64()
                outline.append((round(o.x(int(x)), 4), round(o.y(int(y)), 4)))
        except EOFError:
            continue
        out.append(Region(layer, net, polygon, comp, parse_int(props.get("KIND")), flags2 == 2,
                          parse_bool(props.get("ISBOARDCUTOUT")), props.get("NAME", "").strip(), outline, holes))
    return out


def _decode_codes(value: str) -> str:
    """Polygon names are stored as comma-separated character codes (84,111,112 -> 'Top')."""
    if re.fullmatch(r"\d+(,\d+)*", value or ""):
        try:
            return "".join(chr(int(c)) for c in value.split(","))
        except ValueError:
            return value
    return value


def _polygon_outline(pp: dict[str, str], ox: float, oy: float) -> list[list[float]]:
    pts, i = [], 0
    while f"VX{i}" in pp:
        pts.append([round(mil_to_mm(parse_length_mil(pp[f"VX{i}"]) - ox), 4),
                    round(mil_to_mm(parse_length_mil(pp[f"VY{i}"]) - oy), 4)])
        i += 1
    return pts


def _component_texts(raw: bytes) -> tuple[dict[int, str], dict[int, str]]:
    """Component index -> (designator, comment) strings from Texts6.

    Byte 40 of the first subrecord flags a component's comment text, byte 41 its designator
    (verified against AD26 example boards)."""
    designators: dict[int, str] = {}
    comments: dict[int, str] = {}
    r = BinaryReader(raw)
    while r.remaining() > 5:
        rtype = r.u8()
        if rtype != 5:
            break
        sub1 = r.take(r.u32())
        sub2 = r.take(r.u32())
        if len(sub1) < 42 or not sub2:
            continue
        comp = int.from_bytes(sub1[7:9], "little")
        if comp == NONE16:
            continue
        text = sub2[1 : 1 + sub2[0]].decode("cp1252", errors="replace")
        if sub1[41]:
            designators[comp] = text
        elif sub1[40]:
            comments[comp] = text
    return designators, comments


# ---------------------------------------------------------------------- public API

def read_pcbdoc(path: Path, *, primitives: bool = True) -> PcbDocument:
    path = Path(path)
    st = path.stat()
    return _read_cached(str(path.resolve()), st.st_mtime_ns, st.st_size, primitives)


@lru_cache(maxsize=16)
def _read_cached(path_str: str, _mtime: int, _size: int, primitives: bool) -> PcbDocument:
    return _parse(Path(path_str), primitives)


def _parse(path: Path, primitives: bool) -> PcbDocument:
    warnings: list[str] = []
    with olefile.OleFileIO(str(path)) as ole:
        board_raw = _stream(ole, "Board6/Data") or b""
        board = _board_props(board_raw)
        ox = parse_length_mil(board.get("ORIGINX"))
        oy = parse_length_mil(board.get("ORIGINY"))
        origin = _Origin(ox, oy)

        comp_props = list(text_records(_stream(ole, "Components6/Data") or b""))
        des_texts: dict[int, str] = {}
        comment_texts: dict[int, str] = {}
        if (raw := _stream(ole, "Texts6/Data")) is not None:
            try:
                des_texts, comment_texts = _component_texts(raw)
            except EOFError:
                warnings.append("Texts6 truncated; designators taken from SOURCEDESIGNATOR only")

        components: list[PcbComponent] = []
        for i, c in enumerate(comp_props):
            # The designator text on the board is authoritative (it differs from SOURCEDESIGNATOR
            # when channels/annotation change it); fall back to the schematic link.
            designator = des_texts.get(i) or c.get("SOURCEDESIGNATOR") or f"#{i}"
            components.append(PcbComponent(
                index=i,
                designator=designator,
                footprint=c.get("PATTERN", ""),
                comment=comment_texts.get(i, ""),
                x_mm=mil_to_mm(parse_length_mil(c.get("X")) - ox),
                y_mm=mil_to_mm(parse_length_mil(c.get("Y")) - oy),
                rotation=parse_float(c.get("ROTATION")),
                side="bottom" if c.get("LAYER", "").upper() == "BOTTOM" else "top",
                locked=parse_bool(c.get("LOCKED")),
                height_mm=mil_to_mm(parse_length_mil(c.get("HEIGHT"))),
                lib_ref=c.get("SOURCELIBREFERENCE", ""),
                description=c.get("SOURCEDESCRIPTION", ""),
                unique_id=c.get("SOURCEUNIQUEID", ""),
            ))

        nets = [n.get("NAME", "") for n in text_records(_stream(ole, "Nets6/Data") or b"")]

        classes = []
        kinds = {0: "net", 1: "component", 2: "from_to", 3: "pad", 4: "layer", 6: "differential_pair",
                 7: "polygon"}
        for c in text_records(_stream(ole, "Classes6/Data") or b""):
            members = [c[k] for k in sorted((k for k in c if re.fullmatch(r"M\d+", k)), key=lambda k: int(k[1:]))]
            classes.append({"name": c.get("NAME", ""), "kind": kinds.get(parse_int(c.get("KIND")), c.get("KIND", "")),
                            "superclass": parse_bool(c.get("SUPERCLASS")), "members": members})

        rules = []
        for rp in text_records(_stream(ole, "Rules6/Data") or b"", prefix_bytes=2):
            rule = {"name": rp.get("NAME", ""), "kind": rp.get("RULEKIND", ""),
                    "enabled": parse_bool(rp.get("ENABLED"), True), "priority": parse_int(rp.get("PRIORITY")),
                    "scope1": rp.get("SCOPE1EXPRESSION", ""), "scope2": rp.get("SCOPE2EXPRESSION", ""),
                    "comment": rp.get("COMMENT", "")}
            values = {}
            for k in RULE_UNITS_KEYS:
                if k in rp:
                    values[k.lower() + "_mm"] = round(mil_to_mm(parse_length_mil(rp[k])), 5)
            if values:
                rule["values"] = values
            rules.append(rule)

        polygons = []
        for pp in text_records(_stream(ole, "Polygons6/Data") or b""):
            net_idx = parse_int(pp.get("NET"), NONE16)
            polygons.append({"name": _decode_codes(pp.get("NAME", "")), "layer": pp.get("LAYER", ""),
                             "net": nets[net_idx] if 0 <= net_idx < len(nets) else "",
                             "pour_index": parse_int(pp.get("POURINDEX")),
                             "shelved": parse_bool(pp.get("SHELVED")),
                             "type": pp.get("POLYGONTYPE", ""), "hatch": pp.get("HATCHSTYLE", ""),
                             "pour_over_same_net": parse_bool(pp.get("POUROVER")),
                             "remove_dead_copper": parse_bool(pp.get("REMOVEDEAD")),
                             "outline_mm": _polygon_outline(pp, ox, oy)})

        diff_pairs = []
        for dp in text_records(_stream(ole, "DifferentialPairs6/Data") or b""):
            diff_pairs.append({"name": dp.get("NAME", ""), "positive": dp.get("POSITIVENETNAME", ""),
                               "negative": dp.get("NEGATIVENETNAME", "")})

        doc = PcbDocument(path, board, (ox, oy), _outline(board, ox, oy), _stack(board), components, nets,
                          classes, rules, polygons, diff_pairs, warnings=warnings)

        if primitives:
            for stream, fn, attr in (("Tracks6/Data", _tracks, "tracks"), ("Arcs6/Data", _arcs, "arcs"),
                                     ("Vias6/Data", _vias, "vias"), ("Pads6/Data", _pads, "pads"),
                                     ("Fills6/Data", _fills, "fills"), ("Texts6/Data", _texts, "texts"),
                                     ("Regions6/Data", _regions, "regions")):
                raw = _stream(ole, stream)
                if raw is None:
                    continue
                try:
                    setattr(doc, attr, fn(raw, origin))
                except (EOFError, ValueError) as exc:
                    warnings.append(f"{stream}: partially parsed ({exc})")
            # Poured regions carry no net of their own, only the index of their parent polygon.
            poly_net = {i: nets.index(p["net"]) for i, p in enumerate(polygons) if p["net"] in nets}
            for rg in doc.regions:
                if rg.net == NONE16 and rg.polygon in poly_net:
                    rg.net = poly_net[rg.polygon]
    return doc
