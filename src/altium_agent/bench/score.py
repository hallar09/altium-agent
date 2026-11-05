# SPDX-License-Identifier: Apache-2.0
"""Score a placement or a routing result against the person's board.

Every number is computed the same way for the person's board and for the
result, so a ratio means something. The person's board is the yardstick,
not the target: a result with less wire than the person used is not
better if it fails a check the person's board passes.

* :func:`placement_metrics` - the place_check findings (overlaps, parts
  off the board, decoupling distance, connectors off the edge), wirelength
  (half-perimeter, signal nets only) and how far the parts moved.
* :func:`trial_route` - the in-house engine on the board as it stands, after
  the power fan-out: the routability of a placement, or the baseline of a
  routing task.
* :func:`routing_metrics` - completion from the copper itself
  (bench/connectivity.py), vias and copper length.
"""

from __future__ import annotations

import math
import statistics
import time
from pathlib import Path

from ..offline.files.pcbdoc import NONE16, PcbDocument
from ..placement.check import check_plan
from ..placement.footprints import footprints_of
from ..reference.profile import decoupling
from . import connectivity as C
from .transform import distance, positions, strip_routing


def plane_nets(doc: PcbDocument) -> set[str]:
    """Nets joined by pours and internal planes rather than by tracks (a
    token pour does not count; see routing.fanout.plane_nets)."""
    from ..routing.fanout import plane_nets as carried

    return carried(doc)


def signal_nets(doc: PcbDocument) -> set[str]:
    planes = plane_nets(doc)
    return {doc.net_name(p.net) for p in doc.pads if p.net != NONE16} - planes - {""}


def hpwl_mm(doc: PcbDocument, nets: set[str]) -> float:
    """Half-perimeter wirelength over ``nets``: the usual placement proxy."""
    pts: dict[str, list[tuple[float, float]]] = {}
    for p in doc.pads:
        n = doc.net_name(p.net) if p.net != NONE16 else ""
        if n in nets:
            pts.setdefault(n, []).append((p.x, p.y))
    total = 0.0
    for ps in pts.values():
        if len(ps) > 1:
            xs, ys = [q[0] for q in ps], [q[1] for q in ps]
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return round(total, 2)


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(math.ceil(q * len(s))) - 1)], 3)


def placement_metrics(doc: PcbDocument, *, human: PcbDocument | None = None,
                      decap_max_mm: float = 3.0) -> dict:
    """The placement figures of ``doc`` (routing is ignored)."""
    fps = footprints_of(doc)
    here = positions(doc)
    chk = check_plan(fps, {"placements": list(here.values())}, poly=list(doc.outline_poly or []) or None,
                     decap_max_mm=decap_max_mm, baseline=False)
    by_code = {f["code"]: f.get("count", 1) for f in chk["findings"]}
    decaps = [d["distance_mm"] for d in decoupling(fps)]
    out = {
        "parts": len(fps),
        "errors": chk["counts"].get("error", 0),
        "overlap_pairs": by_code.get("overlap", 0),
        "outside_outline": by_code.get("outside_outline", 0),
        "decap_far": by_code.get("decap_far", 0),
        "near_edge": by_code.get("near_edge", 0),
        "connector_away_from_edge": by_code.get("connector_away_from_edge", 0),
        "decap_mm_median": round(statistics.median(decaps), 3) if decaps else None,
        "decap_mm_p90": _pct(decaps, 0.9),
        "hpwl_signal_mm": hpwl_mm(doc, signal_nets(doc)),
        "bottom_parts": sum(1 for c in doc.components if c.side == "bottom"),
    }
    if human is not None:
        ref = positions(human)
        moved = [distance(here[r], ref[r]) for r in here if r in ref]
        out["moved_mm_median"] = round(statistics.median(moved), 2) if moved else None
        out["side_changed"] = sum(1 for r in here if r in ref and here[r]["side"] != ref[r]["side"])
    return out


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _length(segs) -> float:
    return round(sum(math.hypot(s.x2 - s.x1, s.y2 - s.y1) for s in segs), 1)


def routing_metrics(doc: PcbDocument, *, extra_segs: list | None = None,
                    extra_dots: list | None = None) -> dict:
    """Completion, vias and length of ``doc``'s routing plus ``extra``
    copper (an engine's result, not yet on the board)."""
    cu = C.copper_of(doc)
    cu.segs += list(extra_segs or [])
    cu.dots += list(extra_dots or [])
    res = C.connect(cu)
    sig = signal_nets(doc)
    routed = [s for s in cu.segs]
    return {
        "all": C.summary(res),
        "signal": C.summary(res, sig),
        "vias": len(cu.dots),
        "length_mm": _length(routed),
        "notes": cu.notes,
    }


def board_metrics(board: PcbDocument) -> dict:
    """The routing figures of a finished board (the person's, or one saved
    after routing). Vias and length count only what a router lays down:
    footprint copper is left out, as it is for an engine's result."""
    m = routing_metrics(board)
    base, full = C.copper_of(strip_routing(board)), C.copper_of(board)
    m["vias"] = len(full.dots) - len(base.dots)
    m["length_mm"] = round(_length(full.segs) - _length(base.segs), 1)
    return m


def human_reference(human: PcbDocument) -> dict:
    """The person's own figures: the yardstick for both tasks."""
    return {"placement": placement_metrics(strip_routing(human)), "routing": board_metrics(human)}


def ses_copper(ses: Path, layer_map: dict[str, int], via_hole_mm: float):
    """An SES as connectivity copper: segments with legacy layer ids, vias."""
    from ..routing.specctra import parse_ses

    r = parse_ses(ses, via_hole_mm)
    segs = [C.Seg(t["net"], layer_map.get(t["layer"], 0), t["x1"], t["y1"], t["x2"], t["y2"], t["width"])
            for t in r.tracks if layer_map.get(t["layer"]) in range(1, 33)]
    dots = [C.Dot(v["net"], v["x"], v["y"], v["diameter"]) for v in r.vias]
    return segs, dots


def result_metrics(doc: PcbDocument, segs: list, dots: list) -> dict:
    """The routing figures of ``doc`` plus an engine's copper. An SES repeats
    the board's own copper (footprint vias, kept routing) as protected
    wiring: that is counted once, and vias and length are the new copper's."""
    base = C.copper_of(doc)
    key = lambda s: (round(s.x1, 3), round(s.y1, 3), round(s.x2, 3), round(s.y2, 3), s.layer)  # noqa: E731
    have = {key(s) for s in base.segs}
    have_v = {(round(d.x, 3), round(d.y, 3)) for d in base.dots}
    # A later pass's SES repeats the earlier passes' copper too: keep one of each.
    new_segs = list({key(s): s for s in segs if key(s) not in have}.values())
    new_dots = list({(round(d.x, 3), round(d.y, 3)): d for d in dots
                     if (round(d.x, 3), round(d.y, 3)) not in have_v}.values())
    m = routing_metrics(doc, extra_segs=new_segs, extra_dots=new_dots)
    m["vias"], m["length_mm"] = len(new_dots), _length(new_segs)
    return m


def trial_route(doc: PcbDocument, work: Path, *, stem: str = "trial", two_pass: bool = True) -> dict:
    """Route ``doc`` with the in-house engine and score the result.

    As the broker plans it (``two_pass``): a via from each pour and plane
    net's pad to its plane (routing.fanout), the other nets by the engine
    (interactive.board_model gives it the saved board), then stitching
    between the pours. ``two_pass=False`` lets the engine route every net at
    once. Existing routing stays; a stripped board is routed from scratch.
    Returns the routing metrics of board + result, each step's record, and
    the result as an SES in ``work``.
    """
    from ..interactive.board_model import layout_board, legacy_id
    from ..layout.route.router import route_adaptive
    from ..routing.fanout import FanoutResult, plan_fanout, plan_stitching, with_result, write_ses
    from ..routing.specctra import layer_map

    work.mkdir(parents=True, exist_ok=True)
    pours = sorted(plane_nets(doc) & {doc.net_name(p.net) for p in doc.pads if p.net != NONE16})
    laid = FanoutResult()
    runs: list[dict] = []
    t0 = time.time()
    board = doc
    if two_pass and pours:
        t1 = time.time()
        fan = plan_fanout(doc, pours)
        laid.tracks += fan.tracks
        laid.vias += fan.vias
        board = with_result(doc, fan)
        runs.append({"pass": "fanout", "seconds": round(time.time() - t1, 1), **fan.summary()})
    t1 = time.time()
    lb = layout_board(board)
    nets = None
    if two_pass and pours:
        nets = frozenset(n for n in {p.net for p in lb.pads if p.net} if n not in pours)
    r = route_adaptive(lb, nets=nets)
    out = r.apply(pours=False)
    mm = 1 / 39.37007874
    routed = FanoutResult(
        tracks=[{"net": t.net, "layer": legacy_id(t.layer), "x1": t.x1 * mm, "y1": t.y1 * mm, "x2": t.x2 * mm,
                 "y2": t.y2 * mm, "width": t.width * mm}
                for t in out.tracks[len(lb.tracks):] if t.net and legacy_id(t.layer)],
        vias=[{"net": v.net, "x": v.x * mm, "y": v.y * mm, "diameter": v.diameter * mm, "hole": v.hole * mm}
              for v in out.vias[len(lb.vias):] if v.net])
    laid.tracks += routed.tracks
    laid.vias += routed.vias
    runs.append({"pass": "route", "seconds": round(time.time() - t1, 1), "failed": r.report.failed,
                 "iterations": r.report.iterations})
    if two_pass and pours:
        t1 = time.time()
        st = plan_stitching(with_result(board, routed), pours)
        laid.vias += st.vias
        runs.append({"pass": "stitch", "seconds": round(time.time() - t1, 1), "vias_placed": st.placed})
    segs = [C.Seg(t["net"], t["layer"], t["x1"], t["y1"], t["x2"], t["y2"], t["width"]) for t in laid.tracks]
    dots = [C.Dot(v["net"], v["x"], v["y"], v["diameter"]) for v in laid.vias]
    m = result_metrics(doc, segs, dots)
    laid.rules = {"via_diameter_mm": laid.vias[0]["diameter"] if laid.vias else 0.6,
                  "via_hole_mm": laid.vias[0]["hole"] if laid.vias else 0.3}
    ses = write_ses(laid, work / f"{stem}.ses", layer_map(doc), doc.path.stem)
    m["engine"] = {"name": "inhouse" + (" + fan-out" if two_pass and pours else ""), "passes": runs,
                   "seconds": round(time.time() - t0, 1), "left_to_pours": pours}
    m["files"] = [{"ses": str(ses)}]
    return m


def ratio(a, b) -> float | None:
    return round(a / b, 3) if a is not None and b else None
