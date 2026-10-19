# SPDX-License-Identifier: Apache-2.0
"""A scripted rule enable/disable is read back, and a write that did not
stick is a failure, not a success.

AD26 accepts ``Rule.Enabled := False`` (and DRCEnabled) from a script and
can keep the old value; BoardKit lost a DRC pass to it. The handler now
reads every changed flag back after PostProcess, the simulator models both
behaviours, and the tool turns a mismatch into ``ok: False`` naming the
dependable alternative.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from altium_agent.tools import pcb as pcb_module

PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _tool(monkeypatch, bridge, name="pcb_set_rules_enabled"):
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    pcb_module.register_pcb_tools(_Mcp())
    return captured[name]


class _AsyncBridge:
    """Adapts the synchronous e2e bridge to the async call the tool makes."""

    def __init__(self, bridge):
        self.bridge = bridge

    async def send_command_async(self, command, params=None, timeout=None):
        return self.bridge.send_command(command, params or {}, timeout=5.0)


def test_a_flag_that_sticks_is_verified(monkeypatch, e2e_bridge, altium_sim):
    tool = _tool(monkeypatch, _AsyncBridge(e2e_bridge))
    out = asyncio.run(tool(names=["Silk*"], enabled=False))
    assert out["ok"] is True
    assert out["updated"] == 2 and out["verified"] == 2
    assert out["mismatched"] == []
    rules = {r["name"]: r for r in altium_sim.board.rules}
    assert rules["SilkToSilkClearance"]["drc_enabled"] is False


def test_a_flag_that_does_not_stick_is_a_failure(monkeypatch, e2e_bridge,
                                                 altium_sim):
    altium_sim.rule_enable_sticks = False      # how AD26 behaves
    tool = _tool(monkeypatch, _AsyncBridge(e2e_bridge))
    out = asyncio.run(tool(names=["SilkToSilkClearance"], enabled=False))
    assert out["ok"] is False
    assert out["mismatched"] == ["SilkToSilkClearance"]
    assert "pcb_delete_design_rule" in out["reason"]
    assert "pcb_create_design_rule" in out["reason"]


def test_set_rule_properties_reports_an_enable_that_did_not_take(monkeypatch):
    class _Bridge:
        async def send_command_async(self, command, params=None, timeout=None):
            return {"name": "X", "rule_kind": 0, "properties_updated": 0,
                    "enabled_written": False,
                    "enabled_note": "Enabled was written and did NOT read back."}

    tool = _tool(monkeypatch, _Bridge(), "pcb_set_rule_properties")
    out = asyncio.run(tool(name="X", enabled=False))
    assert out["ok"] is False and "did NOT read back" in out["reason"]


def test_the_handlers_read_the_flags_back():
    """The Pascal side, read: both writers call ReadBackRuleEnabled after
    PostProcess, and the helper is defined before its first caller."""
    text = PCB_PAS.read_text(encoding="utf-8")
    helper = text.index("Function ReadBackRuleEnabled(")
    for routine in ("Function PCB_SetRulesEnabled(", "Function PCB_SetRuleProperties("):
        start = text.index(routine)
        assert helper < start, f"{routine} precedes the helper it calls"
        end = text.index("\nEnd;", start)
        body = text[start:end]
        post = body.rindex("PCBServer.PostProcess")
        read = body.index("ReadBackRuleEnabled(")
        assert read > post, f"{routine} reads back before PostProcess"
    assert re.search(r"'mismatched'", text)
