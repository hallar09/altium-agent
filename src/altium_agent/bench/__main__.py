# SPDX-License-Identifier: Apache-2.0
"""``python -m altium_agent.bench``: the placement and routing benchmark.

    prepare  copy the boards, strip them, write briefs and answers
    run      run a runner (baseline or agent) on a run's tasks and score them
    score    score a result made elsewhere (a live Altium run, a hand-made SES)
    report   the run's results as a table (also written to report.md)

Typical loop::

    python -m altium_agent.bench prepare --tasks place route
    python -m altium_agent.bench run RUN --runner baseline
    python -m altium_agent.bench run RUN --runner agent --skill skills/placement --label placement-v1
    python -m altium_agent.bench report RUN
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

from . import corpus, prepare as prep, report
from .run import RunOptions, run_task, score_task


def _code() -> dict:
    from ..layout.bench_run import code_version

    try:
        return code_version()
    except Exception:  # noqa: BLE001 - a record, not a requirement
        return {}


def _options(a: argparse.Namespace) -> RunOptions:
    return RunOptions(route=not a.no_route, two_pass=not a.one_pass,
                      claude=a.claude, model=a.model, skills=[str(Path(s).resolve()) for s in a.skill or []],
                      toolset=a.toolset, budget_usd=a.budget, timeout_s=a.timeout,
                      **({"prompt": a.prompt} if a.prompt else {}))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m altium_agent.bench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="make a run folder")
    p.add_argument("--manifest", type=Path, default=None)
    p.add_argument("--boards", nargs="*", help="board ids (default: all)")
    p.add_argument("--max-tier", type=int, default=None)
    p.add_argument("--tasks", nargs="+", default=["place", "route"], choices=prep.TASKS)
    p.add_argument("--runs-dir", type=Path, default=prep.default_runs_dir())
    p.add_argument("--name", default=None, help="run folder name (default: a timestamp)")

    r = sub.add_parser("run", help="run a runner on a run's tasks and score them")
    r.add_argument("run_dir", type=Path)
    r.add_argument("--runner", choices=("baseline", "agent"), default="baseline")
    r.add_argument("--label", default=None, help="name for this runner's results (default: the runner)")
    r.add_argument("--only", nargs="*", help="task names (e.g. wifi-place)")
    r.add_argument("--jobs", type=int, default=1)
    r.add_argument("--no-route", action="store_true", help="skip the trial route of placements")
    r.add_argument("--one-pass", action="store_true",
                   help="let the engine route every net at once, without the power fan-out first")
    r.add_argument("--skill", action="append", help="a skill folder to give the agent (repeatable)")
    r.add_argument("--model", default=None)
    r.add_argument("--claude", default=None, help="path of the claude executable")
    r.add_argument("--toolset", default="core", choices=("core", "full", "minimal"))
    r.add_argument("--budget", type=float, default=5.0, help="max USD per task (agent)")
    r.add_argument("--timeout", type=int, default=3600, help="seconds per task (agent)")
    r.add_argument("--prompt", default=None)

    s = sub.add_parser("score", help="score a result made outside the runners")
    s.add_argument("run_dir", type=Path)
    s.add_argument("task", help="task name, e.g. kame-route")
    s.add_argument("--label", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", type=Path)
    g.add_argument("--ses", type=Path)
    g.add_argument("--pcb", type=Path, help="a routed board saved by Altium")
    s.add_argument("--no-route", action="store_true")

    rp = sub.add_parser("report", help="table of a run's results")
    rp.add_argument("run_dir", type=Path)

    a = ap.parse_args(argv)

    if a.cmd == "prepare":
        specs = corpus.load(a.manifest, a.boards, a.max_tier)
        run_dir = a.runs_dir / (a.name or datetime.now().strftime("%Y%m%d-%H%M%S"))
        rec = prep.prepare(specs, a.tasks, run_dir, options={"tasks": a.tasks}, code=_code())
        print(f"{run_dir}\n{len(rec['tasks'])} tasks: {', '.join(t['name'] for t in rec['tasks'])}")
        for b, v in rec["boards"].items():
            print(f"  {b}: {v['movable']} movable, fixed {', '.join(v['fixed_resolved']) or '-'}")
        return 0

    run_dir = a.run_dir.resolve()
    record = prep.load_run(run_dir)
    tasks = {t["name"]: t for t in record["tasks"]}

    if a.cmd == "run":
        names = a.only or list(tasks)
        unknown = [n for n in names if n not in tasks]
        if unknown:
            raise SystemExit(f"not in this run: {', '.join(unknown)} (tasks: {', '.join(tasks)})")
        label = a.label or a.runner
        opts = _options(a)
        opts.label = label
        if a.runner == "agent" and a.jobs > 1:
            print("note: agent tasks run one at a time (--jobs ignored)", file=sys.stderr)
        jobs = 1 if a.runner == "agent" else max(1, a.jobs)
        if jobs == 1:
            recs = []
            for n in names:
                print(f"{n} ...", flush=True)
                recs.append(run_task(str(run_dir), tasks[n], a.runner, label, opts))
                print("  " + json.dumps({k: v for k, v in report.row(recs[-1]).items() if v is not None}))
        else:
            with ProcessPoolExecutor(jobs) as ex:
                futs = [ex.submit(run_task, str(run_dir), tasks[n], a.runner, label, opts) for n in names]
                recs = [f.result() for f in futs]
        text = report.render(run_dir)
        (run_dir / "report.md").write_text(text, encoding="utf-8")
        print(text)
        return 0 if all(r.get("ok") for r in recs) else 1

    if a.cmd == "score":
        if a.task not in tasks:
            raise SystemExit(f"not in this run: {a.task}")
        opts = RunOptions(route=not a.no_route)
        result = {k: str(getattr(a, k)) for k in ("plan", "ses", "pcb") if getattr(a, k)}
        rec = score_task(run_dir, tasks[a.task], result, opts)
        rec["label"], rec["runner"] = a.label, "external"
        out = run_dir / "results" / f"{a.task}@{a.label}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
        print(json.dumps(report.row(rec), indent=1))
        return 0 if rec.get("ok") else 1

    text = report.render(run_dir)
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
