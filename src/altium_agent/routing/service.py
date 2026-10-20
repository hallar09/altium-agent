# SPDX-License-Identifier: Apache-2.0
"""The routing broker's file side, as the route_ tools call it (Altium Agent).

Plain functions: read the saved board, plan, export DSN, run Freerouting
(external), turn its SES into bridge rows, check vias. Only the tool layer
talks to Altium.
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
from . import freerouting as fr
from .broker import ENGINES
from .broker import plan as broker_plan
from .specctra import DsnOptions, export_dsn


def _owner(doc: PcbDocument, project: Path | None) -> Path:
    return project or doc.path


def routing_dir(doc: PcbDocument, project: Path | None = None) -> Path:
    return output_dir_for(_owner(doc, project), "routing")


def engines_status() -> dict:
    f = fr.find()
    return {
        "inhouse": {"available": True, "how": "pcb_autoroute (reads the focused board live), pcb_autoroute_apply"},
        "freerouting": ({"available": f.usable, **f.to_dict()} if f else
                        {"available": False, "searched": [str(d) for d in fr.search_dirs()],
                         "hint": "Freerouting is GPL-3.0 and is not shipped; install it yourself and set "
                                 "ALTIUM_AGENT_FREEROUTING (a jar, with ALTIUM_AGENT_JAVA, or a native build)."}),
        "altium": {"available": True,
                   "how": "Situs via route_situs (no dialog; the board's own strategy) as the finisher; the "
                          "interactive router is used by you in Altium, and the broker reads the saved result"},
    }


POLICY_FILE = "altium_agent.json"


def project_policy(folder: Path) -> dict:
    """The project's own settings: ``altium_agent.json`` beside the .PrjPcb.

    ``{"routing": {"third_party_engines": false}}`` keeps third-party routing
    engines (Freerouting) out of this project; absent, they are allowed when
    installed. The file is the user's; Altium Agent only reads it.
    """
    p = Path(folder) / POLICY_FILE
    if not p.is_file():
        return {"source": None, "third_party_engines": True}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidArgument(f"{p} is not valid JSON: {exc}") from exc
    r = data.get("routing") or {}
    return {"source": str(p), "third_party_engines": bool(r.get("third_party_engines", True))}


def route_broker_plan(project: str | None = None, pcb: str | None = None, profile: Any = None,
                      engines: list[str] | None = None, batch_size: int = 0, max_passes: int = 50,
                      name: str | None = None, reference: str | None = None) -> dict:
    bad = [e for e in (engines or []) if e not in ENGINES]
    if bad:
        raise InvalidArgument(f"unknown engine(s) {bad}; known: {list(ENGINES)}")
    b = load_board(project, pcb)
    prof = load_profile(profile) if profile is not None else None
    policy = project_policy((b.project or b.doc.path).parent)
    engines = list(engines or ENGINES)
    if not policy["third_party_engines"] and "freerouting" in engines:
        engines.remove("freerouting")
    ref_doc = None
    ref_path = reference or (prof.get("source") if prof else None)
    if ref_path and Path(ref_path).suffix.lower() == ".pcbdoc" and Path(ref_path).is_file():
        ref_doc = read_pcbdoc(Path(ref_path))
    elif reference:
        ref_doc = load_board(reference if reference.lower().endswith(".prjpcb") else None,
                             None if reference.lower().endswith(".prjpcb") else reference).doc
    f = fr.find()
    p = broker_plan(b.doc, engines=engines, freerouting_ready=bool(f and f.usable), batch_size=batch_size,
                    profile=prof, max_passes=max_passes, reference=ref_doc)
    p["policy"] = policy
    if not policy["third_party_engines"]:
        p["notes"].insert(0, f"The project's policy ({policy['source']}) forbids third-party engines: "
                             "no Freerouting.")
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


def dsn_options(route_only: list[str] | None, keep_existing: bool, track_width_mm=None, clearance_mm=None,
                via_diameter_mm=None, via_hole_mm=None, route_layers=None, skip_nets=None,
                net_widths_mm=None) -> DsnOptions:
    return DsnOptions(track_width_mm=track_width_mm, clearance_mm=clearance_mm, via_diameter_mm=via_diameter_mm,
                      via_hole_mm=via_hole_mm, route_layers=route_layers, skip_nets=list(skip_nets or []),
                      net_widths_mm=dict(net_widths_mm or {}), keep_existing=keep_existing,
                      route_only=list(route_only) if route_only else None)


def export(pcb_path: str, opts: DsnOptions, tag: str = "") -> dict:
    """Write the DSN for a saved board into its routing folder."""
    doc = read_pcbdoc(Path(pcb_path))
    e = export_dsn(doc, opts)
    work = routing_dir(doc)
    stem = doc.path.stem + (f"-{tag}" if tag else "")
    dsn = work / f"{stem}.dsn"
    dsn.write_text(e.text, encoding="utf-8")
    return {"dsn": str(dsn), "ses": str(work / f"{stem}.ses"), "nets": e.nets, "components": e.components,
            "layer_map": e.layer_map, "rules": e.rules, "origin_mil": list(doc.origin_mil),
            "source": str(doc.path)}


def freerouting_job(params: dict) -> dict:
    """The job body: run Freerouting on an exported DSN (blocks in a worker thread)."""
    run = fr.run(Path(params["dsn"]), Path(params["ses"]), max_passes=params.get("max_passes", 50),
                 timeout_s=params.get("timeout_s", 1200), optimize=params.get("optimize", False),
                 extra=tuple(params.get("extra") or ()))
    out = {**params, "freerouting": run}
    meta = Path(params["ses"]).with_suffix(".job.json")
    meta.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def ses_rows_for(job: dict, only_nets: set[str] | None = None):
    """Bridge rows for a finished Freerouting job, against the board as saved now."""
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
