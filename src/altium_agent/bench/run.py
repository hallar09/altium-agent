# SPDX-License-Identifier: Apache-2.0
"""Run a task, then score what it produced.

Two runners produce a result for a task folder:

* ``baseline`` - the tools' defaults, no model: ``place_plan`` with the
  fixed parts kept, or the in-house engine on the stripped board. What a skill has
  to beat to be worth its tokens.
* ``agent`` - Claude Code headless (``claude -p``) in the task folder, with
  only the AltiumAgent server (pointed at the mask) and only the skills
  given to the run. Its transcript and cost are kept.

Either way the result is a small dict (``{"plan": path}``, ``{"ses":
path}`` or ``{"pcb": path}``) and :func:`score_task` turns it into numbers
next to the person's.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..offline.files.pcbdoc import read_pcbdoc
from . import mask
from .score import board_metrics, placement_metrics, ratio, result_metrics, ses_copper, trial_route
from .transform import apply_placements


@dataclass
class RunOptions:
    # trial routing (place tasks) and the route baseline
    route: bool = True
    two_pass: bool = True    # the power fan-out, then the rest, then stitching, as the broker plans it
    # agent
    claude: str | None = None
    model: str | None = None
    skills: list[str] = field(default_factory=list)
    toolset: str = "core"
    budget_usd: float = 5.0
    timeout_s: int = 3600
    prompt: str = "Read BRIEF.md in this folder and do the task it describes."
    label: str = ""          # names the transcript and MCP config, so reruns keep both


@contextlib.contextmanager
def task_env(run_dir: Path, entry: dict):
    """The process environment a task's tools see: the run's mask and the
    task's own output folder. Restored afterwards."""
    saved = {k: os.environ.get(k) for k in (mask.ENV, "ALTIUM_AGENT_OUTPUT_DIR")}
    os.environ[mask.ENV] = str(run_dir / "mask.json")
    os.environ["ALTIUM_AGENT_OUTPUT_DIR"] = entry["outputs"]
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _answer(run_dir: Path, entry: dict) -> dict:
    return json.loads((run_dir / "answers" / f"{entry['board']}.json").read_text(encoding="utf-8"))


def _newest(folder: Path, pattern: str, since: float = 0.0) -> Path | None:
    """The newest match made at or after ``since`` (so another runner's
    file in the same folder is never taken for this one's)."""
    found = sorted((p for p in folder.rglob(pattern) if p.stat().st_mtime >= since),
                   key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def run_baseline(run_dir: Path, entry: dict, opts: RunOptions) -> dict:
    """The defaults, no model."""
    t0 = time.time()
    if entry["task"] == "place":
        from ..placement.service import place_plan

        ans = _answer(run_dir, entry)
        with task_env(run_dir, entry):
            out = place_plan(project=entry["project"], levers={"keep": ans["fixed"]},
                             antennas=ans.get("antennas") or None, name="baseline")
        if not out.get("plan_path"):
            return {"error": "place_plan returned no plan", "detail": out, "seconds": round(time.time() - t0, 1)}
        return {"plan": out["plan_path"], "seconds": round(time.time() - t0, 1)}
    # route: the in-house engine on the stripped board; score_task routes it.
    return {"trial": True, "seconds": round(time.time() - t0, 1)}


def find_claude(hint: str | None = None) -> str:
    for cand in (hint, shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude.exe"),
                 str(Path.home() / ".local" / "bin" / "claude")):
        if cand and Path(cand).exists():
            return cand
        if cand and shutil.which(cand):
            return shutil.which(cand)
    raise SystemExit("claude (Claude Code) not found; pass --claude <path>")


def _server_command() -> list[str]:
    exe = Path(sys.executable).with_name("altium-agent.exe")
    if not exe.exists():
        exe = Path(sys.executable).with_name("altium-agent")
    return [str(exe)] if exe.exists() else [sys.executable, "-m", "altium_agent"]


def agent_command(run_dir: Path, entry: dict, opts: RunOptions) -> tuple[list[str], Path]:
    """The ``claude -p`` command line for a task, and its MCP config file."""
    cmd = _server_command()
    cfg = run_dir / "mcp" / f"{_stem(entry, opts)}.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"mcpServers": {"AltiumAgent": {
        "type": "stdio", "command": cmd[0], "args": cmd[1:],
        "env": {mask.ENV: str(run_dir / "mask.json"), "ALTIUM_AGENT_OUTPUT_DIR": entry["outputs"],
                "ALTIUM_AGENT_TOOLSET": opts.toolset, "ALTIUM_AGENT_DISABLE_DASHBOARD": "1"}}}}, indent=1),
        encoding="utf-8")
    args = [find_claude(opts.claude), "-p", opts.prompt,
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(cfg), "--strict-mcp-config",
            # Only the task folder's own settings and skills: the run's skills,
            # not whatever is installed for the user.
            "--setting-sources", "project",
            "--permission-mode", "dontAsk",
            "--allowedTools", "mcp__AltiumAgent", "Read", "Write", "Edit", "Glob", "Grep", "Skill",
            "--max-budget-usd", str(opts.budget_usd),
            "--no-session-persistence"]
    if opts.model:
        args += ["--model", opts.model]
    return args, cfg


#: Set by a Claude Code session for the tools it runs. A ``claude -p`` started
#: from inside one (this benchmark run from a Claude chat) would take them as
#: its own and look for the host's sign-in instead of the user's.
_HOST_SESSION_VARS = ("CLAUDECODE", "ANTHROPIC_BASE_URL", "CLAUDE_PID", "CLAUDE_EFFORT")
_HOST_SESSION_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PREVIEW_")


def agent_env(environ) -> dict[str, str]:
    """The environment for ``claude -p``: the user's, without the bench's own
    variables (the agent gets those through its MCP config only) and, when
    run from inside a Claude Code session, without that session's."""
    env = {k: v for k, v in environ.items() if k not in (mask.ENV, "ALTIUM_AGENT_OUTPUT_DIR")}
    if "CLAUDECODE" in env or "CLAUDE_CODE_HOST_SESSION_ID" in env:
        env = {k: v for k, v in env.items()
               if k not in _HOST_SESSION_VARS and not k.startswith(_HOST_SESSION_PREFIXES)}
    return env


def install_skills(folder: Path, skills: list[str]) -> list[str]:
    """Copy skill folders (each holding a SKILL.md) into the task folder's
    ``.claude/skills``; returns their names."""
    names = []
    for s in skills:
        src = Path(s)
        if src.is_file():
            src = src.parent
        if not (src / "SKILL.md").exists():
            raise SystemExit(f"{s}: no SKILL.md")
        shutil.copytree(src, folder / ".claude" / "skills" / src.name, dirs_exist_ok=True)
        names.append(src.name)
    return names


def _stem(entry: dict, opts: RunOptions) -> str:
    return f"{entry['name']}@{opts.label}" if opts.label else entry["name"]


def run_agent(run_dir: Path, entry: dict, opts: RunOptions) -> dict:
    """Claude Code on the task; the result it wrote, or an error."""
    folder = Path(entry["folder"])
    log = run_dir / "transcripts" / f"{_stem(entry, opts)}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    old = folder / "RESULT.json"
    if old.exists():
        # An earlier run's answer (and its reasoning) must not be there for
        # this agent to read: keep it beside the transcripts instead.
        old.replace(log.with_name(f"{entry['name']}.RESULT-{int(old.stat().st_mtime)}.json"))
    skills = install_skills(folder, opts.skills)
    args, cfg = agent_command(run_dir, entry, opts)
    t0 = time.time()
    env = agent_env(os.environ)
    try:
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.run(args, cwd=folder, stdout=fh, stderr=subprocess.STDOUT, env=env,
                                  timeout=opts.timeout_s, check=False)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = "timeout"
    rec: dict = {"seconds": round(time.time() - t0, 1), "exit_code": code, "transcript": str(log),
                 "skills": skills, "model": opts.model, "toolset": opts.toolset, **transcript_stats(log)}
    if rec.get("is_error") or code == "timeout":
        # Claude Code itself failed (not signed in, budget spent, timed out):
        # whatever is in the folder is not this run's work.
        rec["error"] = f"the agent run failed: {rec.get('final') or code}"
        return rec
    out = folder / "RESULT.json"
    if out.exists() and out.stat().st_mtime >= t0 - 2:   # file times can trail the clock
        try:
            rec.update(json.loads(out.read_text(encoding="utf-8")))
        except ValueError as exc:
            rec["error"] = f"RESULT.json is not JSON: {exc}"
    elif entry["task"] == "place" and (p := _newest(Path(entry["outputs"]), "*.json", since=t0)):
        rec["plan"], rec["fallback"] = str(p), "no RESULT.json: the newest plan this run made was scored"
    else:
        rec["error"] = "the agent wrote no RESULT.json"
    return rec


def transcript_stats(log: Path) -> dict:
    """Cost, turns and tool calls from a stream-json transcript."""
    calls: dict[str, int] = {}
    final: dict = {}
    if not log.exists():
        return {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    calls[block["name"]] = calls.get(block["name"], 0) + 1
        elif ev.get("type") == "result":
            final = ev
    return {"cost_usd": final.get("total_cost_usd"), "turns": final.get("num_turns"),
            "is_error": final.get("is_error"), "stop": final.get("subtype"),
            "final": (final.get("result") or "")[:500] or None,
            "tool_calls": dict(sorted(calls.items(), key=lambda kv: -kv[1]))}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _versus(got: dict, human: dict, keys: dict[str, str]) -> dict:
    return {name: ratio(got.get(k), human.get(k)) for name, k in keys.items()}


def score_task(run_dir: Path, entry: dict, result: dict, opts: RunOptions) -> dict:
    """The numbers for one task's result, beside the person's."""
    ans = _answer(run_dir, entry)
    out: dict = {"name": entry["name"], "board": entry["board"], "task": entry["task"], "result": result}
    if result.get("error"):
        out["ok"] = False
        return out
    work = Path(entry["outputs"]) / "score"
    with task_env(run_dir, entry):
        doc = read_pcbdoc(Path(entry["pcb"]))          # masked: the task as given
    if entry["task"] == "place":
        plan = json.loads(Path(result["plan"]).read_text(encoding="utf-8"))
        fixed = {r.upper() for r in ans["fixed"]}
        here = {c.designator.upper(): c for c in doc.components}
        # A fixed part stays where the brief put it; moving one is counted.
        moved_fixed = sorted(p["ref"] for p in plan.get("placements") or [] if p["ref"].upper() in fixed
                             and (abs(float(p["x_mm"]) - here[p["ref"].upper()].x_mm) > 0.01
                                  or abs(float(p["y_mm"]) - here[p["ref"].upper()].y_mm) > 0.01))
        placed = apply_placements(doc, [p for p in plan.get("placements") or [] if p["ref"].upper() not in fixed])
        pm = placement_metrics(placed, human=read_pcbdoc(Path(ans["source"])))
        pm["moved_fixed"] = moved_fixed
        pm["unplaced"] = sorted(set(ans["movable"]) - {p["ref"] for p in plan.get("placements") or []})
        hp = ans["placement"]
        out["placement"] = pm
        out["placement_vs_human"] = {
            **_versus(pm, hp, {"hpwl": "hpwl_signal_mm"}),
            "errors": pm["errors"] - hp["errors"],
            "decap_far": pm["decap_far"] - hp["decap_far"],
        }
        if opts.route:
            rm = trial_route(placed, work, stem="trial", two_pass=opts.two_pass)
            out["trial_route"] = rm
            out["routing_vs_human"] = _routing_vs(rm, ans["routing"])
    else:
        if result.get("trial"):
            rm = trial_route(doc, work, stem="route", two_pass=opts.two_pass)
        elif result.get("ses"):
            from ..routing.specctra import import_rules, layer_map

            # One SES, or several that together make the routing: their copper
            # is scored as one result.
            lm, hole = layer_map(doc), import_rules(doc)["via_hole_mm"]
            segs, dots = [], []
            for ses in ([result["ses"]] if isinstance(result["ses"], str) else list(result["ses"])):
                s_, d_ = ses_copper(Path(ses), lm, hole)
                segs += s_
                dots += d_
            rm = result_metrics(doc, segs, dots)
        elif result.get("pcb"):
            rm = board_metrics(read_pcbdoc(Path(result["pcb"])))     # the saved board, unmasked
        else:
            out["ok"] = False
            out["result"] = {**result, "error": "nothing to score: no ses, pcb or trial"}
            return out
        out["routing"] = rm
        out["routing_vs_human"] = _routing_vs(rm, ans["routing"])
    out["ok"] = True
    return out


def _routing_vs(rm: dict, human: dict) -> dict:
    if rm.get("error"):
        return {"error": rm["error"]}
    return {
        "completion": rm["all"]["completion"], "human_completion": human["all"]["completion"],
        "signal_completion": rm["signal"]["completion"],
        "human_signal_completion": human["signal"]["completion"],
        "vias": ratio(rm["vias"], human["vias"]),
        "length": ratio(rm["length_mm"], human["length_mm"]),
    }


def run_task(run_dir: str, entry: dict, runner: str, label: str, opts: RunOptions) -> dict:
    """Run one task with ``runner`` and write its scored record (a module-level
    function, so a process pool on Windows can import it)."""
    run_dir_p = Path(run_dir)
    try:
        result = run_baseline(run_dir_p, entry, opts) if runner == "baseline" else run_agent(run_dir_p, entry, opts)
        rec = score_task(run_dir_p, entry, result, opts)
    except Exception as exc:  # noqa: BLE001 - one task failing is that task's record
        rec = {"name": entry["name"], "board": entry["board"], "task": entry["task"], "ok": False,
               "result": {"error": f"{type(exc).__name__}: {exc}"}}
    rec["label"], rec["runner"], rec["options"] = label, runner, asdict(opts)
    out = run_dir_p / "results" / f"{entry['name']}@{label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    return rec
