# SPDX-License-Identifier: Apache-2.0
"""House style and placement, as the tools call them (Altium Agent).

Plain functions, files in and JSON out, so tests call them without a
server: ``ref_profile`` / ``ref_compare`` measure boards; ``place_plan``
places a board's parts offline and saves the plan; ``place_check`` checks a
plan; ``apply_ops`` turns a checked plan into the bridge's absolute-mil
placement list. Only the place_apply tool talks to Altium.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from ..offline.context import (
    _forbidden_write_roots,
    _within,
    output_dir_for,
    resolve_project,
    safe_path,
)
from ..offline.errors import InvalidArgument
from ..offline.files.pcbdoc import PcbDocument, read_pcbdoc
from ..units import MILS_PER_MM
from .antenna import AntennaKeepout, antenna_question, parse_antenna_answer, rf_modules
from .check import check_plan
from .footprints import footprints_of
from .placer import place_board
from .shape import outline_from_points

PLAN_VERSION = 1
#: Profiles are shared between projects, so they live in one folder.
PROFILE_HOME = "_house_style"


# ---------------------------------------------------------------- loading

class Board:
    """A board read from its files, with what the schematic adds."""

    def __init__(self, doc: PcbDocument, project: Path | None, sheet_of: dict[str, str],
                 descriptions: dict[str, str]):
        self.doc, self.project = doc, project
        self.sheet_of, self.descriptions = sheet_of, descriptions


def load_board(project: str | None = None, pcb: str | None = None, pcb_index: int = 0) -> Board:
    """The board from ``pcb`` (a .PcbDoc), or the project's PcbDoc."""
    from ..analysis.model import load_model

    proj: Path | None = None
    sheet_of: dict[str, str] = {}
    desc: dict[str, str] = {}
    if project or not pcb:
        proj = resolve_project(project)
        model = load_model(proj)
        for c in model.components():
            if c.sheets:
                sheet_of[c.designator] = c.sheets[0]
            if c.description:
                desc[c.designator] = c.description
        if not pcb:
            doc = model.pcb(pcb_index)
            if doc is None:
                raise InvalidArgument(f"{proj.name} has no PcbDoc")
            return Board(doc, proj, sheet_of, desc)
    path = safe_path(pcb, suffixes=(".pcbdoc",))
    return Board(read_pcbdoc(path), proj, sheet_of, desc)


def _owner(board: Board) -> Path:
    return board.project or board.doc.path


def _write_json(folder: Path, stem: str, data: dict) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in stem).strip("._") or "unnamed"
    path = folder / f"{safe}.json"
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def profiles_dir() -> Path:
    return output_dir_for(Path(PROFILE_HOME), "profiles")


# ---------------------------------------------------------------- house style

def ref_profile(project: str | None = None, pcb: str | None = None, name: str | None = None,
                save: bool = True) -> dict:
    from ..reference.profile import levers_from_profile, profile_board

    b = load_board(project, pcb)
    prof = profile_board(b.doc, b.sheet_of, name=name or b.doc.path.stem)
    prof["levers"] = levers_from_profile(prof)
    out: dict[str, Any] = {"profile": prof}
    if save:
        out["saved_to"] = str(_write_json(profiles_dir(), prof["name"], prof))
    return out


def load_profile(profile: Any) -> dict:
    """A profile given as a dict, a JSON path, or the name it was saved under."""
    if isinstance(profile, dict):
        data = profile
    else:
        text = str(profile or "").strip()
        if not text:
            raise InvalidArgument("no profile given")
        cand = Path(text).expanduser()
        if cand.suffix.lower() != ".json":
            cand = profiles_dir() / f"{text}.json"
        if not cand.is_file():
            saved = sorted(p.stem for p in profiles_dir().glob("*.json"))
            raise InvalidArgument(f"no profile {text!r}", hint=f"saved profiles: {saved or 'none'}")
        data = json.loads(cand.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "placement" not in data:
        raise InvalidArgument("not a house-style profile (made by ref_profile)")
    return data


def ref_compare(profile: Any, project: str | None = None, pcb: str | None = None,
                strictness: str = "range") -> dict:
    from ..reference.compare import STRICTNESS, compare_board

    if strictness not in STRICTNESS:
        raise InvalidArgument(f"strictness must be one of {list(STRICTNESS)}")
    ref = load_profile(profile)
    b = load_board(project, pcb)
    out = compare_board(ref, b.doc, b.sheet_of, strictness)
    out.pop("profile", None)
    return out


# ---------------------------------------------------------------- placement

def _antennas(fps, descriptions: dict[str, str], answers: dict | None) -> tuple[dict, list[dict]]:
    """Keep-outs from the answers, and the questions still open."""
    answers = {str(k).upper(): v for k, v in (answers or {}).items()}
    keep: dict[str, AntennaKeepout] = {}
    questions = []
    for m in rf_modules(fps, descriptions):
        raw = answers.get(m.ref.upper())
        if raw is None:
            questions.append({"ref": m.ref, "question": antenna_question(m)})
            continue
        if isinstance(raw, dict):
            raw = f"{raw.get('end', '')} {raw.get('depth_mm', '')} {raw.get('beyond_mm', 0)}"
        parsed = parse_antenna_answer(str(raw))
        if parsed is None:
            questions.append({"ref": m.ref, "question": antenna_question(m),
                              "error": f"could not read the answer {raw!r}"})
        elif parsed != "none":
            keep[m.ref] = parsed
    return keep, questions


def place_plan(project: str | None = None, pcb: str | None = None, levers: dict | None = None,
               profile: Any = None, antennas: dict | None = None, keep_outline: bool = True,
               name: str | None = None) -> dict:
    b = load_board(project, pcb)
    fps = footprints_of(b.doc, b.sheet_of)
    if not fps:
        raise InvalidArgument("the board has no components; run Import Changes from the schematic first")
    keep, questions = _antennas(fps, b.descriptions, antennas)
    if questions:
        return {"ok": False, "needs_answers": True, "questions": questions,
                "hint": "Pass antennas={ref: answer} (e.g. {\"U5\": \"top 6 5\"} or \"none\") and plan again."}
    lev: dict[str, Any] = {}
    prof = None
    if profile is not None:
        prof = load_profile(profile)
        lev.update(prof.get("levers") or {})
    lev.update(levers or {})
    outline = None
    poly = list(b.doc.outline_poly or [])
    if keep_outline:
        if len(poly) < 3:
            raise InvalidArgument("the board has no outline to keep; pass keep_outline=false and a shape lever")
        outline = outline_from_points(poly)
    layers = len(b.doc.copper_layers) or 2
    plan = place_board(fps, lev, signal_layers=max(1, layers - (2 if layers >= 4 else 0)),
                       antennas=keep, outline=outline)
    stat = b.doc.path.stat()
    plan.update({
        "plan_version": PLAN_VERSION,
        "source": str(b.doc.path),
        "project": str(b.project) if b.project else None,
        "origin_mil": list(b.doc.origin_mil),
        "source_mtime": stat.st_mtime,
        "created": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "profile": prof.get("name") if prof else None,
        "decap_max_mm": ((prof or {}).get("decoupling") or {}).get("distance_mm", {}).get("p90") if prof else None,
        "antennas": {r: k.to_dict() for r, k in keep.items()},
        "kept_outline": bool(keep_outline),
    })
    check = check_plan(fps, plan, poly=poly if keep_outline else None, decap_max_mm=plan["decap_max_mm"])
    plan["check"] = {"ok": check["ok"], "counts": check["counts"],
                     "errors": [f["code"] for f in check["findings"] if f["severity"] == "error"]}
    stem = name or f"{b.doc.path.stem}-{_dt.datetime.now(_dt.UTC):%Y%m%d-%H%M%S}"
    saved = _write_json(output_dir_for(_owner(b), "plans"), stem, plan)
    return {"plan_path": str(saved), "score": plan["score"], "levers": plan["levers"],
            "notes": plan["notes"], "check": check, "parts": len(plan["placements"]),
            "iterations": plan["iterations"]}


def load_plan(plan: Any) -> dict:
    if isinstance(plan, dict):
        data = plan
    else:
        path = safe_path(plan, suffixes=(".json",))
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "placements" not in data or "source" not in data:
        raise InvalidArgument("not a placement plan (made by place_plan)")
    return data


def place_check(plan: Any, pcb: str | None = None) -> dict:
    p = load_plan(plan)
    path = pcb or p["source"]
    doc = read_pcbdoc(safe_path(path, suffixes=(".pcbdoc",)))
    out = check_plan(footprints_of(doc), p, poly=list(doc.outline_poly or []) if p.get("kept_outline", True) else None,
                     decap_max_mm=p.get("decap_max_mm"))
    if Path(path).exists() and p.get("source_mtime") and abs(Path(path).stat().st_mtime - p["source_mtime"]) > 1:
        out["stale"] = "the board file changed after this plan was made; re-plan if parts were added or moved"
    return out


def is_protected_design(path: str) -> bool:
    """A sample or installed design: never edited (only the user's copies are)."""
    p = Path(path)
    return any(_within(p, r) for r in _forbidden_write_roots())


def apply_ops(plan: dict, origin_mil: tuple[float, float] | None = None) -> list[str]:
    """The bridge's ``desig,x_mil,y_mil,rot,layer`` list, in absolute mils."""
    ox, oy = origin_mil if origin_mil is not None else plan["origin_mil"]
    ops = []
    for pl in plan["placements"]:
        ref = str(pl["ref"])
        if any(ch in ref for ch in ",|"):
            raise InvalidArgument(f"designator {ref!r} cannot be sent to the bridge")
        x = round(ox + float(pl["x_mm"]) * MILS_PER_MM)
        y = round(oy + float(pl["y_mm"]) * MILS_PER_MM)
        rot = round(float(pl.get("rotation", 0)) % 360, 3)
        layer = "BottomLayer" if pl.get("side") == "bottom" else "TopLayer"
        ops.append(f"{ref},{x},{y},{rot:g},{layer}")
    return ops


def refuse_reason(plan: dict, accept_findings: bool = False) -> str | None:
    """Why place_apply must not apply this plan, or None."""
    src = plan.get("source", "")
    if is_protected_design(src):
        return (f"{src} is a sample or installed design; Altium Agent edits only your own copies. "
                "Copy the project to a folder of yours, open the copy, and plan again.")
    chk = plan.get("check") or {}
    if not accept_findings and not chk.get("ok", False):
        return (f"the plan's check found errors ({', '.join(chk.get('errors') or []) or 'see place_check'}); "
                "run place_check, fix or re-plan, or pass accept_findings=true to apply anyway")
    return None

