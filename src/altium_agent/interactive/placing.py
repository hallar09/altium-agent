# SPDX-License-Identifier: Apache-2.0
"""AI-driven placement: the agent places each part; the tools check each move.

The agent decides every position. After each move the session answers
what a person sees on screen: does it overlap anything, is it on the
board and clear of the edge, how far is a decoupling cap from its IC's
supply pin, did the move make the connections it serves shorter or
longer. ``part`` says what a part connects to and where those parts are,
so the agent can put it where it belongs. ``view`` draws it.

Positions are in mm from the board origin, as the board is saved. The
state is a session file (interactive/session.py); ``export`` turns it into
the plan ``place_check`` and ``place_apply`` take, so the placement lands
in Altium the same way an engine's would.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..offline.errors import InvalidArgument
from ..offline.files.pcbdoc import PcbDocument, read_pcbdoc
from . import session as S

KIND = "place"


def _doc(data: dict) -> PcbDocument:
    """The board with the session's positions on it."""
    from ..bench.transform import apply_placements

    doc = read_pcbdoc(Path(data["source"]))
    return apply_placements(doc, list(data["positions"].values()))


def _fps(doc: PcbDocument):
    from ..placement.footprints import footprints_of

    return {f.ref.upper(): f for f in footprints_of(doc)}


def _wire(doc: PcbDocument) -> dict[str, float]:
    """Half-perimeter wirelength per signal net (the connections a placement serves)."""
    from ..bench.score import signal_nets

    sig = signal_nets(doc)
    pts: dict[str, list] = {}
    for p in doc.pads:
        n = doc.net_name(p.net) if p.net != 0xFFFF else ""
        if n in sig:
            pts.setdefault(n, []).append((p.x, p.y))
    return {n: (max(x for x, _ in v) - min(x for x, _ in v)) + (max(y for _, y in v) - min(y for _, y in v))
            for n, v in pts.items() if len(v) > 1}


def _check(doc: PcbDocument, decap_max_mm: float = 3.0) -> dict:
    from ..bench.transform import positions
    from ..placement.check import check_plan

    fps = list(_fps(doc).values())
    return check_plan(fps, {"placements": list(positions(doc).values())},
                      poly=list(doc.outline_poly or []) or None, decap_max_mm=decap_max_mm, baseline=False)


def _part_row(f, at: dict, sheet_of: dict | None = None) -> dict:
    return {"ref": f.ref, "footprint": f.footprint, "comment": f.comment,
            "sheet": (sheet_of or {}).get(f.ref),
            "size_mm": [round(f.box.w, 2), round(f.box.h, 2)], "pins": len(f.pins),
            "nets": len({p.net for p in f.pins if p.net}), "x_mm": at["x_mm"], "y_mm": at["y_mm"],
            "rotation": at["rotation"], "side": at["side"], "locked": f.locked}


def start(project: str | None = None, pcb: str | None = None, name: str | None = None,
          from_plan: str | None = None) -> dict[str, Any]:
    """Open a placement session on the saved board (its current positions,
    or those of a plan made earlier)."""
    from ..bench.transform import positions
    from ..placement.service import _owner, load_board, load_plan

    b = load_board(project, pcb)
    pos = positions(b.doc)
    if from_plan:
        plan = load_plan(from_plan)
        for p in plan.get("placements") or []:
            if p["ref"] in pos:
                pos[p["ref"]] = {k: p[k] for k in ("ref", "x_mm", "y_mm", "rotation", "side") if k in p}
    data = {"source": str(b.doc.path), "project": str(b.project) if b.project else None,
            "origin_mil": list(b.doc.origin_mil), "positions": pos, "start": dict(pos),
            "sheet_of": b.sheet_of}
    path = S.new(KIND, _owner(b), name, data)
    doc = _doc(data)
    fps = _fps(doc)
    chk = _check(doc)
    bbox = doc.outline_bbox() or {}
    rows = sorted((_part_row(f, pos[f.ref], b.sheet_of) for f in fps.values()),
                  key=lambda r: -(r["size_mm"][0] * r["size_mm"][1]))
    off = [r["ref"] for r in rows if any(f.get("code") == "outside_outline" and
                                         any(i.get("ref") == r["ref"] for i in f.get("items") or [])
                                         for f in chk["findings"])]
    return {"session": str(path), "board": {"outline_bbox_mm": bbox, "copper_layers": len(doc.copper_layers),
                                            "parts": len(rows)},
            "parts": rows, "off_board": off, "check": chk["counts"],
            "next": "place_session_part for what a part connects to; place_session_move to place it; "
                    "place_session_view to look; place_session_export when done"}


def part(session: str, ref: str) -> dict[str, Any]:
    """A part's pins and nets, and where the parts it connects to are now."""
    from ..bench.score import plane_nets
    from ..placement.footprints import pin_on_board

    path, data = S.load(session, KIND)
    doc = _doc(data)
    fps = _fps(doc)
    f = fps.get(ref.upper())
    if f is None:
        raise InvalidArgument(f"{ref} is not on the board")
    planes = plane_nets(doc)
    by_net: dict[str, list] = {}
    for g in fps.values():
        for p in g.pins:
            if p.net:
                x, y = pin_on_board(g, p)
                by_net.setdefault(p.net, []).append((g.ref, p.name, round(x, 3), round(y, 3)))
    pins, links = [], {}
    for p in f.pins:
        x, y = pin_on_board(f, p)
        pins.append({"pin": p.name, "net": p.net, "x_mm": round(x, 3), "y_mm": round(y, 3), "layer": p.layer})
        if p.net and p.net not in planes:
            for other, pin, ox, oy in by_net.get(p.net, []):
                if other != f.ref:
                    d = math.hypot(ox - x, oy - y)
                    cur = links.get(other)
                    if cur is None or d < cur["nearest_mm"]:
                        links[other] = {"ref": other, "via_net": p.net, "their_pin": pin, "nearest_mm": round(d, 2),
                                        "at_mm": [ox, oy]}
    from ..reference.profile import decoupling

    dec = [d for d in decoupling(list(fps.values())) if d["ref"] == f.ref]
    at = data["positions"][f.ref]
    return {"part": _part_row(f, at, data.get("sheet_of")), "pins": pins,
            "decouples": dec[0] if dec else None,
            "connects_to": sorted(links.values(), key=lambda r: r["nearest_mm"])[:40],
            "power_and_ground": sorted({p.net for p in f.pins if p.net in planes}),
            "note": "Positions are the part's reference point, mm from the board origin; pin positions on the "
                    "board as it sits now."}


def move(session: str, moves: list[dict], force: bool = False) -> dict[str, Any]:
    """Place or move parts. Each move is ``{ref, x_mm, y_mm}`` (absolute)
    or ``{ref, dx_mm, dy_mm}`` (relative), with ``rotation`` (degrees) and
    ``side`` (top/bottom) optional. Locked parts move only with ``force``."""
    path, data = S.load(session, KIND)
    pos = data["positions"]
    before_doc = _doc(data)
    fps = _fps(before_doc)
    prev, moved = {}, []
    for m in moves:
        ref = next((r for r in pos if r.upper() == str(m.get("ref", "")).upper()), None)
        if ref is None:
            raise InvalidArgument(f"{m.get('ref')} is not on the board")
        if fps[ref.upper()].locked and not force:
            raise InvalidArgument(f"{ref} is locked on the board (fixed by the design); pass force=true to move it")
        cur = dict(pos[ref])
        prev.setdefault(ref, dict(cur))
        if "x_mm" in m or "y_mm" in m:
            cur["x_mm"] = round(float(m.get("x_mm", cur["x_mm"])), 4)
            cur["y_mm"] = round(float(m.get("y_mm", cur["y_mm"])), 4)
        cur["x_mm"] = round(cur["x_mm"] + float(m.get("dx_mm", 0)), 4)
        cur["y_mm"] = round(cur["y_mm"] + float(m.get("dy_mm", 0)), 4)
        if "rotation" in m:
            cur["rotation"] = float(m["rotation"]) % 360
        if "side" in m:
            if m["side"] not in ("top", "bottom"):
                raise InvalidArgument("side is top or bottom")
            cur["side"] = m["side"]
        pos[ref] = cur
        moved.append(ref)
    data["history"].append({"moved": prev})
    S.save(path, data)
    after = _doc(data)
    w0, w1 = _wire(before_doc), _wire(after)
    touched = {p.net for r in moved for p in _fps(after)[r.upper()].pins if p.net}
    chk = _check(after)
    mine = []
    for f in chk["findings"]:
        items = [i for i in f.get("items") or [] if i.get("ref") in moved or i.get("other") in moved]
        if items:
            mine.append({"code": f["code"], "severity": f["severity"], "items": items[:10]})
    return {"moved": [{**pos[r]} for r in moved], "findings_on_moved": mine,
            "wire_mm_of_their_nets": {"before": round(sum(w0.get(n, 0) for n in touched), 2),
                                      "after": round(sum(w1.get(n, 0) for n in touched), 2)},
            "board": chk["counts"], "undo": "place_session_undo"}


def check(session: str, decap_max_mm: float = 3.0) -> dict[str, Any]:
    """Every finding on the placement, and its figures."""
    from ..bench.score import placement_metrics

    path, data = S.load(session, KIND)
    doc = _doc(data)
    chk = _check(doc, decap_max_mm)
    return {"ok": chk["ok"], "counts": chk["counts"], "findings": chk["findings"],
            "metrics": placement_metrics(doc, decap_max_mm=decap_max_mm)}


def view(session: str, highlight: list[str] | None = None, side: str = "top", ratsnest: bool = True) -> dict:
    """The board as the session has it, findings numbered, ``highlight`` ringed."""
    from ..placement.footprints import board_box
    from ..units import MILS_PER_MM
    from .view import render

    path, data = S.load(session, KIND)
    doc = _doc(data)
    fps = _fps(doc)
    marks = []
    for f in _check(doc)["findings"]:
        for it in f.get("items") or []:
            g = fps.get(str(it.get("ref", "")).upper())
            if g is not None:
                b = board_box(g)
                marks.append({"x": round((b.min_x + b.max_x) / 2 * MILS_PER_MM, 1),
                              "y": round((b.min_y + b.max_y) / 2 * MILS_PER_MM, 1), "r": 40,
                              "severity": f["severity"], "what": f"{f['code']}: {g.ref}"})
    out = path.with_name(f"{path.stem}-view-{len(data['history'])}")
    return render(doc, out, markers=marks[:60], highlight=highlight, side=side, ratsnest=ratsnest)


def undo(session: str, steps: int = 1) -> dict[str, Any]:
    path, data = S.load(session, KIND)
    undone = 0
    while undone < steps and data["history"]:
        last = data["history"].pop()
        data["positions"].update(last.get("moved") or {})
        undone += 1
    S.save(path, data)
    return {"undone": undone, "left": len(data["history"])}


def export(session: str, name: str | None = None) -> dict[str, Any]:
    """The session as a placement plan: place_check checks it, place_apply writes it to Altium."""
    import datetime as _dt
    import json

    from ..offline.context import output_dir_for
    from ..placement.service import PLAN_VERSION

    from ..placement.service import place_check

    path, data = S.load(session, KIND)
    src = Path(data["source"])
    plan = {"plan_version": PLAN_VERSION, "placements": list(data["positions"].values()),
            "source": data["source"], "project": data.get("project"), "origin_mil": data["origin_mil"],
            "source_mtime": src.stat().st_mtime, "created": S.now(), "kept_outline": True, "levers": {},
            "score": {}, "keepouts": [], "notes": [f"placed by hand in session {path.name}"]}
    # As place_check judges it, so place_apply takes what place_check passes:
    # a finding the board already had on parts the session left alone is inherited.
    chk = place_check(plan)
    plan["check"] = {"ok": chk["ok"], "counts": chk["counts"],
                     "errors": [f["code"] for f in chk["findings"] if f["severity"] == "error"]}
    owner = Path(data["project"]) if data.get("project") else src
    out = output_dir_for(owner, "plans") / f"{name or path.stem}.json"
    out.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    return {"plan_path": str(out), "check": plan["check"],
            "next": "place_check(plan) to confirm, then place_apply(plan, confirm=true) in Altium"}
