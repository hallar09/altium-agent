# SPDX-License-Identifier: Apache-2.0
"""Nothing in Altium Agent starts X2.EXE, and nothing runs a script by
launching it per call.

BoardKit learned both the hard way: a second X2.EXE takes the bridge's
requests, and ``X2.EXE -RScriptingSystem:RunScript`` per call is slow and
racy. The one place that hands a script to Altium, ``altium-agent
relaunch``, is a command the user types once, and it refuses unless
exactly one Altium is already running.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "altium_agent"
_LAUNCHERS = {"Popen", "run", "call", "check_call", "check_output",
              "system", "startfile", "spawnv", "spawnl", "execv", "execl",
              "ShellExecute", "ShellExecuteW", "CreateProcess"}
_ALTIUM = re.compile(r"x2(\.exe)?\b|RunScript|ScriptingSystem", re.IGNORECASE)


def _launch_sites():
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in _LAUNCHERS:
                yield path, node


def test_no_module_launches_altium_or_a_script_through_it():
    offenders = []
    for path, node in _launch_sites():
        text = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
        if _ALTIUM.search(text):
            offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    # cli.cmd_relaunch passes a command built by relaunch_command(), so the
    # literal never appears at the call site; it is checked separately below.
    assert not offenders, f"X2.EXE launched from: {offenders}"


def test_the_runscript_command_is_built_in_exactly_one_place():
    hits = []
    for path in SRC.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "-RScriptingSystem:RunScript" in code:
                hits.append(f"{path.relative_to(SRC)}:{i}")
    assert len(hits) == 1 and hits[0].startswith("cli.py"), hits


def test_no_tool_module_can_reach_the_relaunch(monkeypatch):
    for path in (SRC / "tools").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "relaunch_command" not in text and "cmd_relaunch" not in text, path


def test_relaunch_refuses_when_altium_is_not_running(monkeypatch, tmp_path, capsys):
    from altium_agent import cli
    from altium_agent.bridge import process_manager as pm

    project = tmp_path / "AltiumAgent.PrjScr"
    project.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(pm.AltiumProcessManager, "select_altium_process",
                        lambda self: pm.AltiumSelection(None, [], "none"))
    launched = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append(a))
    assert cli.cmd_relaunch(prjscr=str(project)) == 1
    assert launched == []
    assert "not running" in capsys.readouterr().err.lower()


def test_relaunch_refuses_when_two_altiums_are_running(monkeypatch, tmp_path):
    from altium_agent import cli
    from altium_agent.bridge import process_manager as pm

    project = tmp_path / "AltiumAgent.PrjScr"
    project.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "platform", "win32")
    two = [pm.AltiumProcessInfo(1, "X2.exe", "C:\\X2.exe"),
           pm.AltiumProcessInfo(2, "X2.exe", "C:\\X2.exe")]
    monkeypatch.setattr(pm.AltiumProcessManager, "select_altium_process",
                        lambda self: pm.AltiumSelection(None, two, "ambiguous",
                                                        "2 Altium processes"))
    launched = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append(a))
    assert cli.cmd_relaunch(prjscr=str(project)) == 1
    assert launched == []
