# SPDX-License-Identifier: Apache-2.0
"""The routing broker's file side, as the route_ tools call it (Altium Agent).

Plain functions: read the saved board, plan, fan power out, turn an SES
(a fan-out's, a routing session's) into bridge rows, check vias. Only the
tool layer talks to Altium.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from ..offline.context import output_dir_for
from ..offline.errors import InvalidArgument
from ..offline.files.pcbdoc import PcbDocument, read_pcbdoc
from ..placement.service import is_protected_design, load_board, load_profile
from .broker import ENGINES
from .broker import plan as broker_plan
from .specctra import import_rules, layer_map


def _owner(doc: PcbDocument, project: Path | None) -> Path:
    return project or doc.path


def routing_dir(doc: PcbDocument, project: Path | None = None) -> Path:
    return output_dir_for(_owner(doc, project), "routing")


def engines_status() -> dict:
    return {
        "inhouse": {"available": True,
                    "how": "route_session_route (the saved board, net by net, as the agent directs); "
                           "pcb_autoroute and pcb_autoroute_apply on the live board"},
        "altium": {"available": True,
                   "how": "Situs via route_situs (no dialog; the board's own strategy) as the finisher; the "
                          "interactive router is used by you in Altium, and the broker reads the saved result"},
    }


def route_broker_plan(project: str | None = None, pcb: str | None = None, profile: Any = None,
                      engines: list[str] | None = None, name: str | None = None,
                      reference: str | None = None) -> dict:
    bad = [e for e in (engines or []) if e not in ENGINES]
    if bad:
        raise InvalidArgument(f"unknown engine(s) {bad}; known: {list(ENGINES)}")
    b = load_board(project, pcb)
    prof = load_profile(profile) if profile is not None else None
    engines = list(engines or ENGINES)
    ref_doc = None
    ref_path = reference or (prof.get("source") if prof else None)
    if ref_path and Path(ref_path).suffix.lower() == ".pcbdoc" and Path(ref_path).is_file():
        ref_doc = read_pcbdoc(Path(ref_path))
    elif reference:
        ref_doc = load_board(reference if reference.lower().endswith(".prjpcb") else None,
                             None if reference.lower().endswith(".prjpcb") else reference).doc
    p = broker_plan(b.doc, engines=engines, profile=prof, reference=ref_doc)
    p["created"] = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    p["project"] = str(b.project) if b.project else None
    if is_protected_design(p["source"]):
        p["notes"].insert(0, "This board is a sample or installed design: plan only. Copy the project to a "
                             "folder of yours and plan the copy before routing.")
        p["read_only"] = True
    stem = name or f"{b.doc.path.stem}-route-{_dt.datetime.now(_dt.UTC):%Y%m%d-%H%M%S}"
    path = routing_dir(b.doc, b.project) / f"{stem}.json"
    for s in p["steps"]:
        if s["tool"] == "route_converge":
            s["args"]["plan"] = str(path)
    path.write_text(json.dumps(p, indent=1), encoding="utf-8")
    return {"plan_path": str(path), **p}


def load_plan(plan: Any) -> tuple[dict, Path | None]:
    if isinstance(plan, dict):
        return plan, None
    path = Path(str(plan)).expanduser()
    if not path.is_file():
        raise InvalidArgument(f"no routing plan at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "routed_by_plan" not in data:
        raise InvalidArgument("not a routing plan (made by route_broker_plan)")
    return data, path


def with_prior_routing(doc: PcbDocument, jobs: list[dict]) -> PcbDocument:
    """``doc`` with the copper of earlier jobs (a fan-out, a session export)
    on it, as if imported: so a later step can build on them without Altium.
    In Altium, import the earlier job and save instead."""
    import copy

    from ..offline.files.pcbdoc import NONE16, Track, Via
    from .specctra import parse_ses

    out = copy.copy(doc)
    out.tracks, out.vias = list(doc.tracks), list(doc.vias)
    index = {n: i for i, n in enumerate(doc.nets)}
    for job in jobs:
        if not job.get("ses") or not Path(job["ses"]).exists():
            raise InvalidArgument(f"job {job.get('job_id', '?')} has no SES to build on")
        lm, hole = job["layer_map"], job["rules"]["via_hole_mm"]
        r = parse_ses(Path(job["ses"]), hole)
        out.tracks += [Track(lm[t["layer"]], index[t["net"]], NONE16, t["x1"], t["y1"], t["x2"], t["y2"], t["width"])
                       for t in r.tracks if t["net"] in index and lm.get(t["layer"]) in range(1, 33)]
        out.vias += [Via(index[v["net"]], v["x"], v["y"], v["diameter"], v["hole"], 1, 32)
                     for v in r.vias if v["net"] in index]
    return out


def ses_rows_for(job: dict, only_nets: set[str] | None = None):
    """Bridge rows for a finished fan-out or session job, against the board as saved now."""
    from .ses_import import ses_rows

    doc = read_pcbdoc(Path(job["source"]))
    if [round(v, 3) for v in doc.origin_mil] != [round(v, 3) for v in job["origin_mil"]]:
        raise InvalidArgument(f"the board origin moved since the DSN was exported ({job['origin_mil']} -> "
                              f"{list(doc.origin_mil)} mil); export and route again")
    only = only_nets if only_nets is not None else (set(job["route_only"]) if job.get("route_only") else None)
    return ses_rows(Path(job["ses"]), job["layer_map"], job["rules"], tuple(job["origin_mil"]),
                    existing=doc, only_nets=only)


def via_style(project: str | None = None, pcb: str | None = None, profile: Any = None) -> dict:
    from .via_style import check

    b = load_board(project, pcb)
    return check(b.doc, load_profile(profile) if profile is not None else None)


def tenting_read_back(pcb_path: str, **kw) -> dict:
    from .via_style import read_back

    return read_back(read_pcbdoc(Path(pcb_path)), **kw)


def fanout_job(params: dict) -> dict:
    """The fan-out job: plan the vias on the saved board (with ``after``
    jobs' routing on it), write them as an SES, keep the job record the
    import reads."""
    from .fanout import FanoutResult, plan_fanout, plan_stitching, write_ses

    doc = read_pcbdoc(Path(params["source"]))
    if params.get("after"):
        doc = with_prior_routing(doc, params["after"])
    vias = dict(via_diameter_mm=params.get("via_diameter_mm"), via_hole_mm=params.get("via_hole_mm"),
                clearance_mm=params.get("clearance_mm"))
    if params.get("pads", True):
        res = plan_fanout(doc, params.get("nets"), stub_width_mm=params.get("stub_width_mm"),
                          max_reach_mm=params.get("max_reach_mm", 1.5), **vias)
    else:
        res = FanoutResult(nets=sorted(params.get("nets") or []))
    if params.get("stitch_spacing_mm"):
        from .fanout import with_result

        st = plan_stitching(with_result(doc, res), params.get("nets"), spacing_mm=params["stitch_spacing_mm"],
                            **vias)
        res.vias += st.vias
        res.rules = {**st.rules, **res.rules}
        res.nets = res.nets or st.nets
        res.rules["stitching_vias"] = st.placed
    lm = layer_map(doc)
    work = routing_dir(doc)
    ses = write_ses(res, work / f"{doc.path.stem}-fanout.ses", lm, doc.path.stem)
    out = {"source": str(doc.path), "origin_mil": list(doc.origin_mil), "ses": str(ses), "layer_map": lm,
           "rules": {**import_rules(doc), "via_hole_mm": res.rules["via_hole_mm"]}, "route_only": res.nets,
           "fanout": res.summary()}
    ses.with_suffix(".job.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
