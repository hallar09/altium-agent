# SPDX-License-Identifier: Apache-2.0
"""AI-driven routing: the agent routes each net; the tools lay and check it.

The agent decides the order, what goes first, which nets get which width
and via, and the path where it matters. Each action is one of:

* ``route``  - the in-house engine lays the named nets, every other net's
  copper fixed, at the width and via the agent gives;
* ``draw``   - the agent's own path, point by point, a via wherever the
  layer changes (a pair, an RF feed, a sense line, a power run);
* ``fanout`` / ``stitch`` - power and ground to their planes, pours joined;
* ``ripup`` / ``undo``    - take back a net, or the last action.

After each, ``check`` says what is still open (by the copper itself, pours
and planes counted) and what breaks a clearance (the in-house exact DRC on
the rules from the file), and ``view`` draws it. The state is a session
file; ``export`` turns it into the SES route_import_ses writes to Altium.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..offline.errors import InvalidArgument
from ..offline.files.pcbdoc import PcbDocument, read_pcbdoc
from . import session as S

KIND = "route"


def _added(data: dict):
    from ..routing.fanout import FanoutResult

    return FanoutResult(tracks=list(data["added"]["tracks"]), vias=list(data["added"]["vias"]))


def _doc(data: dict) -> PcbDocument:
    from ..routing.fanout import with_result

    return with_result(read_pcbdoc(Path(data["source"])), _added(data))


def _rules(doc: PcbDocument) -> dict:
    from ..routing.specctra import _rule_defaults

    r = _rule_defaults(doc)
    return {"clearance_mm": r["clearance"], "width_mm": r["width"], "min_width_mm": r["min_width"],
            "via_diameter_mm": r["via_d"], "via_hole_mm": r["via_h"], "outline_clearance_mm": r["outline_clearance"]}


def _open(doc: PcbDocument) -> dict[str, int]:
    """Net -> connections still to make, by the copper (pours and planes count)."""
    from ..bench import connectivity as C

    return {n: r.missing for n, r in C.connect(C.copper_of(doc)).items() if r.missing}


#: Violation rows a route/draw/fanout reply carries; route_session_check(detail="full") gives the rest.
#: Every row costs the agent tokens on every call, and the first few say
#: what is wrong as well as forty do.
REPLY_ROWS = 8


def _by_rule(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r.get("rule", "?")] = out.get(r.get("rule", "?"), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _violations(doc: PcbDocument, nets: set[str] | None, limit: int = 40,
                counts: dict | None = None) -> tuple[int, list[dict]]:
    from ..layout.drc import run_drc
    from .board_model import layout_board

    rep = run_drc(layout_board(doc))
    rows = []
    for v in rep.violations:
        d = v.as_dict()
        d["at_mm"] = [round(v.at[0] / 39.37007874, 3), round(v.at[1] / 39.37007874, 3)]
        d.pop("at", None)
        d["gap_mm"], d["required_mm"] = round(v.gap / 39.37007874, 4), round(v.required / 39.37007874, 4)
        d.pop("gap", None)
        d.pop("required", None)
        if nets is None or any(n and (f":{n}#" in v.a or f":{n}#" in v.b) for n in nets):
            rows.append(d)
    if counts is not None:
        counts.update(matching=len(rows), by_rule=_by_rule(rows))
    return len(rep.violations), rows[:limit]


def _record(path: Path, data: dict, action: str, tracks: list[dict], vias: list[dict]) -> None:
    data["added"]["tracks"].extend(tracks)
    data["added"]["vias"].extend(vias)
    data["history"].append({"action": action, "tracks": len(tracks), "vias": len(vias)})
    S.save(path, data)


def _report(doc: PcbDocument, nets: set[str] | None, tracks: list[dict], vias: list[dict]) -> dict:
    still = _open(doc)
    counts: dict = {}
    total, rows = _violations(doc, nets, limit=REPLY_ROWS, counts=counts)
    out = {"added": {"tracks": len(tracks), "vias": len(vias),
                     "length_mm": round(sum(math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"]) for t in tracks), 2)},
           "violations_on_these_nets": rows, "violations_on_board": total,
           "open_connections_on_board": sum(still.values())}
    if counts.get("matching", 0) > len(rows):
        out["violations_on_these_nets_total"] = counts["matching"]
        out["violations_by_rule"] = counts["by_rule"]
    if nets is not None:
        out["still_open"] = {n: still[n] for n in sorted(nets) if n in still}
        out["done"] = sorted(n for n in nets if n not in still)
    return out


def start(project: str | None = None, pcb: str | None = None, name: str | None = None) -> dict[str, Any]:
    """Open a routing session on the saved board: its rules, layers, and every
    net by tier, with what is still open."""
    from ..placement.service import _owner, load_board
    from ..routing.broker import classify
    from ..routing.fanout import plane_nets

    b = load_board(project, pcb)
    data = {"source": str(b.doc.path), "project": str(b.project) if b.project else None,
            "origin_mil": list(b.doc.origin_mil), "added": {"tracks": [], "vias": []}}
    path = S.new(KIND, _owner(b), name, data)
    doc = b.doc
    c = classify(doc)
    still = _open(doc)
    layers = [{"layer": s.name, "kind": s.kind} for s in doc.stack if s.kind in ("copper", "plane")]
    return {"session": str(path), "rules": _rules(doc), "layers": layers, "planes_and_pours": sorted(plane_nets(doc)),
            "tiers": c["tiers"], "pairs": [{"name": n, "positive": p, "negative": q} for n, p, q in c["pairs"]],
            "open_connections": sum(still.values()), "open_nets": len(still),
            "next": "choose the rules per class, then route_session_fanout (power first), route_session_route "
                    "or route_session_draw per net, route_session_check and route_session_view as you go"}


def _width_rules(lb, nets: list[str], width_mm: float | None, via_d: float | None, via_h: float | None):
    from ..layout.model import Rule
    from ..units import MILS_PER_MM

    for n in nets:
        scope = f"InNet('{n}')"
        if width_mm:
            w = width_mm * MILS_PER_MM
            lb.rules.append(Rule(f"session width {n}", "width", scope, "All", 0, True,
                                 {"min": w, "preferred": w, "max": w}, "Width Constraint (session)"))
        if via_d or via_h:
            v = {}
            if via_d:
                v.update(preferedwidth=via_d * MILS_PER_MM, minwidth=via_d * MILS_PER_MM, maxwidth=via_d * MILS_PER_MM)
            if via_h:
                v.update(preferredholewidth=via_h * MILS_PER_MM, minholewidth=via_h * MILS_PER_MM,
                         maxholewidth=via_h * MILS_PER_MM)
            lb.rules.append(Rule(f"session via {n}", "routingvias", scope, "All", 0, True, v, "Routing Via (session)"))


def route(session: str, nets: list[str], width_mm: float | None = None, via_diameter_mm: float | None = None,
          via_hole_mm: float | None = None) -> dict[str, Any]:
    """The in-house engine routes ``nets``; everything else stays as it is."""
    from ..layout.route.router import route_adaptive
    from .board_model import layout_board, legacy_id

    if not nets:
        raise InvalidArgument("name the nets to route")
    path, data = S.load(session, KIND)
    _packet_guard(data, nets)
    doc = _doc(data)
    known = set(doc.nets)
    bad = [n for n in nets if n not in known]
    if bad:
        raise InvalidArgument(f"not nets of this board: {', '.join(bad[:10])}")
    lb = layout_board(doc)
    _width_rules(lb, nets, width_mm, via_diameter_mm, via_hole_mm)
    r = route_adaptive(lb, nets=frozenset(nets))
    out = r.apply(pours=False)
    mm = 1 / 39.37007874
    tracks = [{"net": t.net, "layer": legacy_id(t.layer), "x1": t.x1 * mm, "y1": t.y1 * mm, "x2": t.x2 * mm,
               "y2": t.y2 * mm, "width": t.width * mm}
              for t in out.tracks[len(lb.tracks):] if legacy_id(t.layer) and t.net]
    vias = [{"net": v.net, "x": v.x * mm, "y": v.y * mm, "diameter": v.diameter * mm, "hole": v.hole * mm}
            for v in out.vias[len(lb.vias):] if v.net]
    _record(path, data, f"route {','.join(nets[:6])}{'...' if len(nets) > 6 else ''}", tracks, vias)
    return {"nets": nets, **_report(_doc(data), set(nets), tracks, vias)}


def drill_pairs(pcb_path: str) -> set[tuple[int, int]]:
    """The board's drill pairs as (low, high) legacy ids, from the saved file."""
    import re

    def lid(tok: bytes) -> int | None:
        t = tok.decode()
        if t == "TOP":
            return 1
        if t == "BOTTOM":
            return 32
        if t.startswith("MID") and t[3:].isdigit():
            return int(t[3:]) + 1
        return None

    try:
        raw = Path(pcb_path).read_bytes()
    except OSError:
        return set()
    out = set()
    for low, high in re.findall(rb"\|LAYERPAIR\d+LOW=([A-Z0-9]+)\|LAYERPAIR\d+HIGH=([A-Z0-9]+)", raw):
        a, b = lid(low), lid(high)
        if a is not None and b is not None:
            out.add((a, b))
    return out


def _span_of(order: list[int], via_span: str, la: int, lb: int, point: dict) -> tuple[int, int]:
    """(layer_start, layer_end) legacy ids for a via changing la -> lb."""
    from .board_model import legacy_id

    lo_name, hi_name = point.get("via_low_layer"), point.get("via_high_layer")
    if lo_name or hi_name:
        lo, hi = legacy_id(str(lo_name or "")), legacy_id(str(hi_name or ""))
        if lo is None or hi is None or lo not in order or hi not in order:
            raise InvalidArgument(f"via_low_layer/via_high_layer {lo_name!r}/{hi_name!r}: name two copper "
                                  "layers of this board (TopLayer, MidLayerN, BottomLayer)")
    elif via_span == "span":
        lo, hi = la, lb
    elif via_span == "through":
        return 1, 32
    else:
        raise InvalidArgument("via_span is 'through' or 'span' (each via exactly from the layer it leaves "
                              "to the layer it reaches); a point may also carry via_low_layer/via_high_layer")
    i, j = sorted((order.index(lo), order.index(hi)))
    if not (i <= order.index(la) <= j and i <= order.index(lb) <= j):
        raise InvalidArgument(f"a via from {lo_name or lo} to {hi_name or hi} does not reach both layers "
                              "the path joins there")
    lo, hi = order[i], order[j]
    if i == 0 and j == len(order) - 1:
        return 1, 32
    return lo, hi


def draw(session: str, net: str, path_points: list[dict], width_mm: float | None = None,
         via_diameter_mm: float | None = None, via_hole_mm: float | None = None,
         via_span: str = "through") -> dict[str, Any]:
    """The agent's own path for ``net``: points ``{x_mm, y_mm, layer}``
    (TopLayer, MidLayer1.., BottomLayer), joined in order; where two
    consecutive points change layer at the same spot, a via.

    ``via_span``: "through" (default, Top to Bottom) or "span" (a blind or
    buried via from exactly the layer left to the layer reached). The
    second point of a layer change may carry ``via_low_layer`` and
    ``via_high_layer`` to give that one via its own span."""
    from ..bench.connectivity import copper_order
    from .board_model import legacy_id

    path, data = S.load(session, KIND)
    _packet_guard(data, [net])
    doc = _doc(data)
    order = [lid for lid in copper_order(doc) if lid <= 32] or [1, 32]
    if net not in doc.nets:
        raise InvalidArgument(f"{net} is not a net of this board")
    if len(path_points) < 2:
        raise InvalidArgument("a path needs two points or more")
    r = _rules(doc)
    w = width_mm or r["width_mm"]
    vd, vh = via_diameter_mm or r["via_diameter_mm"], via_hole_mm or r["via_hole_mm"]
    tracks, vias = [], []
    for a, b in zip(path_points, path_points[1:]):
        la, lb_ = legacy_id(str(a.get("layer", ""))), legacy_id(str(b.get("layer", "")))
        if la is None or lb_ is None:
            raise InvalidArgument(f"unknown layer in {a} or {b}: use TopLayer, MidLayerN or BottomLayer")
        ax, ay, bx, by = float(a["x_mm"]), float(a["y_mm"]), float(b["x_mm"]), float(b["y_mm"])
        if la != lb_:
            if math.hypot(bx - ax, by - ay) > 1e-6:
                raise InvalidArgument("change layer at one spot: give the via's point twice, once per layer")
            if la not in order or lb_ not in order:
                raise InvalidArgument(f"layer {a.get('layer')} or {b.get('layer')} is not on this board")
            lo, hi = _span_of(order, via_span, la, lb_, b)
            via = {"net": net, "x": ax, "y": ay, "diameter": vd, "hole": vh}
            if (lo, hi) != (1, 32):
                via.update(layer_start=lo, layer_end=hi)
            vias.append(via)
        elif math.hypot(bx - ax, by - ay) > 1e-6:
            tracks.append({"net": net, "layer": la, "x1": ax, "y1": ay, "x2": bx, "y2": by, "width": w})
    _record(path, data, f"draw {net}", tracks, vias)
    out = {"net": net, **_report(_doc(data), {net}, tracks, vias)}
    spans = sorted({(v["layer_start"], v["layer_end"]) for v in vias if "layer_start" in v})
    if spans:
        from .board_model import layer_token
        have = drill_pairs(data["source"])
        missing = [(lo, hi) for lo, hi in spans if (lo, hi) not in have and (hi, lo) not in have]
        out["blind_or_buried_vias"] = [f"{layer_token(lo)}-{layer_token(hi)}" for lo, hi in spans]
        if missing:
            out["missing_drill_pairs"] = [
                {"tool": "pcb_add_drill_pair",
                 "arguments": {"low_layer": layer_token(lo), "high_layer": layer_token(hi)}}
                for lo, hi in missing]
            out["note"] = ("the board has no drill pair for these spans: add them (then save) before "
                           "route_import_ses, or Altium's LayerPairs rule flags every one")
    return out


def fanout(session: str, nets: list[str] | None = None, stitch_spacing_mm: float | None = None,
           via_diameter_mm: float | None = None, via_hole_mm: float | None = None,
           stub_width_mm: float | None = None) -> dict[str, Any]:
    """Power and ground pads to their planes (routing.fanout); with
    ``stitch_spacing_mm``, stitching between their pours instead."""
    from ..routing.fanout import plan_fanout, plan_stitching

    path, data = S.load(session, KIND)
    doc = _doc(data)
    kw = dict(via_diameter_mm=via_diameter_mm, via_hole_mm=via_hole_mm)
    if stitch_spacing_mm:
        res = plan_stitching(doc, nets, spacing_mm=stitch_spacing_mm, **kw)
        action = "stitch"
    else:
        res = plan_fanout(doc, nets, stub_width_mm=stub_width_mm, **kw)
        action = "fanout"
    _record(path, data, action, res.tracks, res.vias)
    return {"action": action, **res.summary(), **_report(_doc(data), set(res.nets), res.tracks, res.vias)}


def check(session: str, nets: list[str] | None = None, detail: str = "summary") -> dict[str, Any]:
    """What is still open, and what breaks a clearance. ``detail`` "summary"
    (default): counts, the open nets and violations by rule, the first few
    of each; "full": every open net and up to 200 violations."""
    if detail not in ("summary", "full"):
        raise InvalidArgument("detail is 'summary' or 'full'")
    path, data = S.load(session, KIND)
    doc = _doc(data)
    still = _open(doc)
    full = detail == "full"
    counts: dict = {}
    total, rows = _violations(doc, set(nets) if nets else None, limit=200 if full else 12, counts=counts)
    open_nets = sorted(still.items(), key=lambda kv: (-kv[1], kv[0]))
    out = {"open_connections": sum(still.values()), "open_net_count": len(still),
           "open_nets": dict(open_nets if full else open_nets[:25]),
           "violations": total, "violations_by_rule": counts.get("by_rule", {}), "violation_list": rows,
           "added": {"tracks": len(data["added"]["tracks"]), "vias": len(data["added"]["vias"])},
           "note": "Open is counted on the copper with pours and planes as their outlines; clearances by the "
                   "in-house exact DRC on the board's rules. Altium's DRC is the final word after import."}
    if not full and (len(still) > 25 or counts.get("matching", 0) > len(rows)):
        out["more"] = "route_session_check(detail='full') lists every open net and violation"
    if data.get("packet"):
        out["packet"] = {"nets": len(data["packet"]["nets"]),
                         "open_in_packet": sum(still.get(n, 0) for n in data["packet"]["nets"])}
    return out


def ripup(session: str, nets: list[str]) -> dict[str, Any]:
    """Take back everything this session laid for ``nets``."""
    path, data = S.load(session, KIND)
    want = set(nets)
    before = (len(data["added"]["tracks"]), len(data["added"]["vias"]))
    data["added"]["tracks"] = [t for t in data["added"]["tracks"] if t["net"] not in want]
    data["added"]["vias"] = [v for v in data["added"]["vias"] if v["net"] not in want]
    data["history"].append({"action": f"ripup {','.join(sorted(want))}", "rebuilt": True})
    S.save(path, data)
    return {"removed": {"tracks": before[0] - len(data["added"]["tracks"]),
                        "vias": before[1] - len(data["added"]["vias"])}}


def undo(session: str, steps: int = 1) -> dict[str, Any]:
    """Take back the last actions (a rip-up cannot be undone; route again)."""
    path, data = S.load(session, KIND)
    undone = 0
    while undone < steps and data["history"]:
        last = data["history"][-1]
        if last.get("rebuilt"):
            break
        data["history"].pop()
        nt, nv = last.get("tracks", 0), last.get("vias", 0)
        if nt:
            del data["added"]["tracks"][-nt:]
        if nv:
            del data["added"]["vias"][-nv:]
        undone += 1
    S.save(path, data)
    return {"undone": undone, "left": len(data["history"])}


def view(session: str, nets: list[str] | None = None, side: str = "top") -> dict[str, Any]:
    """The board with the session's copper, open connections as ratsnest,
    violations numbered."""
    from .view import render

    path, data = S.load(session, KIND)
    doc = _doc(data)
    _, rows = _violations(doc, set(nets) if nets else None, limit=40)
    marks = [{"x": v["at_mm"][0] * 39.37007874, "y": v["at_mm"][1] * 39.37007874, "r": 25, "severity": "error",
              "what": f"{v['rule']}: {v['a']} / {v['b']}"} for v in rows]
    out = path.with_name(f"{path.stem}-view-{len(data['history'])}")
    return render(doc, out, markers=marks, nets=set(nets) if nets else None, side=side)


def _packet_guard(data: dict, nets: list[str]) -> None:
    """A packet session routes its own nets only: that is what makes the
    packets safe to plan in parallel and to merge."""
    pk = data.get("packet")
    if pk:
        foreign = [n for n in nets if n not in set(pk["nets"])]
        if foreign:
            raise InvalidArgument(f"this is packet {pk['name']}: {', '.join(foreign[:6])} belong to another "
                                  "packet or to the lead (shared nets); route them in the parent session")


def _net_pads(doc: PcbDocument) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for p in doc.pads:
        if 0 <= p.net < len(doc.nets):
            out.setdefault(doc.nets[p.net], []).append((p.x, p.y))
    return out


def packets(session: str, count: int = 4, margin_mm: float = 1.0, nets: list[str] | None = None,
            axis: str = "auto", fork: bool = True) -> dict[str, Any]:
    """Split the open nets into ``count`` bands across the board, each a job a
    planner can do alone: its own nets, its own band, no copper of the
    others'. A net whose pads span more than one band (with ``margin_mm``)
    is SHARED: the lead routes those first, in this session. With ``fork``
    each packet gets a session file of its own, a copy of this one, that
    routes only its nets; route_session_merge brings the copper back."""
    if not 2 <= int(count) <= 12:
        raise InvalidArgument("count is 2..12 packets")
    path, data = S.load(session, KIND)
    if data.get("packet"):
        raise InvalidArgument("this is already a packet; split the parent session")
    doc = _doc(data)
    still = _open(doc)
    want = [n for n in (nets or sorted(still)) if n in still]
    if not want:
        return {"packets": [], "shared": [], "note": "nothing is open"}
    pads = _net_pads(doc)
    xs = [x for n in want for x, _ in pads.get(n, [])]
    ys = [y for n in want for _, y in pads.get(n, [])]
    poly = doc.outline_poly or []
    bx = [p[0] for p in poly] or xs
    by = [p[1] for p in poly] or ys
    x0, x1, y0, y1 = min(bx), max(bx), min(by), max(by)
    ax = axis if axis in ("x", "y") else ("x" if (x1 - x0) >= (y1 - y0) else "y")
    lo, hi = (x0, x1) if ax == "x" else (y0, y1)
    step = (hi - lo) / count
    cuts = [lo + step * i for i in range(count + 1)]

    bands: list[list[str]] = [[] for _ in range(count)]
    shared: list[str] = []
    for n in want:
        coords = [(x if ax == "x" else y) for x, y in pads.get(n, [])]
        if not coords:
            continue
        a, b = min(coords), max(coords)
        idx = [i for i in range(count) if cuts[i] + margin_mm <= a and b <= cuts[i + 1] - margin_mm]
        (bands[idx[0]] if idx else shared).append(n)

    out_packets = []
    for i, members in enumerate(bands):
        if not members:
            continue
        name = f"P{i + 1}"
        if ax == "x":
            bounds = [round(cuts[i], 3), round(y0, 3), round(cuts[i + 1], 3), round(y1, 3)]
        else:
            bounds = [round(x0, 3), round(cuts[i], 3), round(x1, 3), round(cuts[i + 1], 3)]
        entry: dict[str, Any] = {"packet": name, "nets": len(members), "net_names": members[:12],
                                 "open_connections": sum(still[n] for n in members),
                                 "bounds_mm": bounds}
        if fork:
            child = dict(data)
            child["added"] = {"tracks": list(data["added"]["tracks"]), "vias": list(data["added"]["vias"])}
            child["history"] = []
            child["packet"] = {"name": name, "parent": str(path), "nets": members, "bounds_mm": bounds,
                               "base": {"tracks": len(data["added"]["tracks"]),
                                        "vias": len(data["added"]["vias"])}}
            cpath = path.with_name(f"{path.stem}-{name}.json")
            S.save(cpath, {k: v for k, v in child.items() if k != "updated"})
            entry["session"] = str(cpath)
        out_packets.append(entry)
    shared.sort(key=lambda n: -still[n])
    return {
        "axis": ax, "bands_mm": [round(c, 3) for c in cuts], "packets": out_packets,
        "shared": shared[:40], "shared_count": len(shared),
        "order": ("route the SHARED nets first in this session (they cross bands), then re-run "
                  "route_session_packets so the packets start from that copper, give each packet to one "
                  "planner, and route_session_merge their sessions back here"),
        "brief": ("You route packet {packet} only: session {session}. Its nets are the only ones the session "
                  "accepts; keep copper inside bounds_mm {bounds_mm}. Read nothing else. Use "
                  "route_session_route / route_session_draw / route_session_check (summary). Stop when "
                  "route_session_check shows open_in_packet 0 and no violation on your nets, or after 3 "
                  "failed tries on a net (leave it open). Reply in at most 8 lines: routed, still open, "
                  "violations. Never write to Altium."),
    }


def merge(session: str, packet_sessions: list[str]) -> dict[str, Any]:
    """Bring the copper of packet sessions back into their parent: each
    packet's own nets only, added since it was forked, flagged where it
    leaves the packet's bounds. Then the parent is checked as a whole."""
    path, data = S.load(session, KIND)
    merged, dropped, outside = [], 0, []
    all_tracks, all_vias = [], []
    for ps in packet_sessions:
        cpath, child = S.load(ps, KIND)
        pk = child.get("packet")
        if not pk or Path(pk["parent"]).resolve() != path.resolve():
            raise InvalidArgument(f"{cpath.name} is not a packet of {path.name}")
        own = set(pk["nets"])
        x0, y0, x1, y1 = pk["bounds_mm"]
        tr = child["added"]["tracks"][pk["base"]["tracks"]:]
        vi = child["added"]["vias"][pk["base"]["vias"]:]
        keep_t = [t for t in tr if t["net"] in own]
        keep_v = [v for v in vi if v["net"] in own]
        dropped += len(tr) - len(keep_t) + len(vi) - len(keep_v)
        tol = 0.5
        out_n = sum(1 for t in keep_t for x, y in ((t["x1"], t["y1"]), (t["x2"], t["y2"]))
                    if not (x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol))
        out_n += sum(1 for v in keep_v if not (x0 - tol <= v["x"] <= x1 + tol and y0 - tol <= v["y"] <= y1 + tol))
        if out_n:
            outside.append({"packet": pk["name"], "points_outside_bounds": out_n})
        all_tracks += keep_t
        all_vias += keep_v
        merged.append({"packet": pk["name"], "tracks": len(keep_t), "vias": len(keep_v)})
    _record(path, data, f"merge {','.join(m['packet'] for m in merged)}", all_tracks, all_vias)
    out = {"merged": merged, "dropped_foreign": dropped, **_report(_doc(data), None, all_tracks, all_vias)}
    if outside:
        out["outside_bounds"] = outside
        out["note"] = ("copper left its packet's band; the clearance check above covers where packets meet. "
                       "route_session_undo takes the whole merge back")
    return out


def export(session: str) -> dict[str, Any]:
    """The session's copper as an SES and a job route_import_ses takes."""
    import json

    from ..design.jobs import get_job_store
    from ..routing.fanout import write_ses
    from ..routing.service import routing_dir
    from ..routing.specctra import import_rules, layer_map

    path, data = S.load(session, KIND)
    base = read_pcbdoc(Path(data["source"]))
    rules, lm = import_rules(base), layer_map(base)
    res = _added(data)
    through = next((v for v in res.vias if "layer_start" not in v), None)
    res.rules = {"via_diameter_mm": (through["diameter"] if through else rules["via_diameter_mm"]),
                 "via_hole_mm": (through["hole"] if through else rules["via_hole_mm"])}
    ses = write_ses(res, routing_dir(base) / f"{path.stem}.ses", lm, base.path.stem)
    job = {"source": data["source"], "origin_mil": data["origin_mil"], "ses": str(ses), "layer_map": lm,
           "rules": {**rules, "via_hole_mm": res.rules["via_hole_mm"]}, "route_only": [],
           "session": str(path)}
    ses.with_suffix(".job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")
    store = get_job_store()
    jid = store.submit("session", lambda: job)
    store.wait(jid, 30)
    return {"job_id": jid, "ses": str(ses), "tracks": len(res.tracks), "vias": len(res.vias),
            "next": "route_import_ses(job_id, expect_file) in Altium writes it; then pcb_repour_polygons and "
                    "pcb_run_drc"}
