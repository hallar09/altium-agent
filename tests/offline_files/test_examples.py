# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tests/test_examples.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Regression against Altium's own AD26 example projects (skipped when they are not installed)."""

from __future__ import annotations

from altium_agent.analysis.consistency import compare
from altium_agent.analysis.erc import run_erc
from altium_agent.analysis.model import load_model
from altium_agent.offline.files.libraries import read_schlib
from altium_agent.offline.files.pcbdoc import read_pcbdoc

from .conftest import SPIRIT, requires_examples


@requires_examples
def test_spirit_level_netlist_matches_board_exactly():
    model = load_model(SPIRIT)
    nl = model.netlist()
    pcb = model.pcb()
    sch_sets = {frozenset(f"{m.designator}-{m.pin}" for m in n.members) for n in nl.nets}
    pcb_sets = {frozenset(v) for v in pcb.pad_netlist().values()}
    assert nl.mode == "hierarchical"
    assert len(pcb_sets) == 126
    assert sch_sets == pcb_sets


@requires_examples
def test_spirit_level_board_facts():
    pcb = read_pcbdoc(SPIRIT.with_suffix(".PcbDoc"))
    s = pcb.summary()
    assert s["copper_layer_count"] == 2
    assert s["board_outline"]["width_mm"] > 179 and s["board_outline"]["height_mm"] > 50
    r10 = next(c for c in pcb.components if c.designator == "R10")
    assert (r10.footprint, r10.comment, r10.side) == ("CR2012-0805", "680R", "top")


@requires_examples
def test_spirit_level_checks_run():
    model = load_model(SPIRIT)
    erc = run_erc(model)
    assert erc["summary"]["errors"] == 0
    sync = compare(model, model.pcb())
    assert sync["nets_split_or_unassigned_in_pcb"] == []
    assert sync["footprint_mismatches"] == []


@requires_examples
def test_schlib_reader():
    syms = read_schlib(SPIRIT.parent / "Libraries" / "SpiritLevel_2E.SchLib")
    fpga = next(s for s in syms if s.lib_ref == "XC2S300E-6PQ208C")
    assert fpga.pin_count == 208 and fpga.footprints == ["PQ208"]
