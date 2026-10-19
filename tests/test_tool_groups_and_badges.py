# SPDX-License-Identifier: Apache-2.0
"""Every tool sits in a named group and carries two honest badges.

Groups (docs/TOOL_NAMING.md): app_, proj_, sch_, pcb_, lib_, obj_, route_,
place_, ref_, analyze_, design_, audit_, plus tool_ for the meta-tools
that describe the surface itself.

Maturity: offline / simulator / live_verified / live_unverified.
live_verified is never inferred: it comes only from a record in
tools/live_verified.json, and every record must name a real tool, the
Altium build, the date, the deployed script stamp and an evidence file.

Interaction: readonly / silent / modal / partial.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from altium_agent.tools import register_backend
from altium_agent.tools.metadata import (
    INTERACTIONS,
    LIVE_VERIFIED,
    MATURITIES,
    TOOL_PREFIXES,
    live_verified_records,
    tool_metadata,
)
from altium_agent.tools.registry import ToolRegistry

LEDGER = (Path(__file__).resolve().parents[1] / "src" / "altium_agent"
          / "tools" / "live_verified.json")
SPEC_PREFIXES = ("app_", "proj_", "sch_", "pcb_", "lib_", "obj_", "route_",
                 "place_", "ref_", "analyze_", "design_", "audit_", "tool_")


@pytest.fixture(scope="module")
def names():
    reg = ToolRegistry()
    register_backend(reg, "altium", "full")
    return sorted(reg.names)


def test_the_prefix_table_is_the_documented_one():
    assert set(TOOL_PREFIXES) == set(SPEC_PREFIXES)


def test_every_tool_is_in_a_group(names):
    loose = [n for n in names if not n.startswith(SPEC_PREFIXES)]
    assert not loose, f"tools outside every group: {loose}"


def test_every_tool_has_a_category_and_both_badges(names):
    for n in names:
        md = tool_metadata(n)
        assert md["category"] != "other", n
        assert md["maturity"] in MATURITIES, (n, md)
        assert md["interaction"] in INTERACTIONS, (n, md)


def test_live_verified_comes_only_from_the_ledger(names):
    claimed = {n for n in names if tool_metadata(n)["maturity"] == LIVE_VERIFIED}
    assert claimed == set(live_verified_records()) & set(names)


def test_every_ledger_record_is_complete_and_names_a_real_tool(names):
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    for record in data["records"]:
        assert record.get("tool") in names, record
        for key in ("altium_version", "date", "script_stamp", "evidence"):
            assert str(record.get(key, "")).strip(), (record, key)


def test_offline_tools_are_never_live_badged(names):
    """An offline tool needs no Altium, so a live record would mislead."""
    for tool in live_verified_records():
        if tool in names:
            assert tool_metadata(tool)["maturity"] == LIVE_VERIFIED
