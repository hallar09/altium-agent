# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/specctra.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Specctra DSN export (from a parsed PcbDoc) and SES import, for autorouting with Freerouting.

Design choices that keep the round trip exact without Altium's own Specctra support:
- every component is placed at its own origin, unrotated, on the front side; its pins keep their absolute
  orientation in per-pad padstacks, and padstacks name their copper layers explicitly. No transforms are needed
  to map Altium pads to DSN pins, and mirrored bottom-side parts route on the right layer;
- coordinates are micrometres relative to the Altium board origin (Y up, as in Altium);
- poured polygons are exported as planes of their net, so Freerouting connects to them with vias;
- existing copper is exported as protected wiring, so it is kept.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..units import MM_PER_MIL

MULTILAYER = 74


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def _u(mm: float) -> str:
    return f"{mm * 1000:.1f}"


def _ident(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.+]", "_", s) or "_"


@dataclass
class DsnOptions:
    track_width_mm: float | None = None
    clearance_mm: float | None = None
    via_diameter_mm: float | None = None
    via_hole_mm: float | None = None
    route_layers: list[str] | None = None  # copper layer names to route on (default: all signal layers)
    skip_nets: list[str] = field(default_factory=list)  # nets left to pours / done by hand
    keep_existing: bool = True
    planes: bool = True  # pours as planes (the Java build of Freerouting; its native CLI crashes on them)
    net_widths_mm: dict[str, float] = field(default_factory=dict)
    # Inner layers that only carry one net's board-wide pour (no SMD pads) become power planes: not routed on,
    # and Freerouting connects that net's pins to them with vias. Off by default: on Kame_FMU, routing on all six
    # layers left 19 connections, keeping the two GND layers as planes left 40.
    plane_layers: bool = False
    # Split internal planes: (plane number, net, x0, y0, x1, y1) rectangles in mm, e.g. from PcbAutoLayout's
    # planes.txt REGION lines; split_margin_mm keeps vias off the split line (half its gap + plane clearance).
    split_regions: list[tuple[int, str, float, float, float, float]] = field(default_factory=list)
    split_margin_mm: float = 0.55
    # Incremental routing: route only these nets. Every other net stays in the design (pins, planes, clearances)
    # but goes into the FIXED_CLASS net class, which Freerouting is told to ignore.
    route_only: list[str] | None = None
    # Layers left out of route_layers that are planes of a given net ({layer name: net}, e.g. a fit variant's
    # PWR_VCC3); any other such layer is a plane of the main ground net.
    plane_nets: dict[str, str] = field(default_factory=dict)


FIXED_CLASS = "AGENT_FIXED"


@dataclass
class DsnExport:
    text: str
    layer_map: dict[str, int]  # DSN layer name -> Altium legacy layer id
    nets: int
    components: int
    rules: dict


def _rule_defaults(doc: PcbDocument) -> dict:
    """Board-wide routing constraints from the PcbDoc rules (the 'All'-scoped rule of each kind).

    The via must satisfy every applicable rule at once: RoutingVias (preferred/min size), HoleSize (min hole)
    and MinimumAnnularRing; the outline clearance comes from a Clearance rule scoped to the board outline."""
    width = min_width = clearance = None
    via_d = via_h = hole_min = ring = None
    outline_gap = None
    for r in sorted(doc.rules, key=lambda r: r["priority"], reverse=True):  # priority 1 = highest, applied last
        if not r["enabled"]:
            continue
        v = r.get("values", {})
        k = r["kind"].lower()
        s1 = r["scope1"].strip().lower()
        if k == "clearance" and ("isboardoutline" in s1 or "isboardcutout" in s1):
            outline_gap = v.get("gap_mm") or outline_gap
            continue
        if s1 != "all":
            continue
        if k == "width":
            width = v.get("preferedwidth_mm") or v.get("minlimit_mm") or width
            min_width = v.get("minlimit_mm") or min_width
        elif k == "clearance" and r["scope2"].strip().lower() == "all":
            clearance = v.get("gap_mm") or clearance
        elif k == "routingvias":
            via_d = v.get("preferedwidth_mm") or v.get("minwidth_mm") or via_d
            via_h = v.get("preferedholewidth_mm") or v.get("minholewidth_mm") or via_h
        elif k == "holesize":
            hole_min = v.get("minlimit_mm") or hole_min
        elif k == "minimumannularring":
            ring = v.get("minimum_mm") or v.get("minring_mm") or ring
    via_h = max(via_h or 0.3, hole_min or 0.0)
    via_d = max(via_d or 0.6, round(via_h + 2 * (ring or 0.1), 4))
    clearance = clearance or 0.2032
    return {"width": width or 0.254, "min_width": min_width or 0.0, "clearance": clearance, "via_d": via_d,
            "via_h": via_h, "outline_clearance": max(outline_gap or clearance, clearance)}


def _inset(points: list[tuple[float, float]], d: float) -> list[tuple[float, float]]:
    """Move a simple polygon's edges inward by d (miter join); fine for the usual convex-ish board outlines."""
    n = len(points)
    if d <= 0 or n < 3:
        return points
    area = sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n))
    sign = 1.0 if area > 0 else -1.0  # inward normal is to the left for counter-clockwise outlines
    lines = []
    for i in range(n):
        (x1, y1), (x2, y2) = points[i], points[(i + 1) % n]
        L = math.hypot(x2 - x1, y2 - y1) or 1e-12
        nx, ny = -sign * (y2 - y1) / L, sign * (x2 - x1) / L
        lines.append((x1 + nx * d, y1 + ny * d, x2 + nx * d, y2 + ny * d))
    out = []
    for i in range(n):
        ax1, ay1, ax2, ay2 = lines[i - 1]
        bx1, by1, bx2, by2 = lines[i]
        den = (ax1 - ax2) * (by1 - by2) - (ay1 - ay2) * (bx1 - bx2)
        if abs(den) < 1e-12:
            out.append((bx1, by1))
            continue
        t = ((ax1 - bx1) * (by1 - by2) - (ay1 - by1) * (bx1 - bx2)) / den
        out.append((ax1 + t * (ax2 - ax1), ay1 + t * (ay2 - ay1)))
    return out


def net_width_rules(doc: PcbDocument) -> dict[str, tuple[float, float]]:
    """Net -> (preferred, minimum) width in mm from enabled Width rules scoped InNet(...) / InNetClass(...)
    against All; the lowest priority number wins, as in Altium."""
    members = {c["name"]: c.get("members", []) for c in doc.classes if c.get("kind") == "net"}
    out: dict[str, tuple[float, float, int]] = {}
    for r in doc.rules:
        if r["kind"].lower() != "width" or not r["enabled"] or r["scope2"].strip().lower() not in ("all", ""):
            continue
        m = re.fullmatch(r"\s*In(Net|NetClass)\('([^']+)'\)\s*", r["scope1"])
        v = r["values"]
        if not m or "preferedwidth_mm" not in v:
            continue
        nets = [m.group(2)] if m.group(1) == "Net" else members.get(m.group(2), [])
        pri = int(r["priority"] or 0)
        for n in nets:
            if n not in out or pri < out[n][2]:
                out[n] = (max(v["preferedwidth_mm"], v.get("minlimit_mm", 0)), v.get("minlimit_mm", 0), pri)
    return {n: (p, mn) for n, (p, mn, _) in out.items()}


def _area(poly: list) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))) / 2


def _is_copper_region(r, name_of: dict) -> bool:
    return r.kind == 0 and not r.is_keepout and r.polygon == NONE16 and r.layer in name_of and len(r.outline) >= 3


def _inside(x: float, y: float, poly: list) -> bool:
    """Even-odd point-in-polygon test; poly is [(x, y), ...] in mm."""
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _circle(cx: float, cy: float, r: float, n: int = 16) -> str:
    return " ".join(f"{_u(cx + r * math.cos(2 * math.pi * i / n))} {_u(cy + r * math.sin(2 * math.pi * i / n))}"
                    for i in range(n + 1))


def export_dsn(doc: PcbDocument, opts: DsnOptions | None = None) -> DsnExport:
    opts = opts or DsnOptions()
    rules = _rule_defaults(doc)
    width = opts.track_width_mm or rules["width"]
    clearance = opts.clearance_mm or rules["clearance"]
    via_d = opts.via_diameter_mm or rules["via_d"]
    via_h = opts.via_hole_mm or rules["via_h"]

    # Copper layers in stack order.
    # Altium internal planes (legacy ids 39..54) are power layers: vias pass through them and Freerouting never
    # routes on a power layer that has a plane (without one it treats the layer as a signal layer and routes
    # there - tracks that would cut the plane in Altium). So every plane layer gets a plane: its own net over the
    # whole layer, or - split by net-less split lines - the split regions passed in opts.split_regions.
    layers: list[tuple[str, int]] = []
    internal: dict[int, str] = {}  # legacy id -> plane net
    for s in doc.stack:
        if s.kind == "copper":
            lid = 32 if s.layer_id == 0x0100FFFF else s.layer_id - 0x01000000
        elif s.kind == "plane":
            n = s.layer_id & 0xFFFF
            lid = 38 + n
            net = doc.board.get(f"PLANE{n}NETNAME", "")
            internal[lid] = "" if net in ("", "(No Net)") else net
        else:
            continue
        layers.append((_ident(s.name), lid))
    if not layers:
        layers = [("Top", 1), ("Bottom", 32)]
    name_of = {lid: n for n, lid in layers}
    all_layers = [n for n, _ in layers]

    def poly_layer(p: dict) -> int | None:
        lid = {"TOP": 1, "BOTTOM": 32}.get(p["layer"].upper())
        if lid is None:
            m = re.match(r"MID(\d+)", p["layer"].upper())
            lid = 1 + int(m.group(1)) if m else None
        return lid

    plane_of: dict[int, dict] = {}  # inner layer id -> its board-wide pour
    if opts.plane_layers and opts.planes and doc.outline_mm:
        board = _area([(q["x"], q["y"]) for q in doc.outline_mm])
        smd_layers = {q.layer for q in doc.pads if q.hole == 0}
        for p in doc.polygons:
            lid = poly_layer(p)
            if (lid in name_of and lid not in (1, 32) and lid not in smd_layers and p["net"] and not p["shelved"]
                    and p["net"] not in opts.skip_nets and p["outline_mm"] and _area(p["outline_mm"]) >= 0.8 * board
                    and not any(t.layer == lid and t.net != NONE16 and doc.net_name(t.net) != p["net"]
                                and t.polygon == NONE16 for t in doc.tracks)):
                plane_of[lid] = p
    if opts.route_layers:
        routable = {_ident(x) for x in opts.route_layers}
    else:
        routable = {n for n, lid in layers if lid not in plane_of and lid not in internal}
    power = set(plane_of) | set(internal)
    # signal layers the board does not route on (e.g. inner ground layers): describe them as what they are - a
    # plane of the main ground net. A full-layer keepout instead (even a wire_keepout) makes Freerouting place
    # almost no vias (Kame: 0 vias, 158 open in 48 min; as planes: 88 open after one 80 s pass).
    fill_layers: dict[int, str] = {}
    if opts.route_layers:
        from ..analysis.board import supply_nets
        ground = supply_nets(doc)[1]
        gnet = max(ground, key=lambda g: sum(1 for q in doc.pads if q.net != NONE16 and doc.net_name(q.net) == g),
                   default="")
        named = {_ident(k): v for k, v in opts.plane_nets.items()}
        for n, lid in layers:
            net = named.get(n, gnet)
            if n not in routable and lid not in power and net:
                fill_layers[lid] = net
        power |= set(fill_layers)

    out: list[str] = []
    w = out.append
    w(f"(pcb {_q(doc.path.stem)}")
    w('  (parser (string_quote ") (space_in_quoted_tokens on) (host_cad "Altium Agent") (host_version "0.1"))')
    w("  (resolution um 10)")
    w("  (unit um)")
    w("  (structure")
    for i, (n, lid) in enumerate(layers):
        w(f"    (layer {n} (type {'power' if lid in power else 'signal'}) (property (index {i})))")
    if doc.outline_mm:
        ring = doc.outline_poly  # arcs expanded: a round board stays round
        pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in ring + ring[:1])
        w(f"    (boundary (path pcb 0 {pts}))")
    via_name = f"Via[0-{len(layers) - 1}]_{via_d * 1000:.0f}:{via_h * 1000:.0f}_um"
    w(f"    (via {_q(via_name)})")
    w(f"    (rule (width {_u(width)}) (clearance {_u(clearance)}) (clearance {_u(clearance)} (type default_smd)) "
      f"(clearance {_u(clearance / 2)} (type smd_smd)))")
    # Planes from poured polygons, only where the pour is the net's own area: a pour drawn over other nets' pads
    # (a board-wide ground flood) is cut back around them by Altium, but as a Specctra plane it would block every
    # other net on that layer. Those are left out and filled again after routing.
    for lid, p in plane_of.items():
        pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in p["outline_mm"])
        w(f"    (plane {_q(p['net'])} (polygon {name_of[lid]} 0 {pts}))")
    for lid, net in fill_layers.items():
        pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in doc.outline_poly)
        w(f"    (plane {_q(net)} (polygon {name_of[lid]} 0 {pts}))")
    regioned = {38 + n for n, *_ in opts.split_regions if 38 + n in internal}
    for lid, net in internal.items():
        if lid in regioned:
            continue  # its split regions below
        if net and net not in opts.skip_nets and doc.outline_mm:
            # a split plane without region shapes: its own net everywhere (approximate, but never a signal layer)
            pts = " ".join(f"{_u(q['x'])} {_u(q['y'])}" for q in doc.outline_mm)
            w(f"    (plane {_q(net)} (polygon {name_of[lid]} 0 {pts}))")
    m = opts.split_margin_mm
    for n, net, x0, y0, x1, y1 in opts.split_regions:
        lid = 38 + n
        if lid in internal and net not in opts.skip_nets and x1 - x0 > 2 * m and y1 - y0 > 2 * m:
            a, b, c, d = x0 + m, y0 + m, x1 - m, y1 - m
            w(f"    (plane {_q(net)} (polygon {name_of[lid]} 0 {_u(a)} {_u(b)} {_u(c)} {_u(b)} {_u(c)} {_u(d)} {_u(a)} {_u(d)}))")
    for p in (doc.polygons if opts.planes else []):
        if p["net"] and p["outline_mm"] and not p["shelved"]:
            lid = poly_layer(p)
            if lid in name_of and lid not in plane_of and p["net"] not in opts.skip_nets and not any(
                    (q.layer == lid or q.layer == MULTILAYER) and doc.net_name(q.net) != p["net"]
                    and _inside(q.x, q.y, p["outline_mm"]) for q in doc.pads):
                pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in p["outline_mm"])
                w(f"    (plane {_q(p['net'])} (polygon {name_of[lid]} 0 {pts}))")
    for r in doc.regions:
        if (r.is_keepout or r.is_board_cutout) and len(r.outline) >= 3 and r.polygon == NONE16:
            pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in r.outline)
            if r.is_board_cutout or r.layer not in name_of:
                for n in all_layers:
                    w(f"    (keepout \"\" (polygon {n} 0 {pts}))")
            else:
                w(f"    (keepout \"\" (polygon {name_of[r.layer]} 0 {pts}))")
        elif _is_copper_region(r, name_of) and (r.net == NONE16 or doc.net_name(r.net) in opts.skip_nets):
            # Netless copper is an obstacle. Netted copper regions (custom pad shapes) become pins of their
            # component below; as planes they would make Freerouting stop keeping other nets clear of that net.
            pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in r.outline)
            w(f"    (keepout \"\" (polygon {name_of[r.layer]} 0 {pts}))")
    for f in doc.fills:
        if f.layer in name_of:
            cx, cy = (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2
            hx, hy = abs(f.x2 - f.x1) / 2, abs(f.y2 - f.y1) / 2
            c, s = math.cos(math.radians(f.rotation)), math.sin(math.radians(f.rotation))
            quad = [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx, dy in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy))]
            pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in quad)
            w(f"    (keepout \"\" (polygon {name_of[f.layer]} 0 {pts}))")
    # Lines and arcs on Altium's Keep-Out layer (legacy 56) bar copper on every layer (e.g. a keep-out ring
    # drawn just inside the board edge): thin keep-out paths, one per copper layer.
    for t in doc.tracks:
        if t.layer == 56:
            for n in all_layers:
                w(f"    (keepout \"\" (path {n} {_u(max(t.width, 0.01))} {_u(t.x1)} {_u(t.y1)} {_u(t.x2)} {_u(t.y2)}))")
    for a in doc.arcs:
        if a.layer == 56:
            sweep = (a.end_angle - a.start_angle) % 360 or 360
            steps = max(2, int(sweep / 10))
            pts = []
            for i in range(steps + 1):
                ang = math.radians(a.start_angle + sweep * i / steps)
                pts.append(f"{_u(a.cx + a.radius * math.cos(ang))} {_u(a.cy + a.radius * math.sin(ang))}")
            for n in all_layers:
                w(f"    (keepout \"\" (path {n} {_u(max(a.width, 0.01))} {' '.join(pts)}))")
    # Copper must keep the outline clearance Altium checks, but Freerouting only applies its default clearance to
    # the board edge, and a second (signal) boundary stops it routing at all: keep-out strips along each edge.
    edge = rules["outline_clearance"] - clearance
    if doc.outline_mm and edge > 0:
        outer = [(p["x"], p["y"]) for p in doc.outline_mm]
        inner = _inset(outer, edge)
        for i in range(len(outer)):
            j = (i + 1) % len(outer)
            quad = [outer[i], outer[j], inner[j], inner[i], outer[i]]
            pts = " ".join(f"{_u(x)} {_u(y)}" for x, y in quad)
            for n in all_layers:
                w(f"    (keepout \"\" (polygon {n} 0 {pts}))")
    # Non-plated holes: no copper, but copper must keep the outline/NPTH clearance from them.
    for p in doc.pads:
        if p.hole > 0 and not p.plated:
            r = p.hole / 2 + rules["outline_clearance"] - clearance
            for n in all_layers:
                w(f"    (keepout \"\" (polygon {n} 0 {_circle(p.x, p.y, r)}))")
    for n, lid in layers:
        if n not in routable and lid not in power:
            # a layer we do not route on: no tracks, but vias must still pass through it - a plain `keepout`
            # blocks vias too, and with one on an inner layer no via is legal anywhere (Kame: 0 vias, 158 open)
            w(f"    (wire_keepout \"\" (polygon {n} 0 -1000000 -1000000 1000000 -1000000 1000000 1000000 -1000000 1000000))")
    w("    (control (via_at_smd off))")
    w("  )")

    # Components: one image per placed part (or free pad) at its own origin.
    padstacks: dict[str, str] = {}
    images: list[str] = []
    places: list[str] = []
    pin_ref: dict[int, str] = {}  # pad index -> "COMP-PIN"
    comp_pads: dict[int, list[int]] = defaultdict(list)
    for i, p in enumerate(doc.pads):
        comp_pads[p.component if p.component != NONE16 else -(i + 1)].append(i)

    def padstack(p) -> str:
        if p.layer == MULTILAYER:
            lyr = all_layers
        elif p.layer in name_of:
            lyr = [name_of[p.layer]]
        else:
            return ""
        sx, sy = p.size_x * 1000, p.size_y * 1000
        rot = p.rotation % 180
        shapes = []
        for ln in lyr:
            if p.shape == 1 and abs(sx - sy) < 1:  # round
                shapes.append(f"(shape (circle {ln} {max(sx, p.hole * 1000 + 100):.1f}))")
            elif p.shape == 1:  # oval -> path with round ends
                d = min(sx, sy)
                half = (max(sx, sy) - d) / 2
                ang = math.radians(p.rotation + (0 if sx >= sy else 90))
                dx, dy = half * math.cos(ang), half * math.sin(ang)
                shapes.append(f"(shape (path {ln} {d:.1f} {-dx:.1f} {-dy:.1f} {dx:.1f} {dy:.1f}))")
            elif rot in (0.0, 90.0):
                hx, hy = (sx / 2, sy / 2) if rot == 0 else (sy / 2, sx / 2)
                shapes.append(f"(shape (rect {ln} {-hx:.1f} {-hy:.1f} {hx:.1f} {hy:.1f}))")
            else:
                a = math.radians(p.rotation)
                c, s = math.cos(a), math.sin(a)
                pts = []
                for qx, qy in ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2), (-sx / 2, -sy / 2)):
                    pts.append(f"{qx * c - qy * s:.1f} {qx * s + qy * c:.1f}")
                shapes.append(f"(shape (polygon {ln} 0 {' '.join(pts)}))")
        key = "|".join(shapes)
        if key not in padstacks:
            padstacks[key] = f"PS{len(padstacks)}"
        return padstacks[key]

    # Netted copper regions (custom pad shapes): extra pins of their component, or of their own free image.
    comp_regions: dict[int, list] = defaultdict(list)
    region_pins: dict[int, list[str]] = defaultdict(list)  # net -> pin refs
    for k, r in enumerate(doc.regions):
        if _is_copper_region(r, name_of) and r.net != NONE16 and doc.net_name(r.net) not in opts.skip_nets:
            comp_regions[r.component if r.component != NONE16 else -(100000 + k)].append(r)

    n_comp = 0
    for key in list(comp_pads) + [k for k in comp_regions if k not in comp_pads]:
        idxs = comp_pads.get(key, [])
        if key >= 0:
            comp = doc.components[key]
            cname, ox, oy = _ident(comp.designator), comp.x_mm, comp.y_mm
        elif idxs:
            p0 = doc.pads[idxs[0]]
            cname, ox, oy = f"FREEPAD{-key}", p0.x, p0.y
        else:
            r0 = comp_regions[key][0]
            cname, ox, oy = f"FREECU{-key - 100000}", r0.outline[0][0], r0.outline[0][1]
        pins, seen = [], defaultdict(int)
        for j, r in enumerate(comp_regions.get(key, [])):
            cx = sum(x for x, _ in r.outline) / len(r.outline)
            cy = sum(y for _, y in r.outline) / len(r.outline)
            ring = r.outline + r.outline[:1]
            shape = f"(shape (polygon {name_of[r.layer]} 0 " + " ".join(f"{_u(x - cx)} {_u(y - cy)}" for x, y in ring) + "))"
            if shape not in padstacks:
                padstacks[shape] = f"PS{len(padstacks)}"
            pid = f"CU{j + 1}"
            pins.append(f"      (pin {padstacks[shape]} {_q(pid)} {_u(cx - ox)} {_u(cy - oy)})")
            region_pins[r.net].append(f"{cname}-{pid}")
        for i in idxs:
            p = doc.pads[i]
            if p.hole > 0 and not p.plated and p.net == NONE16:
                continue  # NPTH: exported as a keep-out above
            ps = padstack(p)
            if not ps:
                continue
            base = _ident(p.name or str(i))
            seen[base] += 1
            pid = base if seen[base] == 1 else f"{base}@{seen[base]}"
            pins.append(f"      (pin {ps} {_q(pid)} {_u(p.x - ox)} {_u(p.y - oy)})")
            pin_ref[i] = f"{cname}-{pid}"
        if not pins:
            continue
        n_comp += 1
        images.append(f"    (image {_q('IMG_' + cname)}\n" + "\n".join(pins) + "\n    )")
        places.append(f"    (component {_q('IMG_' + cname)} (place {_q(cname)} {_u(ox)} {_u(oy)} front 0))")

    w("  (placement")
    out.extend(places)
    w("  )")
    w("  (library")
    out.extend(images)
    for key, name in padstacks.items():
        w(f"    (padstack {name} {' '.join(key.split('|'))} (attach off))")
    vshapes = " ".join(f"(shape (circle {n} {via_d * 1000:.1f}))" for n in all_layers)
    w(f"    (padstack {_q(via_name)} {vshapes} (attach off))")
    # existing vias keep their real size (a pre-router's 0.4 mm vias drawn as the 0.35 mm routing via let
    # Freerouting come 0.025 mm too close to them) and their own padstack with (attach on): a footprint's
    # thermal vias sit in its pad by design, and with (attach off) Freerouting counts each as a violation
    existing_via: dict[tuple[float, float], str] = {}
    if opts.keep_existing:
        for v in doc.vias:
            key = (round(v.diameter, 4), round(v.hole, 4))
            if key not in existing_via and v.net != NONE16:
                existing_via[key] = f"Via[0-{len(layers) - 1}]_{key[0] * 1000:.0f}:{key[1] * 1000:.0f}_um_fixed"
                shapes = " ".join(f"(shape (circle {n} {key[0] * 1000:.1f}))" for n in all_layers)
                w(f"    (padstack {_q(existing_via[key])} {shapes} (attach on))")
    w("  )")

    # Network.
    net_pins: dict[int, list[str]] = defaultdict(list)
    for i, p in enumerate(doc.pads):
        if p.net != NONE16 and i in pin_ref:
            net_pins[p.net].append(pin_ref[i])
    for ni, refs in region_pins.items():
        net_pins[ni].extend(refs)
    w("  (network")
    n_nets = 0
    skip = set(opts.skip_nets)
    for ni, pins in net_pins.items():
        name = doc.net_name(ni)
        if not name or name in skip or len(pins) < 1:
            continue
        n_nets += 1
        w(f"    (net {_q(name)} (pins {' '.join(pins)}))")  # pin refs unquoted: Freerouting mis-parses quoted refs
    # Widths: the board's InNet/InNetClass Width rules (preferred width), then explicit net_widths_mm on top.
    # Each net goes in exactly one class, grouped by width.
    rule_w = net_width_rules(doc)
    net_w = {n: p for n, (p, _) in rule_w.items()}
    net_w.update(opts.net_widths_mm)
    routed = [doc.net_name(n) for n in net_pins if doc.net_name(n) and doc.net_name(n) not in skip]
    fixed: list[str] = []
    if opts.route_only is not None:
        only = set(opts.route_only)
        fixed = [n for n in routed if n not in only]
        routed = [n for n in routed if n in only]
    by_width: dict[float, list[str]] = defaultdict(list)
    for n in routed:
        if n in net_w and abs(net_w[n] - width) > 1e-6:
            by_width[round(net_w[n], 4)].append(n)
    special = {n for ns in by_width.values() for n in ns}
    if fixed:
        w(f"    (class {FIXED_CLASS} {' '.join(_q(n) for n in fixed)}")
        w(f"      (circuit (use_via {_q(via_name)})) (rule (width {_u(width)}) (clearance {_u(clearance)})))")
    plain = [n for n in routed if n not in special]
    if plain or not special:
        w(f"    (class default {' '.join(_q(n) for n in plain)}")
        w(f"      (circuit (use_via {_q(via_name)})) (rule (width {_u(width)}) (clearance {_u(clearance)})))")
    for wmm, nets in sorted(by_width.items()):
        w(f"    (class {_q(f'W_{wmm * 1000:.0f}um')} {' '.join(_q(n) for n in nets)} (circuit (use_via {_q(via_name)})) "
          f"(rule (width {_u(wmm)}) (clearance {_u(clearance)})))")
    w("  )")

    # Existing copper (kept).
    w("  (wiring")
    if opts.keep_existing:
        for t in doc.tracks:
            if t.layer in name_of and t.net != NONE16 and t.polygon == NONE16 and t.component == NONE16:
                w(f"    (wire (path {name_of[t.layer]} {_u(t.width)} {_u(t.x1)} {_u(t.y1)} {_u(t.x2)} {_u(t.y2)}) "
                  f"(net {_q(doc.net_name(t.net))}) (type protect))")
        placed: set[tuple[int, int]] = set()
        for v in doc.vias:
            k = (round(v.x * 1000), round(v.y * 1000))
            if v.net != NONE16 and k not in placed:
                placed.add(k)
                ps = existing_via.get((round(v.diameter, 4), round(v.hole, 4)), via_name)
                w(f"    (via {_q(ps)} {_u(v.x)} {_u(v.y)} (net {_q(doc.net_name(v.net))}) (type protect))")
    w("  )")
    w(")")
    return DsnExport("\n".join(out) + "\n", {n: lid for n, lid in layers}, n_nets, n_comp,
                     {"width_mm": width, "min_width_mm": rules["min_width"], "clearance_mm": clearance,
                      "outline_clearance_mm": rules["outline_clearance"], "via_diameter_mm": via_d, "via_hole_mm": via_h,
                      "net_min_width_mm": {n: mn for n, (_, mn) in rule_w.items()}})


# ---------------------------------------------------------------------- SES import


def _sexpr(text: str):
    tokens = re.findall(r'"(?:[^"]*)"|\(|\)|[^\s()]+', text)
    stack: list[list] = [[]]
    for t in tokens:
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t[1:-1] if t.startswith('"') else t)
    return stack[0][0] if stack[0] else []


def _find(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


@dataclass
class SesResult:
    tracks: list[dict]  # x1,y1,x2,y2,width,layer(dsn name),net (mm)
    vias: list[dict]  # x,y,diameter,hole,net (mm)
    nets: int


def parse_ses(path: Path, via_hole_mm: float) -> SesResult:
    root = _sexpr(Path(path).read_text(encoding="utf-8", errors="replace"))
    routes = next(iter(_find(root, "routes")), [])
    res = next(iter(_find(routes, "resolution")), ["resolution", "um", "10"])
    unit, per = res[1].lower(), float(res[2])
    scale = {"um": 0.001, "mm": 1.0, "mil": MM_PER_MIL, "inch": MM_PER_MIL * 1000}.get(unit, 0.001) / per  # -> mm
    via_dia: dict[str, float] = {}
    for lib in _find(routes, "library_out"):
        for ps in _find(lib, "padstack"):
            dias = [float(sh[1][2]) for sh in _find(ps, "shape") if len(sh) > 1 and isinstance(sh[1], list)
                    and sh[1][0] == "circle"]
            if dias:
                via_dia[ps[1]] = max(dias) * scale
    tracks, vias, nets = [], [], 0
    for no in _find(routes, "network_out"):
        for net in _find(no, "net"):
            nets += 1
            name = net[1]
            for wire in _find(net, "wire"):
                for pth in _find(wire, "path"):
                    layer, width = pth[1], float(pth[2]) * scale
                    xy = [float(v) * scale for v in pth[3:] if not isinstance(v, list)]
                    pts = list(zip(xy[0::2], xy[1::2]))
                    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                        if abs(x1 - x2) > 1e-6 or abs(y1 - y2) > 1e-6:
                            tracks.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": width, "layer": layer, "net": name})
            for via in _find(net, "via"):
                vias.append({"x": float(via[2]) * scale, "y": float(via[3]) * scale,
                             "diameter": via_dia.get(via[1], via_hole_mm * 2), "hole": via_hole_mm, "net": name})
    return SesResult(tracks, vias, nets)
