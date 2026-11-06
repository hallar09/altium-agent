# SPDX-License-Identifier: Apache-2.0
"""Pours for the supplies, planned before routing.

The router leaves a net with a pour or a plane to its copper area: each
pad gets a via to it, pours are stitched, and the net is never routed as
tracks. But nothing made the pours, so a board without them had its
ground and supplies routed as long tracks. This plans them the way the
people who laid out Altium's examples did (all five benchmark boards):

* ground poured over the whole board on every copper layer (WiFi, Kame
  and SL1 do exactly that; a pour on a signal layer fills round the
  tracks once routed);
* a layer named for a supply (``int1_power``, ``VDD4``, ``3V3``) gets
  that supply over the whole board instead of ground, and a layer named
  for ground or "power" with no supply in its name gets ground or the
  biggest supply;
* the other supplies get an island round their pads on a power layer,
  over the main one (a higher pour priority), or stay tracks on a board
  with no power layer;
* an internal plane layer with no net is given ground first, then the
  biggest supply (``pcb_set_plane_net``).

The plan is data: ``apply_to`` puts it on a board read from file (for a
routing session and the trial route), and each pour carries the live
call that makes it in Altium.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..offline.files.pcbdoc import NONE16, PcbDocument

GND_LAYER = re.compile(r"gnd|ground|vss", re.IGNORECASE)
PWR_LAYER = re.compile(r"pwr|power|vcc|vdd|supply|\dv\d", re.IGNORECASE)


def _token(lid: int) -> str:
    return "TOP" if lid == 1 else "BOTTOM" if lid == 32 else f"MID{lid - 1}"


def _inset(poly: list[tuple[float, float]], d: float) -> list[tuple[float, float]]:
    """The outline pulled in by ``d`` (a mitred offset; fine for the convex-ish
    outlines boards have; the pour's own board-edge rule does the rest)."""
    import math

    n = len(poly)
    if n < 3 or d <= 0:
        return list(poly)
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1])) / 2
    s = 1 if area > 0 else -1          # CCW: inward is to the left
    out = []
    for i in range(n):
        (x0, y0), (x1, y1), (x2, y2) = poly[i - 1], poly[i], poly[(i + 1) % n]
        e1 = (x1 - x0, y1 - y0)
        e2 = (x2 - x1, y2 - y1)
        l1, l2 = math.hypot(*e1) or 1.0, math.hypot(*e2) or 1.0
        n1 = (-e1[1] / l1 * s, e1[0] / l1 * s)
        n2 = (-e2[1] / l2 * s, e2[0] / l2 * s)
        mx, my = n1[0] + n2[0], n1[1] + n2[1]
        m = math.hypot(mx, my)
        if m < 1e-9:
            mx, my, m = n1[0], n1[1], 1.0
        mx, my = mx / m, my / m
        cos = max(0.3, mx * n1[0] + my * n1[1])
        out.append((round(x1 + mx * d / cos, 4), round(y1 + my * d / cos, 4)))
    return out


def _supply_nets(doc: PcbDocument) -> tuple[list[str], list[str]]:
    """(ground nets, other supplies by pad count, biggest first)."""
    from ..placement.placer import is_ground, is_supply_net

    count: Counter = Counter(doc.net_name(p.net) for p in doc.pads if p.net != NONE16)
    count.pop("", None)
    gnd = sorted((n for n in count if is_ground(n)), key=lambda n: -count[n])
    sup = sorted((n for n in count if not is_ground(n) and is_supply_net(n, count[n]) and count[n] >= 3),
                 key=lambda n: -count[n])
    return gnd, sup


def _named_supply(layer_name: str, supplies: list[str]) -> str | None:
    """The supply a layer is named for (``VDD4`` -> VDD, ``int1_3V3`` -> 3V3)."""
    key = re.sub(r"[^A-Z0-9]", "", layer_name.upper())
    best = None
    for s in supplies:
        k = re.sub(r"[^A-Z0-9]", "", s.upper())
        if len(k) >= 2 and k in key and (best is None or len(k) > len(best[1])):
            best = (s, k)
    return best[0] if best else None


def plan_pours(doc: PcbDocument, *, edge_mm: float = 0.3, island_margin_mm: float = 1.0,
               ground_every_layer: bool = True) -> dict[str, Any]:
    """A pour plan for ``doc``: what goes on which layer, and what already does."""
    from ..bench.connectivity import copper_order
    from ..routing.fanout import plane_nets

    board = [tuple(q) for q in doc.outline_poly or []]
    if len(board) < 3:
        return {"pours": [], "planes": [], "notes": ["the board has no outline: draw it first"]}
    gnd, sup = _supply_nets(doc)
    have = plane_nets(doc)
    notes: list[str] = []
    if have:
        notes.append(f"already carried by pours or planes, left as they are: {', '.join(sorted(have))}")
    ground = next((g for g in gnd if g not in have), None) or (gnd[0] if gnd else None)
    order = [lid for lid in copper_order(doc) if lid <= 32]
    names = {}
    for s in doc.stack:
        if s.kind in ("copper", "plane"):
            from ..bench.connectivity import legacy_of

            lid = legacy_of(s.layer_id)
            if lid is not None:
                names[lid] = s.name
    planes: list[dict] = []
    for s in doc.stack:
        if s.kind == "plane":
            net = doc.board.get(f"PLANE{s.layer_id & 0xFFFF}NETNAME", "").strip()
            if not net or net == "(No Net)":
                pick = ground if ground and ground not in {p["net"] for p in planes} and ground not in have \
                    else next((x for x in sup if x not in have and x not in {p["net"] for p in planes}), None)
                if pick:
                    planes.append({"layer": s.name, "net": pick,
                                   "apply": {"tool": "pcb_set_plane_net", "arguments": {"layer": s.name, "net": pick}}})
    planned = have | {p["net"] for p in planes}
    pours: list[dict] = []
    full = _inset(board, edge_mm)
    outer = {order[0], order[-1]} if order else set()
    power_layers: list[int] = []
    for lid in order:
        name = names.get(lid, _token(lid))
        named = _named_supply(name, sup)
        if named:
            net = named
            power_layers.append(lid)
        elif PWR_LAYER.search(name) and not GND_LAYER.search(name) and sup:
            net = sup[0]
            power_layers.append(lid)
        elif GND_LAYER.search(name) or (ground_every_layer and ground) or lid in outer:
            net = ground
        else:
            net = None
        if not net or net in have:
            continue
        pours.append({"net": net, "layer": _token(lid), "layer_name": name, "kind": "full",
                      "outline_mm": full, "pour_index": 0})
    # the other supplies: islands round their pads on the power layer(s)
    main = {p["net"] for p in pours if p["kind"] == "full" and p["net"] != ground}
    rest = [s for s in sup if s not in main and s not in planned]
    if rest and not power_layers:
        notes.append(f"no power layer: {', '.join(rest[:8])} stay tracks (or give them a layer named for them)")
    pads: dict[str, list[tuple[float, float]]] = {}
    for p in doc.pads:
        if p.net != NONE16:
            pads.setdefault(doc.net_name(p.net), []).append((p.x, p.y))
    bx0, by0 = min(x for x, _ in full), min(y for _, y in full)
    bx1, by1 = max(x for x, _ in full), max(y for _, y in full)
    for k, net in enumerate(rest if power_layers else []):
        pts = pads.get(net) or []
        if len(pts) < 2:
            continue
        x0 = max(bx0, min(x for x, _ in pts) - island_margin_mm)
        y0 = max(by0, min(y for _, y in pts) - island_margin_mm)
        x1 = min(bx1, max(x for x, _ in pts) + island_margin_mm)
        y1 = min(by1, max(y for _, y in pts) + island_margin_mm)
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            continue
        lid = power_layers[k % len(power_layers)]
        pours.append({"net": net, "layer": _token(lid), "layer_name": names.get(lid, _token(lid)),
                      "kind": "island", "outline_mm": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                      "pour_index": 1 + k})
    for i, p in enumerate(pours):
        p["name"] = f"AA_{p['net']}_{p['layer']}_{i + 1}"
    return {"pours": pours, "planes": planes, "ground": ground, "supplies": sup[:12],
            "power_layers": [names.get(lid, _token(lid)) for lid in power_layers], "notes": notes}


def apply_to(doc: PcbDocument, plan: dict) -> PcbDocument:
    """``doc`` with the plan's pours and plane nets on it (the file is not edited)."""
    import copy

    out = copy.copy(doc)
    out.polygons = list(doc.polygons or []) + [
        {"name": p["name"], "layer": p["layer"], "net": p["net"], "pour_index": p.get("pour_index", 0),
         "shelved": False, "type": "Polygon", "hatch": "Solid", "pour_over_same_net": True,
         "remove_dead_copper": True, "outline_mm": [list(q) for q in p["outline_mm"]], "planned": True}
        for p in plan.get("pours") or []]
    if plan.get("planes"):
        out.board = dict(doc.board)
        by_name = {s.name: s for s in doc.stack}
        for pl in plan["planes"]:
            s = by_name.get(pl["layer"])
            if s is not None:
                out.board[f"PLANE{s.layer_id & 0xFFFF}NETNAME"] = pl["net"]
    return out
