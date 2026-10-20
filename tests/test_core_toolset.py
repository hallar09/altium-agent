# SPDX-License-Identifier: Apache-2.0
"""The "core" toolset: a fraction of the schemas, nothing out of reach.

Every advertised schema costs context on every turn in clients that load
them all; the full surface is about 150k tokens. Core advertises what a
design run uses and keeps the rest one tool_invoke away.
"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from altium_agent.tools import TOOLSETS, register_backend
from altium_agent.tools.registry import CORE_TOOLS


def _names(toolset: str) -> dict:
    m = FastMCP("t")
    register_backend(m, toolset=toolset)
    return {t.name: t for t in asyncio.run(m.list_tools())}


def test_core_is_a_toolset():
    assert "core" in TOOLSETS


def test_every_core_tool_exists_in_the_full_surface():
    full = _names("full")
    assert [t for t in CORE_TOOLS if t not in full] == []


def test_core_advertises_exactly_the_core_list_and_is_much_smaller():
    full, core = _names("full"), _names("core")
    assert set(core) == set(CORE_TOOLS)
    size = lambda d: sum(len(json.dumps(t.model_dump(exclude_none=True))) for t in d.values())  # noqa: E731
    assert size(core) < size(full) / 3


def test_skills_tools_are_all_in_core():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "skills"
    full = _names("full")
    named = set()
    for p in root.glob("*/SKILL.md"):
        named |= {n for n in re.findall(r"`([a-z_]+)", p.read_text(encoding="utf-8")) if n in full}
    assert sorted(named - set(CORE_TOOLS)) == []


def test_a_tool_outside_core_is_still_reachable():
    m = FastMCP("t")
    register_backend(m, toolset="core")
    out = asyncio.run(m.call_tool("tool_catalog", {"query": "sch_place_wires"}))
    assert "sch_place_wires" in json.dumps([getattr(c, "text", str(c)) for c in (out[0] if isinstance(out, tuple) else out)])
