# SPDX-License-Identifier: Apache-2.0
"""Altium Agent is its own program: nothing it runs may look for, open or
talk to eda-agent's script project, folders or pointer file. Mentions in
comments that credit where code came from are fine; these checks look only
at code that runs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALTIUM = ROOT / "scripts" / "altium"

FORBIDDEN = re.compile(r"altium_api\.prjscr|programdata\\eda|\\eda agent\\|\.eda-agent", re.I)


def _pascal_code(text: str) -> str:
    text = re.sub(r"\{[^}]*\}", "", text)
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def test_altium_scripts_never_reference_eda_agent():
    hits = []
    for p in sorted(ALTIUM.glob("*.pas")) + sorted(ALTIUM.glob("*.PrjScr")) + sorted(ALTIUM.glob("*.dfm")):
        if p.name == "AltiumAgent.pas":  # generated bundle of the units below
            continue
        for n, line in enumerate(_pascal_code(p.read_text(encoding="utf-8", errors="replace")).splitlines(), 1):
            if FORBIDDEN.search(line):
                hits.append(f"{p.name}:{n}: {line.strip()}")
    assert not hits, hits


def test_python_never_references_eda_agent_paths():
    hits = []
    for p in (ROOT / "src" / "altium_agent").rglob("*.py"):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if FORBIDDEN.search(code):
                hits.append(f"{p.relative_to(ROOT)}:{n}")
    assert not hits, hits


def test_ping_reports_our_own_script_project():
    assert "ALTIUMAGENT.PRJSCR" in (ALTIUM / "Application.pas").read_text(encoding="utf-8")
