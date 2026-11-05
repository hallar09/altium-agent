# SPDX-License-Identifier: Apache-2.0
"""Lay out a benchmark run: copies, masks, answers and briefs.

A run folder holds::

    run.json                 what was run: boards, tasks, options, code version
    mask.json                what each copy is shown as (bench/mask.py)
    answers/<board>.json     the person's figures; never inside a task folder
    tasks/<board>-<task>/    the copied project a runner works in, BRIEF.md
    outputs/<board>-<task>/  ALTIUM_AGENT_OUTPUT_DIR for that task (plans, DSNs)
    results/ transcripts/    written by the runners

Tasks:

* ``place``: routing stripped, every part a brief would not pin moved beside
  the board. The result is a placement plan; it is scored on its own and by
  a trial route.
* ``route``: routing stripped, the person's placement kept. The result is a
  routing (an SES, or a finished board saved by Altium).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import mask
from .corpus import BoardSpec, fixed_parts
from .score import human_reference, plane_nets
from .transform import positions

TASKS = ("place", "route")
#: Not copied: Altium's backups and generated outputs.
_SKIP = shutil.ignore_patterns("History", "Project Outputs*", "__Previews", "*.zip", "*.PcbDocPreview",
                               "*.SchDocPreview", "Project Logs*")


def default_runs_dir() -> Path:
    return Path.home() / "Altium Agent" / "bench" / "runs"


def task_name(board: str, task: str) -> str:
    return f"{board}-{task}"


def _brief(spec: BoardSpec, task: str, doc, fixed: list[str], project: Path, staged: int) -> str:
    bbox = doc.outline_bbox() or {}
    pos = positions(doc)
    layers = len(doc.copper_layers) + sum(1 for s in doc.stack if s.kind == "plane")
    lines = [
        f"# Benchmark task: {task} `{spec.id}`",
        "",
        f"Project: `{project}`",
        f"Board: `{project.parent / spec.pcb}`, {layers} copper layers, outline "
        f"{bbox.get('width_mm', '?')} x {bbox.get('height_mm', '?')} mm, {len(doc.components)} parts.",
        "",
        "Altium is not running. Use the AltiumAgent tools that work from the saved files.",
        "",
    ]
    if task == "place":
        lines += [
            "## What to do",
            "",
            f"{staged} parts were imported and sit beside the board. Place them inside the outline,",
            "ready to route. These parts are fixed by the brief and stay where they are:",
            "",
        ]
        lines += [f"- {r}: x {pos[r]['x_mm']} mm, y {pos[r]['y_mm']} mm, rotation {pos[r]['rotation']}, "
                  f"{pos[r]['side']}" for r in fixed] or ["- (none)"]
        if spec.antennas:
            lines += ["", "Antenna keep-outs (as `place_plan` asks for them), from the reference design:", ""]
            lines += [f"- {r}: {a}" for r, a in sorted(spec.antennas.items())]
        lines += [
            "",
            "## How to finish",
            "",
            "Place the parts yourself in a placement session (`place_session_start` on the project above,",
            "then `place_session_move`, `place_session_check`, `place_session_view`), and export it",
            "(`place_session_export`). You cannot apply a plan here.",
            "When you are done, write `RESULT.json` in this folder:",
            "",
            '    {"plan": "<full path of the plan you chose>", "notes": "<one paragraph: what you did and why>"}',
        ]
    else:
        lines += [
            "## What to do",
            "",
            "The board is placed and unrouted. Route it completely.",
            f"Nets joined by pours or planes: {', '.join(sorted(plane_nets(doc))) or 'none'}.",
            "",
            "## How to finish",
            "",
            "Route it yourself in a routing session (`route_session_start` on the project above, then",
            "`route_session_fanout`, `route_session_route` / `route_session_draw`, `route_session_check`,",
            "`route_session_view`), and export it (`route_session_export`). You cannot import it here.",
            "When you are done, write `RESULT.json` in this folder naming the exported SES and the rules",
            "you chose:",
            "",
            '    {"ses": ["<the .ses route_session_export wrote>"], "rules": [<your rule table rows>],',
            '     "notes": "<one paragraph: what you chose and why>"}',
        ]
    return "\n".join(lines) + "\n"


def prepare(specs: list[BoardSpec], tasks: list[str], run_dir: Path, *, options: dict | None = None,
            code: dict | None = None) -> dict:
    """Create ``run_dir`` for ``specs`` x ``tasks``; returns the run record."""
    bad = [t for t in tasks if t not in TASKS]
    if bad:
        raise SystemExit(f"unknown task(s): {', '.join(bad)} (known: {', '.join(TASKS)})")
    run_dir.mkdir(parents=True, exist_ok=False)
    boards: dict[str, dict] = {}
    masks: dict[str, dict] = {}
    entries = []
    for spec in specs:
        human = spec.read()
        fixed = fixed_parts(human, spec)
        movable = sorted(set(c.designator for c in human.components) - set(fixed))
        answer = {"board": spec.id, "source": str(spec.pcb_path), "fixed": fixed, "movable": movable,
                  "antennas": spec.antennas,
                  "positions": positions(human), **human_reference(human)}
        (run_dir / "answers").mkdir(exist_ok=True)
        (run_dir / "answers" / f"{spec.id}.json").write_text(json.dumps(answer, indent=1), encoding="utf-8")
        for task in tasks:
            name = task_name(spec.id, task)
            folder = run_dir / "tasks" / name
            shutil.copytree(spec.project.parent, folder, ignore=_SKIP)
            project = folder / spec.project.name
            pcb = folder / spec.pcb
            entry = {"strip_routing": True}
            if task == "place":
                entry["stage"] = movable
            masks[str(pcb)] = entry
            (folder / "BRIEF.md").write_text(
                _brief(spec, task, human, fixed, project, len(movable) if task == "place" else 0),
                encoding="utf-8")
            (run_dir / "outputs" / name).mkdir(parents=True, exist_ok=True)
            entries.append({"name": name, "board": spec.id, "task": task, "folder": str(folder),
                            "project": str(project), "pcb": str(pcb),
                            "outputs": str(run_dir / "outputs" / name)})
        boards[spec.id] = {**asdict(spec), "project": str(spec.project), "fixed_resolved": fixed,
                           "movable": len(movable)}
    mask.write(run_dir / "mask.json", masks)
    record = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "boards": boards,
              "tasks": entries, "options": options or {}, "code": code or {}}
    (run_dir / "run.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def load_run(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
