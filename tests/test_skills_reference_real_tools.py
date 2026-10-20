# SPDX-License-Identifier: Apache-2.0
"""The skills, the acceptance runbook and the gap list name only real tools.

A skill that sends the model to a tool that does not exist costs a whole
run; so does a runbook step. Every `name_like_this` in backticks that looks
like a tool (a known group prefix) must be registered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FILES = sorted((REPO / "skills").rglob("SKILL.md")) + [REPO / "docs" / "ACCEPTANCE.md", REPO / "docs" / "GAPS.md"]
GROUPS = ("app_", "proj_", "sch_", "pcb_", "lib_", "obj_", "route_", "place_", "ref_", "analyze_", "design_",
          "audit_", "tool_")


@pytest.fixture(scope="module")
def registered() -> set[str]:
    from altium_agent.tools import register_backend
    from altium_agent.tools.registry import ToolRegistry

    r = ToolRegistry()
    register_backend(r)
    return set(r.names)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_named_tools_exist(path, registered):
    text = path.read_text(encoding="utf-8")
    from altium_agent.design.session import STAGES

    names = {m for m in re.findall(r"`([a-z]+_[a-z0-9_]+)", text) if m.startswith(GROUPS) and m not in STAGES}
    # analyze_si_* in prose is a family, not one tool
    missing = sorted(n for n in names if n not in registered and not n.endswith("_"))
    assert not missing, f"{path.name} names tools that are not registered: {missing}"


def test_three_skills_with_frontmatter():
    names = sorted(p.parent.name for p in (REPO / "skills").rglob("SKILL.md"))
    assert names == ["autodesign", "relayout", "review"]
    for p in (REPO / "skills").rglob("SKILL.md"):
        head = p.read_text(encoding="utf-8").split("---")[1]
        assert f"name: {p.parent.name}" in head and "description:" in head
