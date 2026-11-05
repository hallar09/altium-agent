# SPDX-License-Identifier: Apache-2.0
"""A tool reached through tool_catalog / tool_invoke is the same tool.

Under the core and minimal toolsets most tools are captured by
ToolRegistry, not advertised. The registry used to guess each parameter's
JSON type from the annotation's text, so ``list[str]`` matched "str" and was
reported as a string (117 parameters were wrong), and it called the function
with the raw arguments, so a string passed where a list belonged went
straight through. These tests hold the registry to FastMCP's own schema and
validation, and every tool to annotations that agree with its badge.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from altium_agent.server import build_server_instructions
from altium_agent.tools import register_backend
from altium_agent.tools.metadata import (
    LOCAL_STATE_WRITERS, READONLY, interaction_of,
)
from altium_agent.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def full():
    m = FastMCP("full")
    register_backend(m, "altium", "full")
    return {t.name: t for t in asyncio.run(m.list_tools())}


@pytest.fixture(scope="module")
def registry():
    reg = ToolRegistry()
    register_backend(reg, "altium", "full")
    return reg


@pytest.fixture(scope="module")
def core():
    m = FastMCP("core")
    register_backend(m, "altium", "core")
    return m


def _payload(result):
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_registry_schemas_are_fastmcps(full, registry):
    assert set(registry.names) == set(full)
    differ = [n for n in registry.names
              if registry.get(n).inputSchema != full[n].inputSchema]
    assert differ == []


def test_a_list_parameter_is_reported_as_an_array(core):
    got = _payload(asyncio.run(core.call_tool("tool_catalog", {
        "query": "proj_get_component_info_many", "with_schema": True})))
    entry = next(t for t in got["tools"]
                 if t["name"] == "proj_get_component_info_many")
    assert entry["parameters"]["designators"]["type"] == "array"


def test_invoke_validates_before_the_tool_runs(core):
    got = _payload(asyncio.run(core.call_tool("tool_invoke", {
        "name": "proj_get_component_info_many",
        "arguments": {"designators": "U1"}})))
    assert "error" in got
    assert "designators" in got["error"]
    assert "valid list" in got["error"]
    assert "Error executing tool" not in got["error"]


def test_invoke_coerces_like_a_direct_call(core):
    """FastMCP turns "1.5" into 1.5 for a number parameter; so must the
    invoke route, or the same call behaves differently by toolset."""
    direct = FastMCP("d")
    register_backend(direct, "altium", "full")
    args = {"current_amps": "1.5", "copper_oz": "1"}
    via_invoke = _payload(asyncio.run(core.call_tool("tool_invoke", {
        "name": "pcb_calc_trace_width_for_current", "arguments": args})))
    called = _payload(asyncio.run(direct.call_tool(
        "pcb_calc_trace_width_for_current", args)))
    assert "error" not in via_invoke
    assert via_invoke["result"] == called


def test_every_tool_is_annotated(full):
    assert [n for n, t in full.items() if t.annotations is None] == []


def test_core_keeps_the_annotations(core, full):
    for t in asyncio.run(core.list_tools()):
        assert t.annotations == full[t.name].annotations, t.name


@pytest.mark.parametrize("name", sorted(LOCAL_STATE_WRITERS))
def test_a_local_state_writer_is_not_called_read_only(full, name):
    assert name in full, f"{name} is listed as a writer but is not a tool"
    assert full[name].annotations.readOnlyHint is False


def test_read_only_hint_follows_the_badge(full):
    wrong = [n for n, t in full.items()
             if t.annotations.readOnlyHint
             != (interaction_of(n) == READONLY
                 and n not in LOCAL_STATE_WRITERS)]
    assert wrong == []


def test_deleting_tools_are_destructive_and_adding_ones_are_not(full):
    assert full["pcb_delete_net"].annotations.destructiveHint is True
    assert full["obj_batch_delete"].annotations.destructiveHint is True
    assert full["pcb_place_via"].annotations.destructiveHint is False
    assert full["pcb_place_via"].annotations.readOnlyHint is False


def test_a_read_only_tool_is_never_destructive(full):
    assert [n for n, t in full.items()
            if t.annotations.readOnlyHint
            and t.annotations.destructiveHint] == []


def test_the_units_line_does_not_promise_one_unit(full):
    text = build_server_instructions()
    assert "mils throughout" not in text
    assert "_mm" in text and "mils" in text
    assert any(p.endswith("_mm") for t in full.values()
               for p in t.inputSchema.get("properties", {}))
