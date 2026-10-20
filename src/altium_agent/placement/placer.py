# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Brass contributors
# Adapted from Brass src/board/placer.ts (TypeScript, Apache-2.0) for Altium
# Agent (2026): see docs/PROVENANCE.md. Changes: Python; the overlap terms are
# vectorised with numpy; levers may come from a house-style profile; lengths
# carry _mm in the reply.
"""The placer: deterministic, offline, fast enough to run every round.

It sizes the board from the parts (unless the size is given), starts from a
floorplan that keeps each schematic sheet together, then anneals: total wire
length (half-perimeter per net; supply nets count little, they become pours
and planes) against overlap and the board edge. Decoupling caps are pulled to
their IC's supply pins, connectors to an edge, mounting holes to the corners,
an RF module's antenna end to the edge. A legalisation pass leaves no
overlap; parts that do not fit are reported, never stacked.

An AI does not place parts here: it chooses the levers (board size, aspect,
spacing, sides, fixed parts and edges) and judges the score.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from .antenna import AntennaKeepout, antenna_zone
from .footprints import Box, Footprint
from .shape import (
    FORM_FACTORS,
    Outline,
    build_form,
    circle_outline,
    distance_to_edge,
    inside,
    outline_from_points,
    polygon_outline,
    rect_outline,
)

EDGE_KINDS = ("left", "right", "top", "bottom", "any", "corner", "none")


@dataclass
class Levers:
    width: float | None = None       # board size in mm; None -> sized from the parts
    height: float | None = None
    density: float = 0.3                # share of the board the bodies (with spacing) cover
    aspect: float = 1.3
    corner_radius: float = 2.0
    edge_keepout: float = 0.4
    edge_gap: float = 1.0               # bodies keep this far from the edge, mm
    spacing: float = 0.5                # gap between bodies, mm
    escape: float = 1.0                 # extra room round many-pin parts (multiplier)
    bottom: str = "none"                # none | passives | all
    fixed: list[dict] = field(default_factory=list)   # {ref, x_mm, y_mm, rotation?, side?}
    edges: list[dict] = field(default_factory=list)   # {ref, edge}
    keep: list[str] = field(default_factory=list)     # parts that stay where they are
    seed: int = 1
    effort: float = 1.0
    layers: int = 2
    shape: dict = field(default_factory=lambda: {"kind": "rect"})
    scale: float = 1.0
    heights: str = "enforce"            # enforce | report
    decap_weight: float = 1.5           # pull of a decoupling cap to its IC's supply pin
    body: str = "full"                  # full (pads, silk, courtyard) | copper (pads only)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("width", "height", "corner_radius", "edge_keepout", "edge_gap", "spacing"):
            d[f"{k}_mm"] = d.pop(k)
        return d


def sanitize_levers(raw: dict | None) -> dict:
    """Levers from a tool call or a profile: known keys with sane values only.

    Lengths are accepted as ``*_mm`` (the Altium Agent spelling) or bare.
    """
    out: dict[str, Any] = {}
    if not raw:
        return out

    def num(k: str, lo: float, hi: float) -> float | None:
        v = raw.get(f"{k}_mm", raw.get(k))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        return min(hi, max(lo, float(v)))

    for k, lo, hi in (("width", 5, 1000), ("height", 5, 1000), ("density", 0.08, 0.7),
                      ("aspect", 0.25, 4), ("corner_radius", 0, 50), ("edge_keepout", 0, 5),
                      ("edge_gap", 0, 10), ("spacing", 0.1, 5), ("escape", 0, 3),
                      ("effort", 0.02, 10), ("scale", 0.2, 10), ("decap_weight", 0, 10)):
        v = num(k, lo, hi)
        if v is not None:
            out[k] = v
    seed = num("seed", 1, 1e9)
    if seed is not None:
        out["seed"] = int(seed)
    layers = num("layers", 1, 32)
    if layers is not None:
        out["layers"] = 2 if layers <= 2 else 4 if layers <= 4 else 6 if layers <= 6 else 8
    shape = sanitize_shape(raw.get("shape"))
    if shape:
        out["shape"] = shape
    if raw.get("bottom") in ("none", "passives", "all"):
        out["bottom"] = raw["bottom"]
    if raw.get("body") in ("full", "copper"):
        out["body"] = raw["body"]
    if raw.get("heights") in ("enforce", "report"):
        out["heights"] = raw["heights"]
    if isinstance(raw.get("keep"), list):
        out["keep"] = [str(r) for r in raw["keep"]]
    if isinstance(raw.get("edges"), list):
        out["edges"] = [{"ref": str(e["ref"]), "edge": str(e["edge"])} for e in raw["edges"]
                        if isinstance(e, dict) and isinstance(e.get("ref"), str) and e.get("edge") in EDGE_KINDS]
    if isinstance(raw.get("fixed"), list):
        fixed = []
        for p in raw["fixed"]:
            if not isinstance(p, dict) or not isinstance(p.get("ref"), str):
                continue
            x, y = p.get("x_mm", p.get("x")), p.get("y_mm", p.get("y"))
            try:
                x, y = float(x), float(y)
            except (TypeError, ValueError):
                continue
            item = {"ref": p["ref"], "x": x, "y": y}
            if p.get("rotation") is not None:
                item["rotation"] = float(p["rotation"])
            if p.get("side") in ("top", "bottom"):
                item["side"] = p["side"]
            fixed.append(item)
        out["fixed"] = fixed
    return out


def sanitize_shape(raw: Any) -> dict | None:
    if isinstance(raw, str):
        if raw in ("rect", "circle"):
            return {"kind": raw}
        if raw in FORM_FACTORS:
            return {"kind": "form", "name": raw}
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("kind") in ("rect", "circle"):
        return {"kind": raw["kind"]}
    if raw.get("kind") == "form" and raw.get("name") in FORM_FACTORS:
        return {"kind": "form", "name": raw["name"]}
    if raw.get("kind") in ("polygon", "outline") and isinstance(raw.get("points"), list):
        pts = []
        for p in raw["points"]:
            try:
                pts.append((float(p[0]), float(p[1])) if isinstance(p, (list, tuple))
                           else (float(p["x"]), float(p["y"])))
            except (TypeError, ValueError, KeyError, IndexError):
                continue
        if len(pts) >= 3:
            return {"kind": raw["kind"], "points": pts}
    return None


# ------------------------------------------------------------------ classes of nets and parts

_POWER_NAME = re.compile(
    r"^(?:[ADP]?GND\w*|VSS\w*|VCC\w*|VDD\w*|VEE\w*|VBAT\w*|VIN\w*|VSYS\w*|VBUS\w*|V?\+?\d+V\d*\w*|"
    r"\+?\d+V\d+\w*|\d+V\w*|VREF\w*)$", re.IGNORECASE)
_CONNECTOR = re.compile(r"^(J|CN|CON|USB|X|P)\d", re.IGNORECASE)
_CONNECTOR_FP = re.compile(
    r"conn|header|hdr|usb|jst|molex|terminal|socket|sma|ufl|u\.fl|rj45|dsub|d-sub|pin_?header|ph\d|sh\d|"
    r"gh\d|df\d|pcie|card.?edge|fpc|ffc|zif|[-_]cn[-_]", re.IGNORECASE)
_HOLE = re.compile(r"^(MH|H|HOLE)\d", re.IGNORECASE)
_HOLE_FP = re.compile(r"mount|hole|mh\d|m\d(\.\d)?_?hole", re.IGNORECASE)


def is_supply_net(name: str, pins: int) -> bool:
    return bool(_POWER_NAME.match(name)) or pins > 16


def is_ground(name: str) -> bool:
    return bool(re.match(r"^[ADP]?GND|^VSS|^GROUND", name or "", re.IGNORECASE))


def is_connector(f: Footprint) -> bool:
    return bool(_CONNECTOR.match(f.ref)) and bool(
        _CONNECTOR_FP.search(f.footprint) or _CONNECTOR_FP.search(f.comment)
        or re.match(r"^(J|CN|CON|USB)\d", f.ref, re.IGNORECASE))


def is_mounting_hole(f: Footprint) -> bool:
    return bool(_HOLE.match(f.ref) or _HOLE_FP.search(f.footprint)) and len(f.pins) <= 1


def is_indicator(f: Footprint) -> bool:
    return bool(re.match(r"^(DS|LED)\d", f.ref, re.IGNORECASE) or re.search(r"\bled\b", f"{f.footprint} {f.comment}", re.IGNORECASE)) \
        and len(f.pins) <= 4


def is_passive(f: Footprint) -> bool:
    return len(f.pins) <= 3 and bool(re.match(r"^(R|C|L|D|FB|F)\d", f.ref, re.IGNORECASE)) and not f.through_hole


# ------------------------------------------------------------------ orientation geometry

ORIENTS = 8  # rotation k*90 (k = 0..3) on side 0 top / 1 bottom; index = side*4 + k


def orient_index(k: int, side: int) -> int:
    return side * 4 + (k & 3)


def turn(lx: float, ly: float, o: int) -> tuple[float, float]:
    """Local -> board offset: the bottom side flips about X (as Altium does), then k x 90 CCW."""
    side, k = o >> 2, o & 3
    y0 = -ly if side else ly
    if k == 0:
        return lx, y0
    if k == 1:
        return -y0, lx
    if k == 2:
        return -lx, -y0
    return y0, -lx


def turn_box(b: Box, o: int) -> tuple[float, float, float, float]:
    pts = [turn(b.min_x, b.min_y, o), turn(b.max_x, b.min_y, o), turn(b.max_x, b.max_y, o),
           turn(b.min_x, b.max_y, o)]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    if w <= 0:
        return 0.0
    h = min(a[3], b[3]) - max(a[1], b[1])
    return 0.0 if h <= 0 else w * h


def mulberry32(seed: int):
    """Brass's PRNG, so a seed gives the same placement in both."""
    a = (seed & 0xFFFFFFFF) or 1

    def rnd() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = _imul(t ^ (t >> 15), t | 1)
        t ^= (t + _imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return rnd


def _imul(a: int, b: int) -> int:
    return (a * b) & 0xFFFFFFFF


def _ceil_to(v: float, step: float) -> float:
    return math.ceil(v / step - 1e-9) * step


def _r3(v: float) -> float:
    return round(v * 1000) / 1000


# ------------------------------------------------------------------ the placer

@dataclass
class _Part:
    f: Footprint
    boxes: list[tuple]
    pins: list[list[tuple]]
    margin: float
    movable: bool
    edge: str
    sides: list[int]
    o: int = 0
    x: float = 0.0
    y: float = 0.0
    fixed_box: tuple | None = None
    fixed_pins: list[tuple] | None = None
    rotation: float = 0.0
    nets: list[int] = field(default_factory=list)
    zone: list[tuple] | None = None
    tip: list[tuple] | None = None


@dataclass
class _Net:
    name: str
    weight: float
    pins: list[tuple[int, int]]
    supply: bool
    gidx: np.ndarray | None = None


def place_board(parts_in: list[Footprint], levers: dict | None = None, *,
                signal_layers: int = 2, antennas: dict[str, AntennaKeepout] | None = None,
                outline: Outline | None = None) -> dict:
    """Place every part; returns placements in mm from the board origin, the outline,
    keep-outs, a score and notes. ``outline`` keeps an existing board shape
    (a re-layout inside the same outline) instead of sizing a new one."""
    L = replace(Levers(), **sanitize_levers(levers or {}))
    notes: list[str] = []
    rand = mulberry32(L.seed)
    layers = max(1, signal_layers)
    fixed_by = {p["ref"].upper(): p for p in L.fixed}
    keep = {r.upper() for r in L.keep}
    edge_by = {e["ref"].upper(): e["edge"] for e in L.edges}
    limits = None
    if L.shape.get("kind") == "form" and L.shape.get("name") in FORM_FACTORS:
        limits = build_form(L.shape["name"]).max_height
    if limits:
        tall = [f for f in parts_in if (f.height or 0) > limits["top"] + 1e-6]
        if tall:
            notes.append(f"over the {limits['top']} mm top height limit: "
                         + ", ".join(f"{f.ref} {f.height:.2f} mm" for f in tall))

    ants = {r.upper(): k for r, k in (antennas or {}).items()}
    parts: list[_Part] = []
    if L.body == "copper":
        # Altium Agent: space parts by copper; silkscreen and courtyards may overlap, as on dense boards
        parts_in = [replace(f, box=f.pad_box) if f.pad_box else f for f in parts_in]
        notes.append("bodies are the copper (pads) only: silkscreen and courtyards may overlap")
    for f in parts_in:
        boxes = [turn_box(f.box, o) for o in range(ORIENTS)]
        pins = [[turn(p.x, p.y, o) for p in f.pins] for o in range(ORIENTS)]
        ak = ants.get(f.ref.upper())
        az = antenna_zone(f.box, ak) if ak else None
        w, h = f.box.w, f.box.h
        perimeter = max(2 * (w + h), 1)
        escape = L.escape * min(1.5, len(f.pins) / perimeter * 0.3) if len(f.pins) >= 8 else 0
        fanout_room = 0.3 if L.layers >= 4 else 0
        ref = f.ref.upper()
        edge = edge_by.get(ref) or ("corner" if is_mounting_hole(f) else "any" if is_connector(f) else "none")
        too_tall = bool(limits) and L.heights != "report" and (f.height or 0) > limits["bottom"] + 1e-6
        if too_tall or ak:
            sides = [0]
        elif L.bottom == "all" and not f.through_hole or L.bottom == "passives" and is_passive(f):
            sides = [0, 1]
        else:
            sides = [0]
        parts.append(_Part(
            f=f, boxes=boxes, pins=pins, margin=L.spacing / 2 + escape + fanout_room,
            movable=not f.locked and ref not in fixed_by and ref not in keep, edge=edge, sides=sides,
            zone=[turn_box(az[0], o) for o in range(ORIENTS)] if az else None,
            tip=[turn(az[1][0], az[1][1], o) for o in range(ORIENTS)] if az else None))
    for ref in ants:
        if not any(p.f.ref.upper() == ref for p in parts):
            notes.append(f"antenna keep-out for {ref}: no such part")
    index_of = {p.f.ref.upper(): i for i, p in enumerate(parts)}
    for ref in fixed_by:
        if ref not in index_of:
            notes.append(f"fixed part {ref} is not on the board")

    # ---- nets, then decoupling attractions
    net_idx: dict[str, int] = {}
    nets: list[_Net] = []
    for i, p in enumerate(parts):
        for j, pin in enumerate(p.f.pins):
            if not pin.net:
                continue
            n = net_idx.get(pin.net)
            if n is None:
                n = len(nets)
                net_idx[pin.net] = n
                nets.append(_Net(pin.net, 1.0, [], False))
            nets[n].pins.append((i, j))
    for n in nets:
        n.supply = is_supply_net(n.name, len(n.pins))
        n.weight = 0.08 if n.supply else 0.6 if len(n.pins) > 8 else 1.0
    real_nets = len(nets)
    ic_pins: dict[str, list] = {}
    decaps = 0
    for i, p in enumerate(parts):
        if not re.match(r"^C\d", p.f.ref, re.IGNORECASE) or len(p.f.pins) != 2:
            continue
        a, b = p.f.pins
        if is_ground(a.net or "") and b.net and not is_ground(b.net):
            supply_pin = 1
        elif is_ground(b.net or "") and a.net and not is_ground(a.net):
            supply_pin = 0
        else:
            continue
        net = p.f.pins[supply_pin].net
        n = nets[net_idx[net]]
        if not n.supply:
            continue
        counts: dict[int, int] = {}
        for q in n.pins:
            counts[q[0]] = counts.get(q[0], 0) + 1
        best, best_count = -1, 0
        for part, c in counts.items():
            if len(parts[part].f.pins) >= 8 and c > best_count:
                best, best_count = part, c
        if best < 0:
            continue
        key = f"{best}:{net}"
        lst = ic_pins.get(key) or [q for q in n.pins if q[0] == best]
        ic_pins[key] = lst[1:] + lst[:1]
        nets.append(_Net(f"decap {p.f.ref}", L.decap_weight, [(i, supply_pin), lst[0]], False))
        decaps += 1
    if decaps:
        notes.append(f"{decaps} decoupling cap(s) pulled to their IC's supply pins")
    for k, n in enumerate(nets):
        seen = set()
        if len(n.pins) > 8:
            n.gidx = np.array([0] * len(n.pins))  # filled once pin_base exists

        for part, _ in n.pins:
            if part not in seen:
                seen.add(part)
                parts[part].nets.append(k)

    # ---- the board
    body_area = sum((p.boxes[0][2] - p.boxes[0][0] + 2 * p.margin) * (p.boxes[0][3] - p.boxes[0][1] + 2 * p.margin)
                    for p in parts)
    share = 1 if L.bottom == "none" else 0.8 if L.bottom == "passives" else 0.6
    W, H = L.width or 0, L.height or 0
    biggest = max([1.0] + [max(p.boxes[0][2] - p.boxes[0][0], p.boxes[0][3] - p.boxes[0][1]) for p in parts])
    wanted = body_area * share / min(0.7, max(0.08, L.density))
    if not W or not H:
        aspect = min(4, max(0.25, L.aspect))
        if W and not H:
            H = wanted / W
        elif H and not W:
            W = wanted / H
        else:
            W = math.sqrt(wanted * aspect)
            H = wanted / W
        W = _ceil_to(max(W, biggest) + 2 * L.edge_gap, 0.5)
        H = _ceil_to(max(H, biggest) + 2 * L.edge_gap, 0.5)
    shape = L.shape or {"kind": "rect"}
    shift = (0.0, 0.0)
    if outline is not None:
        shift = outline.extra.get("origin_offset", (0.0, 0.0))
        outline = outline_from_points([(x - shift[0], y - shift[1]) for x, y in outline.poly], outline.name)
    elif shape["kind"] == "circle":
        d = L.width or _ceil_to(max(math.sqrt(4 * wanted / math.pi), biggest * math.sqrt(2)) + 2 * L.edge_gap, 0.5)
        outline = circle_outline(d)
    elif shape["kind"] == "polygon":
        s = max(0.2, L.scale)
        outline = polygon_outline([(x * s, y * s) for x, y in shape["points"]], L.corner_radius)
    elif shape["kind"] == "outline":
        outline = outline_from_points(shape["points"])
        shift = outline.extra["origin_offset"]
        outline = outline_from_points([(x - shift[0], y - shift[1]) for x, y in outline.poly])
    elif shape["kind"] == "form":
        outline = build_form(shape["name"])
    else:
        outline = rect_outline(W, H, L.corner_radius)
    if shift != (0.0, 0.0):
        # the board's own outline need not start at the origin: place in its frame, report in the board's
        for p in parts:
            p.f = replace(p.f, at=replace(p.f.at, x=p.f.at.x - shift[0], y=p.f.at.y - shift[1]))
        fixed_by = {k: {**v, "x": v["x"] - shift[0], "y": v["y"] - shift[1]} for k, v in fixed_by.items()}
    W, H = outline.width, outline.height
    R = outline.corner_radius
    region = (L.edge_gap, L.edge_gap, W - L.edge_gap, H - L.edge_gap)
    corner_inset = R * (1 - math.sqrt(0.5)) if outline.rect and R > 0 else 0
    mask = None if outline.rect else _Mask(outline, L.edge_gap)
    if not outline.rect:
        notes.append(f"outline: {outline.name}")
    if outline.card_edge:
        ce = outline.card_edge
        cands = [p for p in parts if p.f.ref.upper() not in fixed_by
                 and (ce.match.search(p.f.footprint) or ce.match.search(p.f.comment))]
        cands.sort(key=lambda p: -len(p.f.pins))
        if cands:
            card = cands[0]
            b = card.boxes[orient_index(round(ce.rotation / 90), 0)]
            fixed_by[card.f.ref.upper()] = {"ref": card.f.ref, "x": _r3(ce.x - (b[0] + b[2]) / 2),
                                           "y": _r3(ce.y - b[1]), "rotation": ce.rotation, "side": "top"}
            card.movable = False
            card.edge = "none"
            notes.append(f"{card.f.ref} sits on the card edge")
        else:
            notes.append(f"no card-edge connector found for {outline.name}")
    if not outline.rect:
        for p in parts:
            if p.edge != "none":
                p.edge = "corner" if p.edge == "corner" else "any"

    n_parts = len(parts)
    # numpy state: each part's current body box and side, for the vectorised overlap terms
    BX = np.zeros((n_parts, 4))
    MARGIN = np.array([p.margin for p in parts])
    SIDE = np.zeros(n_parts, dtype=int)
    TH = np.array([p.f.through_hole for p in parts], dtype=bool)
    zoned = [i for i, p in enumerate(parts) if p.zone]
    # every pin's board position, for the long nets (supplies, buses) measured with numpy
    pin_base = np.cumsum([0] + [len(p.f.pins) for p in parts])
    PX = np.zeros(int(pin_base[-1]))
    PY = np.zeros(int(pin_base[-1]))
    pin_offsets = [[np.array(o_pins, dtype=float).reshape(-1, 2) for o_pins in p.pins] for p in parts]
    for n in nets:
        if n.gidx is not None:
            n.gidx = np.array([pin_base[part] + pin for part, pin in n.pins], dtype=int)

    def bbox(p: _Part, o=None, x=None, y=None):
        o = p.o if o is None else o
        x = p.x if x is None else x
        y = p.y if y is None else y
        b = p.fixed_box if o < 0 else p.boxes[o]
        return (x + b[0], y + b[1], x + b[2], y + b[3])

    def side_of(p: _Part) -> int:
        return (1 if p.f.at.side == "bottom" else 0) if p.o < 0 else p.o >> 2

    def sync(i: int) -> None:
        p = parts[i]
        BX[i] = bbox(p)
        SIDE[i] = side_of(p)
        a, b = pin_base[i], pin_base[i + 1]
        if b > a:
            off = np.array(p.fixed_pins, dtype=float).reshape(-1, 2) if p.o < 0 else pin_offsets[i][p.o]
            PX[a:b] = p.x + off[:, 0]
            PY[a:b] = p.y + off[:, 1]

    def set_exact(p: _Part, x: float, y: float, rotation: float, side: str) -> None:
        p.x, p.y, p.rotation = x, y, rotation
        k = ((rotation % 360) + 360) % 360 / 90
        s = 1 if side == "bottom" else 0
        if abs(k - round(k)) < 1e-6:
            p.o = orient_index(round(k) & 3, s)
            return
        t = math.radians(rotation)

        def tr(lx, ly):
            y0 = -ly if s else ly
            return lx * math.cos(t) - y0 * math.sin(t), lx * math.sin(t) + y0 * math.cos(t)

        b = p.f.box
        pts = [tr(b.min_x, b.min_y), tr(b.max_x, b.min_y), tr(b.max_x, b.max_y), tr(b.min_x, b.max_y)]
        p.fixed_box = (min(q[0] for q in pts), min(q[1] for q in pts), max(q[0] for q in pts), max(q[1] for q in pts))
        p.fixed_pins = [tr(q.x, q.y) for q in p.f.pins]
        p.o = -1

    def snap_to_edge(p: _Part) -> None:
        if p.o < 0:
            return
        b = p.boxes[p.o]
        left, right = region[0] - b[0], region[2] - b[2]
        bottom, top = region[1] - b[1], region[3] - b[3]

        def clamp_x():
            p.x = min(max(p.x, left + corner_inset), right - corner_inset)

        def clamp_y():
            p.y = min(max(p.y, bottom + corner_inset), top - corner_inset)

        e = p.edge
        if e == "corner":
            p.x = left + corner_inset if abs(p.x - left) <= abs(p.x - right) else right - corner_inset
            p.y = bottom + corner_inset if abs(p.y - bottom) <= abs(p.y - top) else top - corner_inset
            return
        if e == "any":
            d = {"left": abs(p.x - left), "right": abs(p.x - right), "bottom": abs(p.y - bottom), "top": abs(p.y - top)}
            e = min(d.items(), key=lambda kv: kv[1])[0]
        if e == "left":
            p.x = left
            clamp_y()
        elif e == "right":
            p.x = right
            clamp_y()
        elif e == "bottom":
            p.y = bottom
            clamp_x()
        elif e == "top":
            p.y = top
            clamp_x()

    def snap(p: _Part) -> None:
        if p.edge != "none" and outline.rect:
            snap_to_edge(p)

    # ---- start: fixed and kept parts where they belong, the rest floorplanned by sheet
    for p in parts:
        fx = fixed_by.get(p.f.ref.upper())
        if fx:
            set_exact(p, fx["x"], fx["y"], fx.get("rotation", 0), fx.get("side", "top"))
        elif not p.movable:
            set_exact(p, p.f.at.x, p.f.at.y, p.f.at.rotation, p.f.at.side)
    _floorplan([p for p in parts if p.movable], region, rand)
    for p in parts:
        if p.movable:
            snap(p)
    for i in range(n_parts):
        sync(i)

    def pin_at(p: _Part, j: int):
        off = p.fixed_pins[j] if p.o < 0 else p.pins[p.o][j]
        return p.x + off[0], p.y + off[1]

    def net_len(n: _Net) -> float:
        if len(n.pins) < 2:
            return 0.0
        if n.gidx is not None:
            xs, ys = PX[n.gidx], PY[n.gidx]
            return float(xs.max() - xs.min() + ys.max() - ys.min())
        xs, ys = [], []
        for part, pin in n.pins:
            x, y = pin_at(parts[part], pin)
            xs.append(x)
            ys.append(y)
        return max(xs) - min(xs) + max(ys) - min(ys)

    def outside(p: _Part) -> float:
        b = bbox(p)
        dx = max(0, region[0] - b[0]) + max(0, b[2] - region[2])
        dy = max(0, region[1] - b[1]) + max(0, b[3] - region[3])
        s = dx * (b[3] - b[1]) + dy * (b[2] - b[0]) + 4 * (dx + dy)
        return s + mask.blocked(b) if mask else s

    def indicator_pull(p: _Part) -> float:
        if not is_indicator(p.f):
            return 0.0
        return (15 if side_of(p) else 0) + (3 * max(0, H - L.edge_gap - bbox(p)[3]) if outline.card_edge else 0)

    big_area = outline.area * 0.15

    def settle_pull(p: _Part) -> float:
        if not outline.card_edge or side_of(p) or len(p.sides) > 1:
            return 0.0
        b = bbox(p)
        return 10 * b[1] if (b[2] - b[0]) * (b[3] - b[1]) > big_area else 0.0

    def edge_pull(p: _Part) -> float:
        if not mask or p.edge == "none":
            return 0.0
        b = bbox(p)
        d = distance_to_edge(outline.poly, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        return 2 * max(0, d - min(b[2] - b[0], b[3] - b[1]) / 2 - L.edge_gap - 0.3)

    def zone_of(p: _Part):
        if not p.zone or p.o < 0:
            return None
        z = p.zone[p.o]
        return (p.x + z[0], p.y + z[1], p.x + z[2], p.y + z[3])

    def pair_overlap(p: _Part, q: _Part) -> float:
        s = 0.0
        if side_of(p) == side_of(q) or p.f.through_hole or q.f.through_hole:
            bp, bq = bbox(p), bbox(q)
            s = _overlap((bp[0] - p.margin, bp[1] - p.margin, bp[2] + p.margin, bp[3] + p.margin),
                         (bq[0] - q.margin, bq[1] - q.margin, bq[2] + q.margin, bq[3] + q.margin))
        pz = zone_of(p)
        if pz:
            s += _overlap(pz, bbox(q))
        qz = zone_of(q)
        if qz:
            s += _overlap(qz, bbox(p))
        return s

    def overlap_of(i: int) -> float:
        """Vectorised: grown body boxes against every other part on a clashing side, plus antenna zones."""
        p = parts[i]
        b = bbox(p)
        m = p.margin
        gx0 = BX[:, 0] - MARGIN
        gy0 = BX[:, 1] - MARGIN
        gx1 = BX[:, 2] + MARGIN
        gy1 = BX[:, 3] + MARGIN
        w = np.minimum(b[2] + m, gx1) - np.maximum(b[0] - m, gx0)
        h = np.minimum(b[3] + m, gy1) - np.maximum(b[1] - m, gy0)
        side = side_of(p)
        clash = (SIDE == side) | TH | p.f.through_hole
        clash[i] = False
        s = float(np.sum(np.where(clash & (w > 0) & (h > 0), w * h, 0.0)))
        if zoned:
            pz = zone_of(p)
            for j in (range(n_parts) if pz else zoned):
                if j == i:
                    continue
                q = parts[j]
                if pz:
                    s += _overlap(pz, tuple(BX[j]))
                qz = zone_of(q)
                if qz:
                    s += _overlap(qz, b)
        return s

    away_from_card = outline.card_edge.y + 5 if outline.card_edge else -math.inf

    def antenna_pull(p: _Part) -> float:
        if not p.tip or p.o < 0:
            return 0.0
        tx, ty = p.tip[p.o]
        x, y = p.x + tx, p.y + ty
        inner = 25 * distance_to_edge(outline.poly, x, y) if inside(outline.poly, x, y) else 0.0
        if y < away_from_card:
            return 25 * (away_from_card - y) + inner
        return inner

    movable = [i for i, p in enumerate(parts) if p.movable]
    iterations = (round(min(800_000, max(60_000, 3000 * n_parts)) * max(0.02, L.effort)) if movable else 0)
    span = max(W, H)

    def local_cost(i: int, lam: float, mu: float, skip: set | None = None) -> float:
        p = parts[i]
        s = 0.0
        for k in p.nets:
            if skip is None or k not in skip:
                s += nets[k].weight * net_len(nets[k])
        return (s + lam * overlap_of(i) + mu * outside(p) + edge_pull(p) + antenna_pull(p)
                + indicator_pull(p) + settle_pull(p))

    def area(p: _Part) -> float:
        b = p.fixed_box if p.o < 0 else p.boxes[p.o]
        return (b[2] - b[0]) * (b[3] - b[1])

    # ---- a card's big parts first: each at the lowest free spot, held while the rest anneals
    first_placed: list[int] = []
    if outline.card_edge:
        big = sorted([i for i in movable if settle_pull(parts[i]) > 0], key=lambda i: -area(parts[i]))
        placed_first: set[int] = set()
        for i in big:
            p = parts[i]
            best = None
            for s in p.sides:
                for k in range(4):
                    p.o = orient_index(k, s)
                    x = region[0]
                    while x <= region[2]:
                        y = region[1]
                        while y <= region[3]:
                            p.x, p.y = x, y
                            if outside(p) > 1e-9 or any(
                                    j != i and (not q.movable or j in placed_first) and pair_overlap(p, q) > 1e-9
                                    for j, q in enumerate(parts)):
                                y += 0.25
                                continue
                            c = settle_pull(p) + antenna_pull(p) + abs(x - W / 2) * 0.1
                            if best is None or c < best[0]:
                                best = (c, x, y, p.o)
                            break
                        x += 0.5
            if best:
                _, p.x, p.y, p.o = best
                movable.remove(i)
                placed_first.add(i)
                notes.append(f"{p.f.ref} placed first, low on the card (the far end stays free)")
            sync(i)
        first_placed.extend(placed_first)

    # ---- anneal
    t_sum, cnt = 0.0, 0
    for _ in range(min(200, len(movable) * 4)):
        i = movable[int(rand() * len(movable))]
        p = parts[i]
        before = local_cost(i, 1, 10)
        ox, oy = p.x, p.y
        p.x += (rand() - 0.5) * span * 0.3
        p.y += (rand() - 0.5) * span * 0.3
        sync(i)
        t_sum += abs(local_cost(i, 1, 10) - before)
        cnt += 1
        p.x, p.y = ox, oy
        sync(i)
    T0 = t_sum / cnt * 0.6 if cnt else 1.0
    T0 = T0 or 1.0
    t_end = max(1e-4, T0 * 2e-4)
    for it in range(iterations if movable else 0):
        progress = it / iterations
        T = T0 * (t_end / T0) ** progress
        lam = 1.5 * (1 + 40 * progress * progress)
        mu = 20 * (1 + 10 * progress)
        r = rand()
        i = movable[int(rand() * len(movable))]
        p = parts[i]
        if r < 0.15 and len(movable) > 1:
            j = movable[int(rand() * len(movable))]
            q = parts[j]
            if j == i or (p.edge != "none") != (q.edge != "none"):
                continue
            shared = set(p.nets) & set(q.nets)
            before = local_cost(i, lam, mu) + local_cost(j, lam, mu, shared) - lam * pair_overlap(p, q)
            saved = (p.x, p.y, q.x, q.y)
            p.x, p.y, q.x, q.y = q.x, q.y, p.x, p.y
            snap(p)
            snap(q)
            sync(i)
            sync(j)
            after = local_cost(i, lam, mu) + local_cost(j, lam, mu, shared) - lam * pair_overlap(p, q)
            d = after - before
            if d > 0 and rand() >= math.exp(-d / T):
                p.x, p.y, q.x, q.y = saved
                sync(i)
                sync(j)
            continue
        before = local_cost(i, lam, mu)
        saved = (p.x, p.y, p.o)
        if r < 0.27:
            p.o = orient_index(((p.o & 3) + (1 if rand() < 0.5 else 3)) & 3, p.o >> 2)
        elif r < 0.3 and len(p.sides) > 1:
            p.o = orient_index(p.o & 3, (p.o >> 2) ^ 1)
        else:
            reach = max(0.3, span * (0.45 * (1 - progress) ** 2 + 0.01))
            p.x += (rand() * 2 - 1) * reach
            p.y += (rand() * 2 - 1) * reach
        snap(p)
        sync(i)
        d = local_cost(i, lam, mu) - before
        if d > 0 and rand() >= math.exp(-d / T):
            p.x, p.y, p.o = saved
            sync(i)

    def slide_antennas() -> None:
        for i in movable:
            p = parts[i]
            if not p.tip or p.o < 0:
                continue
            b = p.boxes[p.o]
            tx, ty = p.tip[p.o]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            dx, dy = ((math.copysign(1, tx - cx), 0) if abs(tx - cx) > abs(ty - cy) else (0, math.copysign(1, ty - cy)))
            home = (p.x, p.y)
            base = overlap_of(i)
            out0 = outside(p)
            best = home
            s = 0.25
            while s <= span:
                p.x, p.y = home[0] + dx * s, home[1] + dy * s
                sync(i)
                if outside(p) > out0 + 1e-9:
                    break
                if overlap_of(i) <= base + 1e-9:
                    best = (p.x, p.y)
                s += 0.25
            p.x, p.y = best
            sync(i)

    slide_antennas()

    # ---- legalise: every overlap or out-of-board part moves to the nearest free spot
    unplaced: list[str] = []

    def free(i: int) -> bool:
        return outside(parts[i]) < 1e-9 and overlap_of(i) < 1e-9

    order = sorted(movable, key=lambda i: -area(parts[i]))
    for _pass in range(2):
        for i in order:
            if free(i):
                continue
            p = parts[i]
            home = (p.x, p.y, p.o)
            best, best_cost = None, math.inf
            step = max(0.25, min(1.0, math.sqrt(area(p)) / 4))
            orients = [orient_index(k, s) for s in p.sides for k in range(4)]
            d = 0.0
            while d <= span and best is None:
                for x, y in _ring_points(home[0], home[1], d, step):
                    for o in orients:
                        p.x, p.y, p.o = x, y, o
                        snap(p)
                        sync(i)
                        if not free(i):
                            continue
                        c = edge_pull(p) + antenna_pull(p) + indicator_pull(p) + settle_pull(p)
                        for k in p.nets:
                            c += nets[k].weight * net_len(nets[k])
                        if c < best_cost:
                            best_cost, best = c, (p.x, p.y, p.o)
                d += max(step, d * 0.08)
            p.x, p.y, p.o = best if best else home
            sync(i)
    slide_antennas()
    for i in first_placed + order:
        if not free(i):
            unplaced.append(parts[i].f.ref)
    if unplaced:
        notes.append(f"{len(unplaced)} part(s) do not fit without overlap: {', '.join(unplaced[:12])}"
                     f"{', ...' if len(unplaced) > 12 else ''}; grow the board or lower the density")

    # ---- result
    signal = total = 0.0
    lens = []
    for k in range(real_nets):
        ln = net_len(nets[k])
        total += ln
        if not nets[k].supply:
            signal += ln
            lens.append({"net": nets[k].name, "length_mm": round(ln, 1)})
    overlaps = 0
    for i in range(n_parts):
        for j in range(i + 1, n_parts):
            p, q = parts[i], parts[j]
            clash = side_of(p) == side_of(q) or p.f.through_hole or q.f.through_hole
            zi, zj = zone_of(p), zone_of(q)
            if ((clash and _overlap(bbox(p), bbox(q)) > 1e-6) or (zi and _overlap(zi, bbox(q)) > 1e-6)
                    or (zj and _overlap(zj, bbox(p)) > 1e-6)):
                overlaps += 1
    keepouts = []
    for p in parts:
        z = zone_of(p)
        if z:
            c = {"ref": p.f.ref, "min_x_mm": _r3(max(0, z[0]) + shift[0]), "min_y_mm": _r3(max(0, z[1]) + shift[1]),
                 "max_x_mm": _r3(min(W, z[2]) + shift[0]), "max_y_mm": _r3(min(H, z[3]) + shift[1])}
            if c["max_x_mm"] > c["min_x_mm"] and c["max_y_mm"] > c["min_y_mm"]:
                keepouts.append(c)
    bodies = sum(area(p) for p in parts)
    placements = [{"ref": p.f.ref, "x_mm": _r3(p.x + shift[0]), "y_mm": _r3(p.y + shift[1]),
                   "rotation": p.rotation if p.o < 0 else (p.o & 3) * 90,
                   "side": "bottom" if side_of(p) else "top"} for p in parts]
    if limits and L.heights == "report":
        by = {f.ref: f for f in parts_in}
        over = [pl for pl in placements
                if (by[pl["ref"]].height or 0) > (limits["bottom"] if pl["side"] == "bottom" else limits["top"]) + 1e-6]
        if over:
            notes.append("over the height limit (reported, not avoided): "
                         + ", ".join(f"{pl['ref']} {by[pl['ref']].height:.2f} mm on the {pl['side']}" for pl in over))
    pitch = 0.4
    od = outline.to_dict()
    if shift != (0.0, 0.0):
        od["offset_mm"] = [round(shift[0], 4), round(shift[1], 4)]
    return {
        "width_mm": W, "height_mm": H, "corner_radius_mm": R, "edge_keepout_mm": L.edge_keepout,
        "outline": od, "placements": placements, "keepouts": keepouts,
        "score": {
            "signal_length_mm": round(signal), "total_length_mm": round(total),
            "density": _r3(bodies / outline.area) if outline.area else None,
            "demand": _r3(signal * pitch / (outline.area * layers)) if outline.area else None,
            "overlaps": overlaps, "unplaced": unplaced,
            "longest": sorted(lens, key=lambda d: -d["length_mm"])[:5],
            "bottom_parts": sum(1 for pl in placements if pl["side"] == "bottom"),
        },
        "levers": L.to_dict(), "iterations": iterations, "notes": notes,
    }


class _Mask:
    """Where a body may go on a shaped board: blocked cells as a summed-area table."""

    def __init__(self, outline: Outline, gap: float) -> None:
        self.cell = max(0.1, min(0.25, min(outline.width, outline.height) / 200))
        cell = self.cell
        self.nx = math.ceil(outline.width / cell) + 1
        self.ny = math.ceil(outline.height / cell) + 1
        xs = (np.arange(self.nx) + 0.5) * cell
        ys = (np.arange(self.ny) + 0.5) * cell
        X, Y = np.meshgrid(xs, ys)
        bad = (~_inside_np(outline.poly, X, Y)) | (_edge_dist_np(outline.poly, X, Y) < gap)
        self.sat = np.zeros((self.ny + 1, self.nx + 1))
        self.sat[1:, 1:] = bad.cumsum(0).cumsum(1)

    def blocked(self, b) -> float:
        c = self.cell
        i0 = max(0, min(self.nx, math.ceil(b[0] / c - 0.5)))
        i1 = max(0, min(self.nx, math.floor(b[2] / c - 0.5) + 1))
        j0 = max(0, min(self.ny, math.ceil(b[1] / c - 0.5)))
        j1 = max(0, min(self.ny, math.floor(b[3] / c - 0.5) + 1))
        if i1 <= i0 or j1 <= j0:
            return 0.0
        s = self.sat
        n = s[j1, i1] - s[j1, i0] - s[j0, i1] + s[j0, i0]
        return float(n) * c * c * 4


def _inside_np(poly, X, Y):
    c = np.zeros(X.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[i - 1]
        if yi == yj:
            continue
        cond = ((yi > Y) != (yj > Y)) & (X < (xj - xi) * (Y - yi) / (yj - yi) + xi)
        c ^= cond
    return c


def _edge_dist_np(poly, X, Y):
    best = np.full(X.shape, np.inf)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i - 1]
        x2, y2 = poly[i]
        dx, dy = x2 - x1, y2 - y1
        den = dx * dx + dy * dy or 1.0
        t = np.clip(((X - x1) * dx + (Y - y1) * dy) / den, 0, 1)
        best = np.minimum(best, np.hypot(X - (x1 + t * dx), Y - (y1 + t * dy)))
    return best


def _floorplan(parts: list[_Part], region, rand) -> None:
    """Starting positions: the region sliced among the sheets by area, each sheet packed in rows."""
    if not parts:
        return
    groups: dict[str, list[_Part]] = {}
    for p in parts:
        groups.setdefault(p.f.sheet or "board", []).append(p)

    def area_of(ps):
        return sum((p.boxes[0][2] - p.boxes[0][0] + 2 * p.margin) * (p.boxes[0][3] - p.boxes[0][1] + 2 * p.margin)
                   for p in ps)

    items = sorted(({"k": k, "ps": ps, "a": area_of(ps)} for k, ps in groups.items()), key=lambda d: -d["a"])
    cells: dict[str, tuple] = {}

    def slice_(r, lst):
        if len(lst) == 1:
            cells[lst[0]["k"]] = r
            return
        total = sum(x["a"] for x in lst)
        acc, cut = 0.0, 1
        while cut < len(lst):
            acc += lst[cut - 1]["a"]
            if acc >= total / 2:
                break
            cut += 1
        a, b = lst[:cut], lst[cut:]
        share = sum(x["a"] for x in a) / total if total else 0.5
        if r[2] - r[0] >= r[3] - r[1]:
            xm = r[0] + (r[2] - r[0]) * share
            slice_((r[0], r[1], xm, r[3]), a)
            slice_((xm, r[1], r[2], r[3]), b)
        else:
            ym = r[1] + (r[3] - r[1]) * share
            slice_((r[0], r[1], r[2], ym), a)
            slice_((r[0], ym, r[2], r[3]), b)

    slice_(region, items)
    for it in items:
        cell = cells[it["k"]]
        ps = sorted(it["ps"], key=lambda p: -(p.boxes[0][3] - p.boxes[0][1]))
        x, y, row_h = cell[0], cell[1], 0.0
        for p in ps:
            b = p.boxes[0]
            w = b[2] - b[0] + 2 * p.margin
            h = b[3] - b[1] + 2 * p.margin
            if x + w > cell[2] and x > cell[0]:
                x = cell[0]
                y += row_h
                row_h = 0.0
            p.o = 0
            p.x = x + p.margin - b[0] + (rand() - 0.5) * 0.01
            p.y = min(y, cell[3] - h) + p.margin - b[1]
            x += w
            row_h = max(row_h, h)


def _ring_points(x: float, y: float, d: float, step: float) -> list[tuple[float, float]]:
    if d == 0:
        return [(x, y)]
    out = []
    n = min(64, max(1, round(2 * d / step)))
    for i in range(n):
        t = -d + 2 * d * i / n
        out.extend([(x + t, y - d), (x + d, y + t), (x - t, y + d), (x - d, y - t)])
    return out
