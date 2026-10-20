# SPDX-License-Identifier: Apache-2.0
"""Look before claiming: the review at each stage gate (Altium Agent).

At a gate the model gets, for the saved design, the pictures (board top and
bottom, every schematic sheet) with the findings numbered on them, the
stage's measured numbers, and a rubric to answer from the pictures. Its
verdict goes into the session journal. Numbers decide pass and fail; the
pictures catch what numbers miss (a part in the wrong place, a cramped
connector, a ratsnest that crosses the board, an unreadable sheet).

Everything here reads the saved files. Unsaved edits in Altium are not in
the review; save first.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from ..offline.files.pcbdoc import PcbDocument
from ..units import MILS_PER_MM

#: Gate -> journal stages it closes, what is drawn, and the rubric.
GATES: dict[str, dict[str, Any]] = {
    "schematic": {
        "stages": ("schematic_emit",),
        "draw": ("sheets",),
        "rubric": [
            "Every sheet reads left to right: inputs left, outputs right, power on top, ground below.",
            "No part, label or wire overlaps another; no wire runs through a part body.",
            "Every IC has its decoupling caps drawn next to its supply pins.",
            "Net labels and power ports are on wire ends, not floating (the ERC count says how many float).",
            "Connector pinouts and the USB/SWD/QSPI interfaces are labelled so a reviewer can follow them.",
        ],
    },
    "placement": {
        "stages": ("placement",),
        "draw": ("top", "bottom"),
        "rubric": [
            "Connectors and plugs sit on the board edge, facing out, with nothing in front of them.",
            "Each decoupling cap is beside its IC's supply pin, on the shortest path.",
            "The crystal sits next to its MCU pins, with no other signal parts between them.",
            "The USB path (connector, ESD, series resistors, MCU) is short and in a line.",
            "Buttons, LEDs and test pads are reachable and not under other parts.",
            "Nothing overlaps (the markers show measured overlaps) and parts keep off the edge.",
        ],
    },
    "routing": {
        "stages": ("routing", "pours_tuning"),
        "draw": ("top", "bottom"),
        "rubric": [
            "No ratsnest line (dashed yellow) is left, or each one left is explained.",
            "The differential pair runs together, same layer, same length, no stubs or detours.",
            "No track runs under the crystal or through an antenna keep-out.",
            "Power tracks are visibly wider than signals; no long thin supply runs.",
            "No needless detours or vias; 45-degree bends, no acute angles.",
            "Pours are connected (no isolated islands) and stitched.",
        ],
    },
    "verification": {
        "stages": ("verification",),
        "draw": ("top", "bottom", "sheets"),
        "rubric": [
            "Every marker on the board (DRC copper problems) is resolved or explained.",
            "Silkscreen designators are readable and not on pads.",
            "Board outline, mounting holes and connector cut-outs match the mechanical intent.",
            "The design is not called manufacturing-ready: list what must still be checked by hand.",
        ],
    },
}


def _png(svg_text: str, path: Path, bbox: dict | None, width: int = 1600) -> dict:
    from ..render.rasterize import rasterize_svg

    path.write_text(svg_text, encoding="utf-8")
    height = width
    if bbox and bbox.get("width") and bbox.get("height"):
        height = max(400, int(width * (bbox["height"] + 500) / (bbox["width"] + 500)))
    r = rasterize_svg(str(path), str(path.with_suffix(".png")), width=width, height=height)
    return {"svg": str(path), "png": r.get("png_path"), "png_note": None if r.get("ok") else r.get("reason")}


def latest_drc_report(project_dir: Path, pcb: Path) -> Path | None:
    """The newest DRC report Altium wrote for this board, if any."""
    cands = [p for p in project_dir.rglob("*.htm*") if "drc" in p.name.lower() or "design rule" in p.name.lower()]
    cands = [p for p in cands if p.stat().st_mtime >= pcb.stat().st_mtime - 7 * 86400]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _board_findings(doc: PcbDocument, gate: str, drc_report: Path | None) -> tuple[list[dict], list[dict], dict]:
    """(markers, ratsnest lines, numbers) for the board at this gate."""
    from ..analysis.copper import ratsnest
    from ..placement.check import check_plan
    from ..placement.footprints import board_box, footprints_of
    from ..render.file_geometry import ratsnest_lines

    markers: list[dict] = []
    numbers: dict[str, Any] = {}
    fps = footprints_of(doc)
    here = {"levers": {"body": "copper"}, "placements": [
        {"ref": f.ref, "x_mm": f.at.x, "y_mm": f.at.y, "rotation": f.at.rotation, "side": f.at.side} for f in fps]}
    chk = check_plan(fps, here, poly=list(doc.outline_poly or []), baseline=False)
    numbers["placement"] = chk["counts"]
    centre = {f.ref: board_box(f, True) for f in fps}
    side_of = {f.ref: (None if f.through_hole else f.at.side) for f in fps}
    for fnd in chk["findings"]:
        if gate in ("routing", "verification") and fnd["code"] == "decap_far":
            continue
        for it in fnd.get("items") or []:
            b = centre.get(it.get("ref"))
            if b is None:
                continue
            markers.append({"x": round((b.min_x + b.max_x) / 2 * MILS_PER_MM, 1),
                            "y": round((b.min_y + b.max_y) / 2 * MILS_PER_MM, 1),
                            "r": max(30.0, max(b.w, b.h) / 2 * MILS_PER_MM + 10),
                            "side": side_of.get(it.get("ref")),
                            "severity": fnd["severity"], "what": f"{fnd['code']}: {it.get('ref')}"})
    lines: list[dict] = []
    if gate != "placement":
        rn = ratsnest(doc)
        numbers["unrouted_connections"] = rn["unrouted_connections"]
        numbers["open_nets"] = [o["net"] for o in rn["open_nets"]][:30]
        lines = ratsnest_lines(doc)
    if drc_report is not None and gate in ("routing", "verification"):
        from ..analysis.drc_report import summarize_file

        s = summarize_file(drc_report)
        numbers["drc"] = {k: s.get(k) for k in ("violations", "copper_violations", "noise_violations", "complete",
                                                "by_rule")}
        numbers["drc"]["report"] = str(drc_report)
        ox, oy = (v / MILS_PER_MM for v in doc.origin_mil)
        for item in (s.get("copper") or [])[:60]:
            loc = item.get("location_mm")
            if loc:
                x, y = (loc[0] + loc[2]) / 2 - ox, (loc[1] + loc[3]) / 2 - oy
                markers.append({"x": round(x * MILS_PER_MM, 1), "y": round(y * MILS_PER_MM, 1), "r": 40,
                                "severity": "error", "what": item.get("message", item.get("rule", "DRC"))[:120]})
    for i, m in enumerate(markers, 1):
        m["label"] = str(i)
    return markers, lines, numbers


def review(project: str | None, gate: str, *, pcb_index: int = 0, sheets: list[str] | None = None,
           drc_report: str | None = None, profile: Any = None, width: int = 1600) -> dict:
    """Render and measure the saved design for ``gate``; returns pictures, numbers and the rubric."""
    from ..analysis.model import load_model
    from ..offline.context import output_dir_for, resolve_project, safe_path
    from ..offline.errors import InvalidArgument
    from ..render import (
        PcbRenderOptions,
        SchRenderOptions,
        render_pcb_svg,
        render_sch_svg,
    )
    from ..render.file_geometry import pcb_geometry, sch_geometry

    if gate not in GATES:
        raise InvalidArgument(f"gate must be one of {list(GATES)}")
    spec = GATES[gate]
    proj = resolve_project(project)
    model = load_model(proj)
    out = output_dir_for(proj, "reviews") / f"{gate}-{_dt.datetime.now(_dt.UTC):%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    images: list[dict] = []
    numbers: dict[str, Any] = {}
    findings: list[dict] = []

    if "sheets" in spec["draw"]:
        from ..analysis.erc import run_erc

        e = run_erc(model)
        numbers["erc"] = e["summary"]
        findings += [{"where": ",".join(f.get("sheets") or []), "severity": f["severity"], "what": f["message"]}
                     for f in e["findings"] if f["severity"] == "error"][:40]
        wanted = {s.lower() for s in sheets or []}
        for sh in model.sheets:
            if wanted and sh.path.name.lower() not in wanted and sh.path.stem.lower() not in wanted:
                continue
            if not sh.components and not sh.wires:
                continue
            g = sch_geometry(sh)
            svg = render_sch_svg(g, SchRenderOptions())
            images.append({"view": f"sheet {sh.path.name}",
                           **_png(svg, out / f"sheet-{sh.path.stem}.svg", None, width)})

    doc = model.pcb(pcb_index) if any(v in spec["draw"] for v in ("top", "bottom")) else None
    if doc is not None:
        rep = safe_path(drc_report) if drc_report else latest_drc_report(proj.parent, doc.path)
        markers, lines, n = _board_findings(doc, gate, rep)
        numbers.update(n)
        findings += [{"label": m["label"], "severity": m["severity"], "what": m["what"]} for m in markers]
        g = pcb_geometry(doc)
        g["review"] = {"markers": markers, "lines": lines}
        for side in ("top", "bottom"):
            if side in spec["draw"]:
                svg = render_pcb_svg(g, PcbRenderOptions(interactive_legend=False, view_side=side))
                images.append({"view": f"board {side}", **_png(svg, out / f"board-{side}.svg", g["bbox"], width)})
        if profile is not None and gate in ("routing", "verification"):
            from ..placement.service import load_profile
            from ..reference.compare import compare_board

            c = compare_board(load_profile(profile), doc)
            numbers["style"] = {"matches": c["matches"], "findings": [f["code"] for f in c["findings"]]}
            from ..routing.via_style import check as via_check

            numbers["vias"] = {"matches": via_check(doc, load_profile(profile))["matches"]}
    return {
        "gate": gate, "stages": list(spec["stages"]), "folder": str(out), "images": images,
        "numbers": numbers, "findings": findings, "rubric": spec["rubric"],
        "how": ("Read every PNG. Answer each rubric item yes/no with what you saw (cite marker numbers). "
                "Numbers decide pass/fail; the pictures add what numbers miss. Then record the gate with "
                "design_session_log(event='stage_result', stage=<stage>, status='ok'|'blocked', "
                "text=<your answers>, data=<the numbers>) and do not pass a gate with an open error marker."),
        "note": "From the saved files: save in Altium first; unsaved edits are not in the review.",
    }
