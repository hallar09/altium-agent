# SPDX-License-Identifier: Apache-2.0
"""routing.fanout: power and ground pads reach their planes by short vias.

Freerouting, asked to fan power out, placed far too few vias and drew power
tracks across the board instead (Altium's WiFi example: the person used 86
vias, Freerouting 18 to 36). These hold the planner to what a person does:
a via beside the pad, clear of every other net, on the net's plane; nothing
for a pad its pour already joins; a stub into a pour the pad sits just
outside; nothing twice; and a pad it cannot reach reported, not forced.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from altium_agent.offline.files.pcbdoc import NONE16, Pad, PcbComponent, PcbDocument, StackLayer, Track, Via
from altium_agent.routing.fanout import plan_fanout, plan_stitching, plane_nets, with_result, write_ses
from altium_agent.routing.specctra import parse_ses

SQUARE = [[0, 0], [20, 0], [20, 20], [0, 20]]
RULES = dict(via_diameter_mm=0.6, via_hole_mm=0.3, stub_width_mm=0.25, clearance_mm=0.2)


def _board(gnd_pours=(("BOTTOM", SQUARE),)) -> PcbDocument:
    doc = PcbDocument(Path("f.PcbDoc"), {}, (0.0, 0.0), [{"x": x, "y": y} for x, y in SQUARE],
                      [StackLayer(0, "Top", 0x01000001, "copper"), StackLayer(1, "Bottom", 0x0100FFFF, "copper")],
                      [PcbComponent(0, "C1", "0603", "", 10, 10, 0, "top", False, 1, "", "", ""),
                       PcbComponent(1, "U1", "QFN", "", 12, 10, 0, "top", False, 1, "", "", "")],
                      ["GND", "SIG"], [], [], [], [])
    doc.pads = [Pad("1", 1, 0, 0, 10, 10, 1.0, 1.0, 0, False, 2, 0),        # GND, top: needs a via
                Pad("2", 1, 1, 1, 11.4, 10, 0.6, 1.0, 0, False, 2, 0)]       # SIG right beside it
    doc.polygons = [{"name": f"G{i}", "net": "GND", "layer": layer, "shelved": False, "outline_mm": outline}
                    for i, (layer, outline) in enumerate(gnd_pours)]
    return doc


def test_a_pad_off_its_pour_gets_a_via_beside_it_clear_of_other_nets():
    doc = _board()
    r = plan_fanout(doc, **RULES)
    assert (r.needed, r.placed, len(r.unresolved)) == (1, 1, 0)
    (v,) = r.vias
    assert v["net"] == "GND" and (v["diameter"], v["hole"]) == (0.6, 0.3)
    assert math.hypot(v["x"] - 10, v["y"] - 10) < 0.5 + 0.3 + 0.2 + 1e-6      # the nearest spot, not far off
    # clear of SIG's pad (x 11.1 .. 11.7, y 9.5 .. 10.5) by the clearance
    gap = max(11.1 - v["x"], v["x"] - 11.7, 0) if abs(v["y"] - 10) < 0.5 + 0.3 else 1
    assert gap >= 0.3 + 0.2 - 1e-6 or v["x"] < 11.1 - 0.5
    (t,) = r.tracks
    assert (t["layer"], t["net"], t["x1"], t["y1"]) == (1, "GND", 10, 10) and (t["x2"], t["y2"]) == (v["x"], v["y"])


def test_a_pad_its_pour_already_joins_gets_nothing():
    r = plan_fanout(_board(gnd_pours=(("TOP", SQUARE), ("BOTTOM", SQUARE))), **RULES)
    assert (r.on_pour, r.needed, r.vias) == (1, 0, [])


def test_a_pad_just_outside_its_pour_gets_a_stub_into_it_and_no_via():
    lower = [[0, 0], [20, 0], [20, 9.0], [0, 9.0]]                    # top pour ends 0.5 mm below the pad
    r = plan_fanout(_board(gnd_pours=(("TOP", lower),)), **RULES)
    assert (r.to_pour, r.vias) == (1, [])
    (t,) = r.tracks
    assert t["y2"] < 9.0 and t["layer"] == 1


def test_a_net_with_nowhere_to_go_is_reported_not_forced():
    r = plan_fanout(_board(gnd_pours=()), nets=["GND"], **RULES)
    assert r.vias == [] and r.unresolved[0]["reason"].startswith("the net has no plane")


def test_running_it_again_adds_nothing_twice():
    doc = _board()
    first = plan_fanout(doc, **RULES)
    again = plan_fanout(with_result(doc, first), **RULES)
    assert (again.already, again.needed, again.vias) == (1, 0, [])


def test_a_same_net_via_in_reach_is_shared():
    doc = _board()
    doc.vias = [Via(0, 9.0, 10.0, 0.6, 0.3, 1, 32)]
    r = plan_fanout(doc, **RULES)
    assert (r.reused, r.placed) == (1, 0)


def test_stitching_joins_pours_on_two_layers_and_keeps_clear():
    doc = _board(gnd_pours=(("TOP", SQUARE), ("BOTTOM", SQUARE)))
    st = plan_stitching(doc, spacing_mm=5.0, via_diameter_mm=0.6, via_hole_mm=0.3, clearance_mm=0.2)
    assert st.placed >= 9
    for v in st.vias:
        assert not (10.6 < v["x"] < 12.2 and 9.0 < v["y"] < 11.0)        # nowhere on SIG's pad


def test_a_token_pour_is_not_a_plane():
    doc = _board(gnd_pours=(("BOTTOM", [[1, 1], [2, 1], [2, 2], [1, 2]]),))   # 1 mm2 on a 400 mm2 board
    assert plane_nets(doc) == set()
    assert plane_nets(_board()) == {"GND"}


def test_the_fanout_reads_back_as_an_ses(tmp_path):
    r = plan_fanout(_board(), **RULES)
    ses = write_ses(r, tmp_path / "f.ses", {"Top": 1, "Bottom": 32})
    back = parse_ses(ses, 0.3)
    assert len(back.vias) == 1 and back.vias[0]["diameter"] == pytest.approx(0.6)
    assert back.vias[0]["x"] == pytest.approx(r.vias[0]["x"], abs=1e-4)
    (t,) = back.tracks
    assert t["layer"] == "Top" and t["width"] == pytest.approx(0.25)


def test_existing_routing_of_another_net_is_kept_clear_of():
    doc = _board()
    # a SIG track hugging the pad's left and lower sides
    doc.tracks = [Track(1, 1, NONE16, 9.0, 8.5, 9.0, 11.5, 0.25), Track(1, 1, NONE16, 9.0, 8.8, 12.0, 8.8, 0.25)]
    r = plan_fanout(doc, **RULES)
    for v in r.vias:
        for t in doc.tracks:
            # point-to-segment distance minus half widths >= clearance
            px, py = v["x"], v["y"]
            dx, dy = t.x2 - t.x1, t.y2 - t.y1
            u = max(0, min(1, ((px - t.x1) * dx + (py - t.y1) * dy) / (dx * dx + dy * dy)))
            d = math.hypot(px - (t.x1 + u * dx), py - (t.y1 + u * dy)) - 0.3 - t.width / 2
            assert d >= 0.2 - 1e-6
