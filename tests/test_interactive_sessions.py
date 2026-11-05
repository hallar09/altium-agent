# SPDX-License-Identifier: Apache-2.0
"""AI-driven placement and routing sessions (altium_agent.interactive).

The agent decides; the session carries each action out on the saved board,
checks it and keeps the state in a file. These hold the pieces to that:
the saved board reaches the in-house router and its exact DRC intact, every
move and route is checked and can be undone, and the result lands in Altium
the way an engine's does (a plan for place_apply, an SES for route_import_ses).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from altium_agent.interactive.board_model import layer_token, layout_board, legacy_id
from altium_agent.layout.drc import run_drc
from altium_agent.offline.files.pcbdoc import NONE16, Pad, PcbComponent, PcbDocument, StackLayer, Track
from altium_agent.units import MILS_PER_MM

_PUBLIC = Path(os.environ.get("PUBLIC") or Path(os.environ.get("SystemDrive", "C:") + os.sep) / "Users" / "Public")
WIFI_DIR = _PUBLIC / "Documents" / "Altium" / "AD26" / "Examples" / "Mini PC" / "Mini PC - WiFi"
wifi = pytest.mark.skipif(not (WIFI_DIR / "WiFi.PcbDoc").exists(), reason="Altium's WiFi example is not installed")


def _board(routed: bool) -> PcbDocument:
    doc = PcbDocument(Path("i.PcbDoc"), {}, (0.0, 0.0),
                      [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 20, "y": 10}, {"x": 0, "y": 10}],
                      [StackLayer(0, "Top", 0x01000001, "copper"), StackLayer(1, "Bottom", 0x0100FFFF, "copper")],
                      [PcbComponent(0, "R1", "0603", "", 5, 5, 0, "top", False, 1, "", "", ""),
                       PcbComponent(1, "R2", "0603", "", 15, 5, 0, "top", False, 1, "", "", "")],
                      ["A", "B"], [],
                      [{"name": "Clearance", "kind": "Clearance", "enabled": True, "priority": 1, "scope1": "All",
                        "scope2": "All", "values": {"gap_mm": 0.2}},
                       {"name": "Width", "kind": "Width", "enabled": True, "priority": 1, "scope1": "All",
                        "scope2": "All", "values": {"minlimit_mm": 0.15, "maxlimit_mm": 1.0, "preferedwidth_mm": 0.25}}],
                      [], [])
    doc.pads = [Pad("1", 1, 0, 0, 4.5, 5, 0.6, 0.8, 0, False, 2, 0), Pad("2", 1, 1, 0, 5.5, 5, 0.6, 0.8, 0, False, 2, 0),
                Pad("1", 1, 0, 1, 14.5, 5, 0.6, 0.8, 0, False, 2, 0), Pad("2", 1, 1, 1, 15.5, 5, 0.6, 0.8, 0, False, 2, 0)]
    if routed:
        doc.tracks = [Track(1, 0, NONE16, 4.5, 5, 4.5, 8, 0.25), Track(1, 0, NONE16, 4.5, 8, 14.5, 8, 0.25),
                      Track(1, 0, NONE16, 14.5, 8, 14.5, 5, 0.25),
                      # B runs below, clear of A's pads
                      Track(1, 1, NONE16, 5.5, 5, 5.5, 2, 0.25), Track(1, 1, NONE16, 5.5, 2, 15.5, 2, 0.25),
                      Track(1, 1, NONE16, 15.5, 2, 15.5, 5, 0.25)]
    return doc


def test_layer_names_round_trip():
    for lid in (1, 2, 5, 31, 32, 39, 54):
        assert legacy_id(layer_token(lid)) == lid


def test_a_saved_board_reaches_the_router_model_in_mils_with_its_rules():
    lb = layout_board(_board(routed=True))
    assert [l.name for l in lb.layers] == ["TopLayer", "BottomLayer"]
    assert lb.pads[0].comp == "R1" and lb.pads[0].copper[0].layer == "TopLayer"
    assert lb.pads[0].x == pytest.approx(4.5 * MILS_PER_MM)
    gaps = [r.values["gap"] for r in lb.rules if r.kind == "clearance"]
    assert gaps == [pytest.approx(0.2 * MILS_PER_MM)]
    assert any(r.descriptor.startswith("Width Constraint") for r in lb.rules)


def test_the_exact_drc_reads_a_converted_board_right():
    done = run_drc(layout_board(_board(routed=True)))
    assert done.unrouted == {} and done.violations == []
    bare = run_drc(layout_board(_board(routed=False)))
    assert set(bare.unrouted) == {"A", "B"}


@pytest.fixture
def copy(tmp_path, monkeypatch):
    """A copy of the WiFi example, outputs in tmp: the sample is never touched."""
    folder = tmp_path / "wifi"
    shutil.copytree(WIFI_DIR, folder, ignore=shutil.ignore_patterns("History", "Project Outputs*", "*.zip"))
    monkeypatch.setenv("ALTIUM_AGENT_OUTPUT_DIR", str(tmp_path / "out"))
    return folder


@wifi
def test_a_placement_session_moves_checks_undoes_and_exports(copy):
    from altium_agent.interactive import placing as P
    from altium_agent.placement.service import place_check

    s = P.start(project=str(copy / "WiFi_miniPCIe.PrjPcb"), name="t")
    assert len(s["parts"]) == 28 and s["parts"][0]["ref"] == "U1"        # largest first
    cap = P.part(s["session"], "C10")
    assert cap["decouples"]["net"] == "3V3"
    m = P.move(s["session"], [{"ref": "C10", "dx_mm": 40}])
    assert any(f["code"] == "outside_outline" for f in m["findings_on_moved"])
    assert P.undo(s["session"])["undone"] == 1
    assert P.check(s["session"])["counts"]["error"] == 1                  # the board's own, inherited
    e = P.export(s["session"])
    assert e["check"]["ok"] and place_check(e["plan_path"])["ok"]


@wifi
def test_a_locked_part_moves_only_when_forced(copy):
    from altium_agent.interactive import placing as P
    from altium_agent.offline.errors import InvalidArgument

    s = P.start(pcb=str(copy / "WiFi.PcbDoc"), name="t")
    with pytest.raises(InvalidArgument, match="locked"):
        P.move(s["session"], [{"ref": "P1", "dx_mm": 1}])
    assert P.move(s["session"], [{"ref": "P1", "dx_mm": 1}], force=True)["moved"][0]["ref"] == "P1"


@wifi
def test_a_routing_session_routes_draws_rips_up_and_lands_as_an_ses(copy, tmp_path, monkeypatch):
    from altium_agent.bench import mask
    from altium_agent.design.jobs import get_job_store
    from altium_agent.interactive import routing as R
    from altium_agent.routing.service import ses_rows_for

    pcb = copy / "WiFi.PcbDoc"
    monkeypatch.setenv(mask.ENV, str(mask.write(tmp_path / "mask.json", {str(pcb): {"strip_routing": True}})))
    s = R.start(pcb=str(pcb), name="t")
    assert s["open_connections"] > 30 and set(s["planes_and_pours"]) == {"GND", "3V3"}
    f = R.fanout(s["session"])
    assert f["vias_placed"] > 5
    r = R.route(s["session"], ["PA12_USB_D_P", "PA12_USB_D_N"], width_mm=0.25)
    assert r["done"] == ["PA12_USB_D_N", "PA12_USB_D_P"] and r["violations_on_these_nets"] == []
    d = R.draw(s["session"], "NetC19_1", [{"x_mm": 1, "y_mm": 1, "layer": "TopLayer"},
                                          {"x_mm": 2, "y_mm": 1, "layer": "TopLayer"},
                                          {"x_mm": 2, "y_mm": 1, "layer": "BottomLayer"},
                                          {"x_mm": 2, "y_mm": 2, "layer": "BottomLayer"}])
    assert d["added"]["tracks"] == 2 and d["added"]["vias"] == 1
    assert R.undo(s["session"])["undone"] == 1
    gone = R.ripup(s["session"], ["PA12_USB_D_P"])
    assert gone["removed"]["tracks"] > 0
    e = R.export(s["session"])
    rows = ses_rows_for(get_job_store().get(e["job_id"]).result)
    assert rows.summary()["tracks"] == e["tracks"] and rows.summary()["vias"] == e["vias"]


def test_a_session_file_of_the_wrong_kind_is_refused(tmp_path, monkeypatch):
    from altium_agent.interactive import session as S
    from altium_agent.offline.errors import InvalidArgument

    monkeypatch.setenv("ALTIUM_AGENT_OUTPUT_DIR", str(tmp_path))
    p = S.new("place", tmp_path / "b.PcbDoc", "x", {"positions": {}})
    with pytest.raises(InvalidArgument, match="not a route session"):
        S.load(p, "route")
    with pytest.raises(InvalidArgument, match="already exists"):
        S.new("place", tmp_path / "b.PcbDoc", "x", {})
