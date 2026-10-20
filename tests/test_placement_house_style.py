# SPDX-License-Identifier: Apache-2.0
"""Phase (c): the Brass placer port, house-style profiles, place tools.

Synthetic boards run everywhere; the Kame and WiFi tests run when the
sample files are present (ALTIUM_AGENT_KAME / ALTIUM_AGENT_EXAMPLES).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from altium_agent.placement import service as svc
from altium_agent.placement.check import check_plan
from altium_agent.placement.footprints import At, Box, Footprint, Pin
from altium_agent.placement.placer import place_board, sanitize_levers
from altium_agent.placement.shape import rect_outline
from altium_agent.units import MILS_PER_MM


def _part(ref, w, h, nets, x=0.0, y=0.0, locked=False, footprint="0603"):
    pins = []
    n = len(nets)
    for i, net in enumerate(nets):
        px = -w / 2 + 0.3 + (w - 0.6) * (i / max(1, n - 1)) if n > 1 else 0.0
        pins.append(Pin(str(i + 1), net, round(px, 4), 0.0, 0.3, 0.3))
    box = Box(-w / 2, -h / 2, w / 2, h / 2)
    return Footprint(ref=ref, footprint=footprint, comment=ref, box=box, pins=pins, locked=locked,
                     through_hole=False, at=At(x, y, 0.0, "top"), pad_box=box)


def _board():
    ic = _part("U1", 5, 5, ["VCC", "GND", "A", "B", "C", "D", "E", "F"], footprint="QFN")
    parts = [ic]
    for i in range(1, 7):
        parts.append(_part(f"C{i}", 1.6, 0.8, ["VCC", "GND"]))
    for i, n in enumerate("ABCDEF", 1):
        parts.append(_part(f"R{i}", 1.6, 0.8, [n, "VCC"]))
    return parts


def _no_overlap(plan, parts):
    res = check_plan(parts, plan, poly=None, baseline=False)
    return not any(f["code"] == "overlap" for f in res["findings"])


LEVERS = {"effort": 0.02, "seed": 7, "bottom": "none", "spacing_mm": 0.3, "edge_gap_mm": 0.5}


def test_placer_places_without_overlap_inside_the_outline():
    parts = _board()
    out = rect_outline(20, 15, 0)
    plan = place_board(parts, LEVERS, outline=out)
    assert plan["score"]["unplaced"] == []
    assert plan["score"]["overlaps"] == 0
    assert _no_overlap(plan, parts)
    for p in plan["placements"]:
        assert 0 <= p["x_mm"] <= 20 and 0 <= p["y_mm"] <= 15


def test_placer_is_deterministic_for_a_seed():
    parts = _board()
    a = place_board(parts, LEVERS, outline=rect_outline(20, 15, 0))
    b = place_board(parts, LEVERS, outline=rect_outline(20, 15, 0))
    assert a["placements"] == b["placements"]


def test_fixed_part_stays_where_it_is_put():
    parts = _board()
    lev = {**LEVERS, "fixed": [{"ref": "U1", "x_mm": 6.0, "y_mm": 7.0, "rotation": 90}]}
    plan = place_board(parts, lev, outline=rect_outline(20, 15, 0))
    u1 = next(p for p in plan["placements"] if p["ref"] == "U1")
    assert (u1["x_mm"], u1["y_mm"], u1["rotation"]) == (6.0, 7.0, 90)


def test_decaps_end_up_near_their_ic():
    parts = _board()
    plan = place_board(parts, {**LEVERS, "effort": 0.05}, outline=rect_outline(30, 30, 0))
    res = check_plan(parts, plan, decap_max_mm=6.0, baseline=False)
    assert not any(f["code"] == "decap_far" for f in res["findings"])


def test_levers_accept_mm_suffix_and_body():
    s = sanitize_levers({"spacing_mm": 0.4, "edge_gap_mm": 1.0, "body": "copper", "bogus": 1})
    assert s == {"spacing": pytest.approx(0.4), "edge_gap": pytest.approx(1.0), "body": "copper"}
    assert "bogus" not in s


def test_check_flags_overlap_and_outline_and_inherited():
    a = _part("R1", 2, 1, ["A", "B"])
    b = _part("R2", 2, 1, ["A", "B"])
    poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
    plan = {"levers": {}, "placements": [
        {"ref": "R1", "x_mm": 5, "y_mm": 5, "rotation": 0, "side": "top"},
        {"ref": "R2", "x_mm": 5.5, "y_mm": 5, "rotation": 0, "side": "top"}]}
    res = check_plan([a, b], plan, poly=poly)
    assert {f["code"] for f in res["findings"]} >= {"overlap"}
    assert res["ok"] is False
    plan["placements"][1]["x_mm"] = 11
    res = check_plan([a, b], plan, poly=poly)
    assert "outside_outline" in {f["code"] for f in res["findings"]}
    # the same overlap on a board whose parts the plan does not move is inherited
    a2 = _part("R1", 2, 1, ["A", "B"], x=5, y=5)
    b2 = _part("R2", 2, 1, ["A", "B"], x=5.5, y=5)
    plan = {"levers": {}, "placements": [
        {"ref": "R1", "x_mm": 5, "y_mm": 5, "rotation": 0, "side": "top"},
        {"ref": "R2", "x_mm": 5.5, "y_mm": 5, "rotation": 0, "side": "top"}]}
    res = check_plan([a2, b2], plan, poly=poly)
    assert res["ok"] is True
    assert [f["code"] for f in res["findings"]] == ["inherited"]


def test_check_flags_parts_in_an_antenna_keepout():
    a = _part("R1", 2, 1, ["A", "B"])
    plan = {"levers": {}, "placements": [{"ref": "R1", "x_mm": 5, "y_mm": 5, "rotation": 0, "side": "top"}],
            "keepouts": [{"ref": "U9", "min_x_mm": 4, "min_y_mm": 4, "max_x_mm": 8, "max_y_mm": 8}]}
    res = check_plan([a], plan, baseline=False)
    assert "in_antenna_keepout" in {f["code"] for f in res["findings"]}


# ---------------------------------------------------------------- apply

def test_apply_ops_are_absolute_mils():
    plan = {"origin_mil": [1000.0, 2000.0], "placements": [
        {"ref": "U1", "x_mm": 2.54, "y_mm": -1.27, "rotation": 450, "side": "bottom"}]}
    x, y = round(1000 + 2.54 * MILS_PER_MM), round(2000 - 1.27 * MILS_PER_MM)
    assert svc.apply_ops(plan) == [f"U1,{x},{y},90,BottomLayer"]


def test_apply_ops_refuse_separator_in_designator():
    from altium_agent.offline.errors import InvalidArgument

    with pytest.raises(InvalidArgument):
        svc.apply_ops({"origin_mil": [0, 0], "placements": [{"ref": "U1|x", "x_mm": 0, "y_mm": 0}]})


def test_refuses_samples_and_failed_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC", str(tmp_path))
    sample = tmp_path / "Documents" / "Altium" / "Kame" / "Kame.PcbDoc"
    assert "sample or installed" in svc.refuse_reason({"source": str(sample), "check": {"ok": True}})
    mine = tmp_path / "mine" / "Kame.PcbDoc"
    assert svc.refuse_reason({"source": str(mine), "check": {"ok": True}}) is None
    bad = {"source": str(mine), "check": {"ok": False, "errors": ["overlap"]}}
    assert "overlap" in svc.refuse_reason(bad)
    assert svc.refuse_reason(bad, accept_findings=True) is None


class _Bridge:
    def __init__(self, file, origin):
        self.file, self.origin, self.sent = file, origin, []

    async def send_command_async(self, cmd, params, timeout=None):
        self.sent.append((cmd, params))
        if cmd == "pcb.get_layout_model":
            return {"file": self.file, "origin": self.origin}
        return {"applied": len(params["placements"].split("|")), "failed": 0}


def _tools():
    from altium_agent.tools.house_style import register_house_style_tools
    from altium_agent.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_house_style_tools(reg)
    return reg


def _plan_file(tmp_path, source):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"source": source, "origin_mil": [100.0, 200.0], "check": {"ok": True},
                             "placements": [{"ref": "R1", "x_mm": 1, "y_mm": 1, "rotation": 0, "side": "top"}]}))
    return str(p)


@pytest.mark.parametrize("focused,origin,expect", [
    ("C:/mine/b.PcbDoc", [1000000, 2000000], "applied"),
    ("C:/other/b.PcbDoc", [1000000, 2000000], "focused board"),
    ("C:/mine/b.PcbDoc", [0, 0], "origin"),
])
def test_place_apply_checks_the_live_board(tmp_path, monkeypatch, focused, origin, expect):
    import altium_agent.bridge as bridge_mod

    monkeypatch.setenv("PUBLIC", str(tmp_path / "public"))
    br = _Bridge(focused, origin)
    monkeypatch.setattr(bridge_mod, "get_bridge", lambda: br)
    fn = _tools().get("place_apply").fn
    plan = _plan_file(tmp_path, "C:\\mine\\b.PcbDoc")
    res = asyncio.run(fn(plan=plan, confirm=True, checkpoint=False))
    if expect == "applied":
        assert res.get("applied") is True
        assert br.sent[-1][0] == "pcb.import_placement"
        assert br.sent[-1][1]["placements"] == f"R1,{round(100 + MILS_PER_MM)},{round(200 + MILS_PER_MM)},0,TopLayer"
    else:
        assert res["ok"] is False and expect in res["refused"]
        assert all(c != "pcb.import_placement" for c, _ in br.sent)


def test_place_apply_previews_without_confirm(tmp_path, monkeypatch):
    import altium_agent.bridge as bridge_mod

    def boom():
        raise AssertionError("no bridge call without confirm")

    monkeypatch.setattr(bridge_mod, "get_bridge", boom)
    res = asyncio.run(_tools().get("place_apply").fn(plan=_plan_file(tmp_path, "C:/mine/b.PcbDoc")))
    assert res["applied"] is False and res["preview"]["parts"] == 1


# ---------------------------------------------------------------- real boards

KAME = os.environ.get("ALTIUM_AGENT_KAME", "")
EXAMPLES = os.environ.get("ALTIUM_AGENT_EXAMPLES", "")
WIFI = str(Path(EXAMPLES) / "Mini PC" / "Mini PC - WiFi" / "WiFi_miniPCIe.PrjPcb") if EXAMPLES else ""
needs_kame = pytest.mark.skipif(not (KAME and Path(KAME).is_file()), reason="Kame_FMU sample not available")
needs_wifi = pytest.mark.skipif(not (WIFI and Path(WIFI).is_file()), reason="WiFi example not available")


@pytest.fixture
def outdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ALTIUM_AGENT_OUTPUT_DIR", str(tmp_path / "out"))
    return tmp_path / "out"


@needs_kame
def test_kame_profile_levers_and_self_match(outdir):
    r = svc.ref_profile(KAME)
    prof = r["profile"]
    assert prof["board"]["copper_layers"] == 6
    assert prof["placement"]["parts"] == 102
    assert r["profile"]["levers"]["body"] == "copper"
    assert r["profile"]["levers"]["bottom"] == "all"
    assert Path(r["saved_to"]).is_file()
    same = svc.ref_compare("Kame_FMU", KAME)
    assert same["matches"] is True


@needs_kame
@needs_wifi
def test_wifi_against_kame_style(outdir):
    svc.ref_profile(KAME)
    c = svc.ref_compare("Kame_FMU", WIFI)
    codes = {f["code"] for f in c["findings"]}
    assert "layer_count_differs" in codes


@needs_wifi
def test_wifi_plan_asks_about_the_antenna_first(outdir):
    r = svc.place_plan(WIFI, levers={"effort": 0.01})
    assert r["ok"] is False and r["needs_answers"]
    assert r["questions"][0]["ref"] == "U1"


@needs_kame
def test_kame_plan_is_saved_with_its_origin(outdir):
    r = svc.place_plan(KAME, profile=None, levers={"effort": 0.01, "body": "copper", "bottom": "all",
                                                   "spacing_mm": 0.35, "edge_gap_mm": 1.4})
    plan = json.loads(Path(r["plan_path"]).read_text())
    assert plan["origin_mil"] and len(plan["placements"]) == 102
    assert Path(r["plan_path"]).is_relative_to(outdir)
    chk = svc.place_check(r["plan_path"])
    assert "counts" in chk
