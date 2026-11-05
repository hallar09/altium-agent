# SPDX-License-Identifier: Apache-2.0
"""A saved board as the in-house router's model, without Altium.

The in-house engines (layout/) work on a ``LayoutBoard``: mils, Altium's
layer names, typed rules. Until now one came only from a live Altium
(layout/read_altium.py). This builds the same model from the offline
reader's ``PcbDocument``, so routing a net, checking a board with the exact
DRC, and the interactive sessions all work from the saved file.

Coordinates stay in the board's own frame (from its origin), as the offline
reader gives them, times 39.37 for mils. Rules are written with Altium's own
descriptor wording ("Clearance Constraint (Gap=...)"), so layout/rules.py
reads them as it reads a live board's.
"""

from __future__ import annotations

from ..layout.model import (
    Arc, Component, DiffPair, Layer, LayoutBoard, Pad, PadCopper, Region, Rule, Track, Via, BOTTOM, TOP,
)
from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..units import MILS_PER_MM

MULTI = 74
KEEPOUT = 56
_SHAPES = {1: "round", 2: "rect", 3: "octagon", 9: "roundrect"}


def layer_token(lid: int) -> str:
    """Altium's name for a legacy copper layer id."""
    if lid == 1:
        return "TopLayer"
    if lid == 32:
        return "BottomLayer"
    if 2 <= lid <= 31:
        return f"MidLayer{lid - 1}"
    if 39 <= lid <= 54:
        return f"InternalPlane{lid - 38}"
    return {KEEPOUT: "KeepOutLayer", MULTI: "MultiLayer"}.get(lid, f"Layer{lid}")


def legacy_id(token: str) -> int | None:
    """The legacy id of a copper layer name (the inverse of layer_token)."""
    if token == "TopLayer":
        return 1
    if token == "BottomLayer":
        return 32
    if token.startswith("MidLayer"):
        return int(token[8:]) + 1
    if token.startswith("InternalPlane"):
        return int(token[13:]) + 38
    return None


def _m(v: float) -> float:
    return v * MILS_PER_MM


def _pts(pts) -> list[tuple[float, float]]:
    return [(_m(p[0]), _m(p[1])) for p in pts]


def _layers(doc: PcbDocument) -> tuple[list[Layer], list[str]]:
    out: list[Layer] = []
    for s in doc.stack:
        if s.kind == "copper":
            lid = 32 if s.layer_id == 0x0100FFFF else s.layer_id - 0x01000000
            out.append(Layer(layer_token(lid), "signal", len(out), "", _m(s.copper_thickness_mm or 0.035)))
        elif s.kind == "plane":
            n = s.layer_id & 0xFFFF
            net = doc.board.get(f"PLANE{n}NETNAME", "").strip()
            out.append(Layer(layer_token(38 + n), "plane", len(out), "" if net == "(No Net)" else net,
                             _m(s.copper_thickness_mm or 0.035)))
    if not out:
        out = [Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)]
    signal = [l.name for l in out if l.kind == "signal"]
    return out, signal


def _rules(doc: PcbDocument) -> list[Rule]:
    """The board's rules in the descriptor wording layout/rules.py types
    them by; values in mils under the names its readers ask for."""
    from ..routing.specctra import _rule_defaults

    d = _rule_defaults(doc)
    out: list[Rule] = []
    for r in doc.rules or []:
        kind, v = r.get("kind", ""), {k[:-3] if k.endswith("_mm") else k: _m(x)
                                      for k, x in (r.get("values") or {}).items()}
        name, s1, s2 = r.get("name", ""), r.get("scope1") or "All", r.get("scope2") or "All"
        if kind == "Clearance":
            values, text = {"gap": v.get("gap", _m(d["clearance"]))}, "Clearance Constraint"
        elif kind == "Width":
            values = {"min": v.get("minlimit", 0.0), "preferred": v.get("preferedwidth", 0.0),
                      "max": v.get("maxlimit", 0.0)}
            text = "Width Constraint"
        elif kind == "RoutingVias":
            lo, hi = v.get("minwidth", 0.0), v.get("maxwidth", 0.0)
            hlo, hhi = v.get("minholewidth", 0.0), v.get("maxholewidth", 0.0)
            pref = min(max(_m(d["via_d"]), lo), hi) if hi else max(_m(d["via_d"]), lo)
            hpref = min(max(_m(d["via_h"]), hlo), hhi) if hhi else max(_m(d["via_h"]), hlo)
            values = {"preferedwidth": pref, "minwidth": lo, "maxwidth": hi,
                      "preferredholewidth": hpref, "minholewidth": hlo, "maxholewidth": hhi}
            text = "Routing Via"
        elif kind == "BoardOutlineClearance":
            values, text = {"gap": v.get("gap", 0.0)}, "Board Outline Clearance"
        elif kind == "HoleToHoleClearance":
            values, text = {"gap": v.get("gap", 0.0)}, "Hole To Hole Clearance"
        elif kind == "DiffPairsRouting":
            values, text = v, "Differential Pairs Routing"
        elif kind == "PlaneClearance":
            values, text = v, "Power Plane Clearance"
        elif kind == "PlaneConnect":
            values, text = v, "Power Plane Connect"
        elif kind == "PolygonConnect":
            values, text = v, "Polygon Connect"
        elif kind == "MinimumAnnularRing":
            values, text = v, "Minimum Annular Ring"
        elif kind == "HoleSize":
            values, text = v, "Hole Size Constraint"
        else:
            continue
        out.append(Rule(name, kind.lower(), s1, s2, int(r.get("priority") or 1), bool(r.get("enabled", True)),
                        values, f"{text} ({name})"))
    if not any(r.kind == "clearance" for r in out):
        out.append(Rule("Clearance", "clearance", values={"gap": _m(d["clearance"])},
                        descriptor="Clearance Constraint (default)"))
    if not any(r.kind == "width" for r in out):
        out.append(Rule("Width", "width", values={"min": _m(d["min_width"]), "preferred": _m(d["width"]),
                                                 "max": _m(d["width"]) * 4},
                        descriptor="Width Constraint (default)"))
    if not any(r.kind == "routingvias" for r in out):
        out.append(Rule("RoutingVias", "routingvias", values={"preferedwidth": _m(d["via_d"]),
                                                              "preferredholewidth": _m(d["via_h"])},
                        descriptor="Routing Via (default)"))
    return out


def layout_board(doc: PcbDocument, *, poured_copper: bool = False) -> LayoutBoard:
    """``doc`` as a LayoutBoard (mils, the board's own frame).

    ``poured_copper`` adds what pours left on a finished board. The reader
    does not keep the voids a pour has round other nets' pads, so that
    copper shows as clashing with them: leave it out (the default) for
    routing and clearance checks, and count pours by their outlines
    instead (bench/connectivity.py).
    """
    layers, signal = _layers(doc)
    b = LayoutBoard(name=doc.path.name, source=str(doc.path), layers=layers,
                    outline=_pts(doc.outline_poly or []))
    refs = {c.index: c.designator for c in doc.components}

    def net(n: int) -> str:
        return doc.net_name(n) if n != NONE16 else ""

    for c in doc.components:
        b.components.append(Component(c.designator, c.footprint, _m(c.x_mm), _m(c.y_mm), c.rotation,
                                      BOTTOM if c.side == "bottom" else TOP, c.locked, _m(c.height_mm or 0),
                                      c.comment))
    for p in doc.pads:
        shape = _SHAPES.get(p.shape, "rect")
        if p.hole > 0 or p.layer == MULTI:
            on = signal
        else:
            on = [layer_token(p.layer)]
        copper = [PadCopper(lay, shape, _m(p.size_x), _m(p.size_y), float(p.corner_pct or 0)) for lay in on]
        b.pads.append(Pad(refs.get(p.component, ""), p.name, _m(p.x), _m(p.y), p.rotation, net(p.net), copper,
                          _m(p.hole), plated=p.plated))
    for t in doc.tracks:
        if 1 <= t.layer <= 32 and t.polygon != NONE16 and t.net != NONE16:
            continue                     # poured copper: its region, below, stands for it
        if 1 <= t.layer <= 32 and t.polygon == NONE16:
            b.tracks.append(Track(layer_token(t.layer), _m(t.x1), _m(t.y1), _m(t.x2), _m(t.y2), _m(t.width),
                                  net(t.net), refs.get(t.component, ""), bool(t.keepout)))
        elif t.layer == KEEPOUT:
            for lay in signal:
                b.tracks.append(Track(lay, _m(t.x1), _m(t.y1), _m(t.x2), _m(t.y2), _m(t.width), "", "", True))
    for a in doc.arcs:
        if 1 <= a.layer <= 32 and a.polygon == NONE16:
            b.arcs.append(Arc(layer_token(a.layer), _m(a.cx), _m(a.cy), _m(a.radius), a.start_angle, a.end_angle,
                              _m(a.width), net(a.net), refs.get(a.component, "")))
        elif a.layer == KEEPOUT:
            for lay in signal:
                b.arcs.append(Arc(lay, _m(a.cx), _m(a.cy), _m(a.radius), a.start_angle, a.end_angle,
                                  _m(a.width), "", "", True))
    for v in doc.vias:
        # Blind and buried vias span their own layers; anything the stack
        # does not know is treated as through, as every via used to be.
        low, high = layer_token(v.layer_start), layer_token(v.layer_end)
        if low not in signal or high not in signal:
            low, high = signal[0], signal[-1]
        b.vias.append(Via(_m(v.x), _m(v.y), _m(v.diameter), _m(v.hole), low, high, net(v.net),
                          comp=refs.get(v.component, "")))
    for r in doc.regions:
        if len(r.outline) < 3:
            continue
        if r.polygon != NONE16:
            # Poured copper, as the pour left it (a finished board): it joins
            # its net like a track does. A stripped board has none.
            if poured_copper and r.kind == 0 and 1 <= r.layer <= 32 and r.net != NONE16:
                b.regions.append(Region(layer_token(r.layer), _pts(r.outline), [], net(r.net), "pour", False, "",
                                        f"polygon:{r.polygon}"))
            continue
        if r.is_board_cutout:
            b.cutouts.append(_pts(r.outline))
        elif r.is_keepout:
            for lay in (signal if r.layer in (MULTI, KEEPOUT) else [layer_token(r.layer)]):
                b.regions.append(Region(lay, _pts(r.outline), [], "", "keepout", True, refs.get(r.component, ""),
                                        "region"))
        elif 1 <= r.layer <= 32:
            b.regions.append(Region(layer_token(r.layer), _pts(r.outline), [], net(r.net),
                                    "copper" if r.kind == 0 else "cutout", False, refs.get(r.component, ""),
                                    "region"))
    for f in doc.fills:
        if 1 <= f.layer <= 32:
            from ..layout import geom

            cx, cy = _m((f.x1 + f.x2) / 2), _m((f.y1 + f.y2) / 2)
            shape = geom.pad_shape(cx, cy, _m(abs(f.x2 - f.x1)), _m(abs(f.y2 - f.y1)), "rect", f.rotation)
            b.regions.append(Region(layer_token(f.layer), list(shape.pts), [], net(f.net), "copper", False,
                                    refs.get(f.component, ""), "fill"))
    from ..bench.connectivity import _poly_layer

    for p in doc.polygons or []:
        lid = _poly_layer(p.get("layer", ""))
        if lid and p.get("net") and len(p.get("outline_mm") or []) >= 3 and not p.get("shelved"):
            b.regions.append(Region(layer_token(lid), _pts(p["outline_mm"]), [], p["net"], "pour_boundary",
                                    False, "", f"polygon:{p.get('name', '')}"))
    for c in doc.classes or []:
        if str(c.get("kind", "")).lower() in ("net", "0") and c.get("members") and not c.get("superclass"):
            b.net_classes[c["name"]] = list(c["members"])
    for dp in doc.diff_pairs or []:
        if dp.get("positive") and dp.get("negative"):
            b.diff_pairs.append(DiffPair(dp.get("name", ""), dp["positive"], dp["negative"]))
    b.rules = _rules(doc)
    b.meta = {"from": "pcbdoc", "units": "mil", "frame": "board origin"}
    return b
