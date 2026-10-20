# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tests/test_netlist.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Connectivity rules on hand-built sheets (no files needed)."""

from __future__ import annotations

from pathlib import Path

from altium_agent.offline.files.sch_netlist import expand_bus, netlist_from_docs
from altium_agent.offline.files.schdoc import SchComponent, SchDocument, SchLabel, SchPin, SchSheetSymbol


def pin(num: str, x: float, y: float, kind: str = "passive", hidden_net: str = "") -> SchPin:
    return SchPin(num, f"P{num}", kind, bool(hidden_net), hidden_net, (x, y), (x, y), 1)


def comp(ref: str, *pins: SchPin) -> SchComponent:
    return SchComponent(0, ref, "RES", "", "", "10k", "0603", ["0603"], "", 1, 1, (0, 0), 0, False, "", {}, list(pins))


def sheet(name: str, components=(), wires=(), labels=(), symbols=(), junctions=()) -> SchDocument:
    return SchDocument(Path(name), {}, list(components), [list(w) for w in wires], [], list(junctions), list(labels),
                       list(symbols), [], {}, {})


def nets_by_pins(nl):
    return {frozenset(f"{m.designator}-{m.pin}" for m in n.members): n.name for n in nl.nets}


def test_wire_tee_and_label():
    s = sheet("a.SchDoc",
              components=[comp("R1", pin("1", 0, 0), pin("2", 100, 0)), comp("R2", pin("1", 300, 0)),
                          comp("R3", pin("1", 200, -100))],
              # R1-2 -> R2-1 straight; R3-1 joins through a wire ending on the middle of the first (T-junction).
              wires=[[(100, 0), (300, 0)], [(200, -100), (200, 0)]],
              labels=[SchLabel("net_label", "SIG", [(150, 0)])])
    nl = netlist_from_docs([s], "2")
    assert nets_by_pins(nl)[frozenset({"R1-2", "R2-1", "R3-1"})] == "SIG"
    assert [f"{m.designator}-{m.pin}" for m in nl.unconnected_pins] == ["R1-1"]


def test_crossing_wires_without_junction_do_not_connect():
    s = sheet("a.SchDoc",
              components=[comp("R1", pin("1", 0, 0)), comp("R2", pin("1", 200, 0)),
                          comp("R3", pin("1", 100, -100)), comp("R4", pin("1", 100, 100))],
              wires=[[(0, 0), (200, 0)], [(100, -100), (100, 100)]])
    sets = set(nets_by_pins(netlist_from_docs([s], "2")))
    assert frozenset({"R1-1", "R2-1"}) in sets and frozenset({"R3-1", "R4-1"}) in sets


def test_power_ports_global_labels_local_in_flat_mode():
    a = sheet("a.SchDoc", components=[comp("C1", pin("1", 0, 0), pin("2", 0, 100))],
              labels=[SchLabel("power_port", "GND", [(0, 0)]), SchLabel("net_label", "X", [(0, 100)])])
    b = sheet("b.SchDoc", components=[comp("C2", pin("1", 0, 0), pin("2", 0, 100))],
              labels=[SchLabel("power_port", "GND", [(0, 0)]), SchLabel("net_label", "X", [(0, 100)])])
    nets = nets_by_pins(netlist_from_docs([a, b], "1"))  # flat: labels stay on their sheet
    assert nets[frozenset({"C1-1", "C2-1"})] == "GND"
    assert frozenset({"C1-2", "C2-2"}) not in nets
    nets_global = nets_by_pins(netlist_from_docs([a, b], "2"))  # global: labels join too
    assert frozenset({"C1-2", "C2-2"}) in nets_global


def test_hidden_power_pins_join_named_net():
    a = sheet("a.SchDoc", components=[comp("U1", pin("7", 0, 0, "power", hidden_net="VCC")),
                                      comp("C1", pin("1", 50, 0))],
              labels=[SchLabel("power_port", "VCC", [(50, 0)])])
    assert nets_by_pins(netlist_from_docs([a], "2"))[frozenset({"U1-7", "C1-1"})] == "VCC"


def test_hierarchical_entry_to_port_and_bus_expansion():
    top = sheet("top.SchDoc", symbols=[
        SchSheetSymbol(0, "A", "a.SchDoc", (0, 0), (100, 100), [SchLabel("sheet_entry", "EN", [(0, -10)]),
                                                                 SchLabel("sheet_entry", "D[1..0]", [(0, -20)])]),
        SchSheetSymbol(1, "B", "b.SchDoc", (500, 0), (100, 100), [SchLabel("sheet_entry", "EN", [(500, -10)]),
                                                                   SchLabel("sheet_entry", "D[1..0]", [(500, -20)])]),
    ], wires=[[(0, -10), (500, -10)]])
    a = sheet("a.SchDoc", components=[comp("U1", pin("1", 0, 0), pin("2", 0, 50), pin("3", 0, 60))],
              labels=[SchLabel("port", "EN", [(0, 0), (40, 0)]), SchLabel("port", "D[1..0]", [(100, 100), (140, 100)]),
                      SchLabel("net_label", "D0", [(0, 50)]), SchLabel("net_label", "D1", [(0, 60)])])
    b = sheet("b.SchDoc", components=[comp("U2", pin("1", 0, 0), pin("2", 0, 50), pin("3", 0, 60))],
              labels=[SchLabel("port", "EN", [(0, 0), (40, 0)]), SchLabel("port", "D[1..0]", [(100, 100), (140, 100)]),
                      SchLabel("net_label", "D0", [(0, 50)]), SchLabel("net_label", "D1", [(0, 60)])])
    nl = netlist_from_docs([top, a, b], "3")
    nets = nets_by_pins(nl)
    assert frozenset({"U1-1", "U2-1"}) in nets  # EN through entries/ports and the top-level wire
    assert nets[frozenset({"U1-2", "U2-2"})] == "D0"  # bus member carried by name
    assert nets[frozenset({"U1-3", "U2-3"})] == "D1"
    assert not nl.floating_labels  # bus names never reported as floating nets


def test_expand_bus():
    assert expand_bus("D[3..0]") == ["D3", "D2", "D1", "D0"]
    assert expand_bus("ADDR[0..2]") == ["ADDR0", "ADDR1", "ADDR2"]
    assert expand_bus("CLK") == []
