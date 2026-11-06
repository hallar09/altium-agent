# SPDX-License-Identifier: Apache-2.0
"""Ground and supply pours, planned before routing.

The router leaves a net with a pour or plane to it (a via from each pad,
stitching between pours), but nothing made the pours: a board without them
had ground and every supply routed as long tracks. On the benchmark, with
the people's pours taken off, routing WiFi's power as tracks took 410 mm of
copper and 51 vias against the people's 215 mm and 33; the planned pours
gave exactly the people's figures (the same four pours: ground on Top,
int2_gnd and Bottom, 3V3 on int1_power).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from altium_agent.offline.files.pcbdoc import read_pcbdoc
from altium_agent.routing.fanout import plane_nets
from altium_agent.routing.pours import _inset, _named_supply, apply_to, plan_pours
from altium_agent.tools import pcb as pcb_mod
from altium_agent.tools.registry import ToolRegistry

from tests.pascal_source import load
from tests.test_interactive_sessions import WIFI_DIR, copy, wifi  # noqa: F401  (fixture)
from tests.test_rules_stack_and_vias_for_agents import _Recorder

_PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _bare(doc):
    return replace(doc, polygons=[], board={k: v for k, v in doc.board.items()
                                            if not (k.startswith("PLANE") and k.endswith("NETNAME"))})


def test_a_layer_named_for_a_supply_gets_it():
    sup = ["VDD", "VPP", "3V3", "VTT"]
    assert _named_supply("VDD4", sup) == "VDD"
    assert _named_supply("int1_3V3", sup) == "3V3"
    assert _named_supply("SPLIT2", sup) is None


def test_inset_pulls_a_square_in_on_every_side():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert _inset(sq, 1.0) == [(1, 1), (9, 1), (9, 9), (1, 9)]
    assert _inset(list(reversed(sq)), 1.0) == [(1, 9), (9, 9), (9, 1), (1, 1)]


@wifi
def test_wifi_without_its_pours_gets_the_ones_its_designer_drew():
    doc = _bare(read_pcbdoc(WIFI_DIR / "WiFi.PcbDoc"))
    assert plane_nets(doc) == set()
    plan = plan_pours(doc)
    got = {(p["net"], p["layer"], p["kind"]) for p in plan["pours"]}
    assert got == {("GND", "TOP", "full"), ("3V3", "MID1", "full"), ("GND", "MID2", "full"),
                   ("GND", "BOTTOM", "full")}
    assert plane_nets(apply_to(doc, plan)) == {"GND", "3V3"}
    # the board as drawn already has them: nothing to add
    assert plan_pours(read_pcbdoc(WIFI_DIR / "WiFi.PcbDoc"))["pours"] == []


@wifi
def test_a_session_routes_with_planned_pours(copy, tmp_path, monkeypatch):  # noqa: F811
    from altium_agent.bench import mask
    from altium_agent.interactive import routing as R

    pcb = copy / "WiFi.PcbDoc"
    monkeypatch.setenv(mask.ENV, str(mask.write(tmp_path / "mask.json", {str(pcb): {"strip_routing": True}})))
    s = R.start(pcb=str(pcb), name="t")["session"]
    real = R.read_pcbdoc
    monkeypatch.setattr(R, "read_pcbdoc", lambda p, **k: _bare(real(p, **k)))   # the board with no pours
    assert R.check(s)["open_connections"] > 0
    out = R.pours(s)
    assert set(out["carried_by_copper_area"]) == {"GND", "3V3"} and len(out["pours"]) == 4
    f = R.fanout(s)
    assert f["vias_placed"] > 5                       # their pads now drop to the pours
    assert R.S.load(s, "route")[1]["pours"]["pours"]
    e = R.export(s)
    assert "pcb_apply_pours" in e["next"]
    assert R.pours(s, clear=True)["cleared"] is True
    assert "pours" not in R.S.load(s, "route")[1]


def _tools(monkeypatch, reply=None):
    rec = _Recorder(reply)
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: rec)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    fns = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    return fns, rec


@wifi
def test_place_polygon_sends_absolute_mm(monkeypatch):
    fns, rec = _tools(monkeypatch, {"placed": True})
    doc = read_pcbdoc(WIFI_DIR / "WiFi.PcbDoc", primitives=False)
    out = asyncio.run(fns["pcb_place_polygon"]([[0, 0], [10, 0], [10, 5]], "MidLayer1", "3V3",
                                               str(WIFI_DIR / "WiFi.PcbDoc")))
    assert out == {"placed": True}
    cmd, params = rec.sent[-1]
    assert cmd == "pcb.place_polygon" and params["layer"] == "MidLayer1" and params["net"] == "3V3"
    first = [float(v) for v in params["points_mm"].split(";")[0].split(",")]
    assert first == pytest.approx([doc.origin_mil[0] * 0.0254, doc.origin_mil[1] * 0.0254], abs=1e-4)
    assert asyncio.run(fns["pcb_place_polygon"]([[0, 0], [1, 1]], "TopLayer", "GND", "x"))["ok"] is False


@wifi
def test_apply_pours_makes_islands_first_then_full_pours(monkeypatch, tmp_path):
    fns, rec = _tools(monkeypatch, {"placed": True, "success": True})
    plan = {"planes": [], "pours": [
        {"net": "GND", "layer": "TOP", "kind": "full", "pour_index": 0, "outline_mm": [[0, 0], [5, 0], [5, 5]]},
        {"net": "3V3", "layer": "MID1", "kind": "island", "pour_index": 1, "outline_mm": [[1, 1], [2, 1], [2, 2]]}]}
    dry = asyncio.run(fns["pcb_apply_pours"](str(WIFI_DIR / "WiFi.PcbDoc"), plan=plan))
    assert dry["dry_run"] is True and [w["kind"] for w in dry["would_make"]] == ["island", "full"]
    assert rec.sent == []
    real = asyncio.run(fns["pcb_apply_pours"](str(WIFI_DIR / "WiFi.PcbDoc"), plan=plan, dry_run=False))
    assert real["ok"] is True and real["made"] == 2
    assert [p["layer"] for _, p in rec.sent] == ["MidLayer1", "TopLayer"]


def test_the_polygon_handler_uses_only_proven_calls():
    """PCB_PlacePolygon is PCB_PlacePolygonRect's body with PCB_SetBoardOutline's
    vertex parsing: nothing either of them does not already do on AD26."""
    h = load(_PCB_PAS, minimum=100)
    body = h["PCB_PlacePolygon"]
    for ident in ("PCBObjectFactory(ePolyObject", "Polygon.PointCount", "TPolySegment", "ePolySegmentLine",
                  "MMToCoord(StrToFloatDef", "ClosePolySegments(Polygon", "ePolygonPourOver_SameNet",
                  "PCBM_BoardRegisteration", "Polygon.Rebuild", "BindPrimitiveToNet(FoundNet, Polygon)"):
        assert ident in body
    for ident in ("PourIndex :=", "Polygon.Name :=", "MilsToCoordF"):
        assert ident not in body
    rect, outline = h["PCB_PlacePolygonRect"], h["PCB_SetBoardOutline"]
    assert "MMToCoord(StrToFloatDef" in outline and "Polygon.PointCount" in rect
    assert "'place_polygon':" in _PCB_PAS.read_text(encoding="utf-8")
