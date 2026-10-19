# SPDX-License-Identifier: Apache-2.0
"""Lengths at the API are millimetres with the unit in the name.

The forked eda-agent surface speaks mils and often names no unit, so its
non-conforming tools are listed in core/LEGACY_UNIT_TOOLS.json while they
are migrated. This file keeps that list honest in both directions:

- a tool NOT on the list must conform (a new tool cannot add mils);
- a tool ON the list must still need to be there (a migrated tool is
  removed, so the list cannot overstate the debt).
"""

from __future__ import annotations

import pytest

from altium_agent.core.units_policy import (
    classify,
    legacy_tools,
    mils_to_mm,
    mm_to_mils,
    reply_to_mm,
    unit_of,
)
from altium_agent.tools import register_backend
from altium_agent.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def surface():
    reg = ToolRegistry()
    register_backend(reg, "altium", "full")
    return {name: classify(name, reg.get(name).fn) for name in reg.names}


def test_no_new_tool_takes_mils_or_an_unnamed_length(surface):
    legacy = legacy_tools()
    offenders = sorted(n for n, u in surface.items()
                       if not u.conforms and n not in legacy)
    assert not offenders, (
        "these tools take a length in mils or without a unit in its name, "
        "and are not on the legacy list: "
        + "; ".join(f"{n} mil={surface[n].mil} bare={surface[n].bare}"
                    for n in offenders)
        + ". Take millimetres named *_mm and convert inside the tool.")


def test_the_legacy_list_only_names_tools_that_still_need_it(surface):
    legacy = legacy_tools()
    gone = sorted(n for n in legacy if n not in surface)
    fixed = sorted(n for n in legacy if n in surface and surface[n].conforms)
    assert not gone, f"legacy list names tools that no longer exist: {gone}"
    assert not fixed, (
        f"these tools now conform; remove them from "
        f"core/LEGACY_UNIT_TOOLS.json: {fixed}")


def test_altium_agent_groups_are_millimetre_only(surface):
    """The groups Altium Agent adds never join the legacy list."""
    new_groups = ("place_", "ref_", "analyze_")
    legacy = legacy_tools()
    assert not [n for n in legacy if n.startswith(new_groups)]


@pytest.mark.parametrize("name,kind", [
    ("x_mm", "mm"), ("width_mm", "mm"), ("width_mils", "mil"),
    ("gap_mil", "mil"), ("x", "bare"), ("hole_size", "bare"),
    ("net", "none"), ("copper_oz", "none"), ("width_px", "none"),
])
def test_unit_of(name, kind):
    assert unit_of(name) == kind


def test_conversions_round_trip():
    assert mm_to_mils(0.254) == pytest.approx(10.0)
    assert mils_to_mm(10) == pytest.approx(0.254)
    assert mils_to_mm(mm_to_mils(0.4)) == pytest.approx(0.4)


def test_replies_gain_mm_twins_without_losing_mils():
    out = reply_to_mm({"width_mils": 10, "nested": [{"gap_mil": 5}],
                       "size_mm": 1.0, "size_mils": 999})
    assert out["width_mils"] == 10 and out["width_mm"] == pytest.approx(0.254)
    assert out["nested"][0]["gap_mm"] == pytest.approx(0.127)
    # an existing mm key is never overwritten by a conversion
    assert out["size_mm"] == 1.0
