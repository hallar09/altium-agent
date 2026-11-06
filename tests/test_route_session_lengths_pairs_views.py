# SPDX-License-Identifier: Apache-2.0
"""Lengths, pairs and close-up views in a routing session.

The SODIMM routing agents wrote some 390 scripts of their own, and the
commonest were the three things here: measuring a net pad to pad with its
vias and layer scaling (``lengths.py``, ``measure.py``: ~25), drawing a
pair by hand point by point for each net (``pair.py``, ``dqs.py``), and
rendering one layer or one corner of the board (``rend.py``: 31). Each is
now one session call.
"""

from __future__ import annotations

import math

import pytest

from altium_agent.offline.errors import InvalidArgument

from tests.test_interactive_sessions import copy, wifi  # noqa: F401  (fixture)


@pytest.fixture
def stripped(copy, tmp_path, monkeypatch):  # noqa: F811
    from altium_agent.bench import mask
    from altium_agent.interactive import routing as R

    pcb = copy / "WiFi.PcbDoc"
    monkeypatch.setenv(mask.ENV, str(mask.write(tmp_path / "mask.json", {str(pcb): {"strip_routing": True}})))
    return R.start(pcb=str(pcb), name="t")["session"]


def _pt(x, y, layer):
    return {"x_mm": x, "y_mm": y, "layer": layer}


@wifi
def test_a_drawn_net_measures_pad_to_pad_with_its_vias(stripped):
    from altium_agent.interactive import routing as R

    # U1.14 (top, 11.04, 5.306) to R13.2 (top, 7.775, 22.2) by way of the bottom
    pts = [_pt(11.04, 5.306, "TopLayer"), _pt(11.04, 10, "TopLayer"), _pt(11.04, 10, "BottomLayer"),
           _pt(7.775, 10, "BottomLayer"), _pt(7.775, 10, "TopLayer"), _pt(7.775, 22.2, "TopLayer")]
    R.draw(stripped, "NetR13_2", pts, width_mm=0.2)
    out = R.lengths(stripped, ["NetR13_2"], from_ref="U1", to_ref="R13")
    row = out["nets"]["NetR13_2"]
    tracks = (10 - 5.306) + (11.04 - 7.775) + (22.2 - 10)
    depth = 1.225 - 0.025                       # top copper to bottom copper, mid to mid
    assert row["vias"] == 2 and row["via_mm"] == pytest.approx(2 * depth, abs=1e-3)
    assert row["path_mm"] == pytest.approx(tracks + 2 * depth, abs=0.01)
    assert row["total_mm"] == pytest.approx(row["path_mm"], abs=0.01)
    assert row["by_layer_mm"]["BottomLayer"] == pytest.approx(11.04 - 7.775, abs=1e-3)

    scaled = R.lengths(stripped, ["NetR13_2"], from_ref="U1", to_ref="R13",
                       layer_scale={"BottomLayer": 2.0}, via_length=False)["nets"]["NetR13_2"]
    assert scaled["path_mm"] == pytest.approx(tracks + (11.04 - 7.775), abs=0.01)

    # not joined yet: a net with no copper says so instead of a number
    other = R.lengths(stripped, ["NetR12_2"], from_ref="U1", to_ref="R12")["nets"]["NetR12_2"]
    assert other["path_mm"] is None and other["note"] == "not routed"
    with pytest.raises(InvalidArgument):
        R.lengths(stripped, ["NetR13_2"], from_ref="U1")
    with pytest.raises(InvalidArgument):
        R.lengths(stripped, ["NO_SUCH_NET"])


@wifi
def test_groups_and_pairs_report_deltas_and_skew(stripped):
    from altium_agent.interactive import routing as R

    R.route(stripped, ["PA12_USB_D_P", "PA12_USB_D_N"], width_mm=0.25)
    out = R.lengths(stripped, from_ref="P1", to_ref="U2", pairs=[["PA12_USB_D_P", "PA12_USB_D_N"]],
                    groups=[{"name": "usb", "nets": ["PA12_USB_D_P", "PA12_USB_D_N"], "tolerance_mm": 0.0001}])
    p, n = out["nets"]["PA12_USB_D_P"]["path_mm"], out["nets"]["PA12_USB_D_N"]["path_mm"]
    straight = math.hypot(21.2 - 13.75, 1.4 - 7.725)
    assert p is not None and n is not None and p >= straight - 0.01
    assert out["pairs"][0]["skew_mm"] == pytest.approx(p - n, abs=1e-3)
    g = out["groups"][0]
    assert g["target_mm"] == pytest.approx(max(p, n), abs=1e-3)
    assert g["spread_mm"] == pytest.approx(abs(p - n), abs=1e-3)
    if abs(p - n) > 0.0001:
        assert g["outside_tolerance"] == [min(("PA12_USB_D_P", p), ("PA12_USB_D_N", n), key=lambda kv: kv[1])[0]]


@wifi
def test_a_pair_is_drawn_from_its_centreline(stripped):
    from altium_agent.interactive import routing as R

    centre = [_pt(15, 10, "BottomLayer"), _pt(16.5, 10, "BottomLayer"), _pt(16.5, 10, "TopLayer"),
              _pt(18, 10, "TopLayer")]
    before = len(R.S.load(stripped, "route")[1]["history"])
    out = R.draw_pair(stripped, "PA12_USB_D_P", "PA12_USB_D_N", centre, gap_mm=0.15, width_mm=0.2)
    assert out["added"]["vias"] == 2 and out["drawn_skew_mm"] == pytest.approx(0.0, abs=1e-6)
    data = R.S.load(stripped, "route")[1]
    assert len(data["history"]) == before + 1                  # one action: one undo takes both
    d = (0.15 + 0.2) / 2
    p = [t for t in data["added"]["tracks"] if t["net"] == "PA12_USB_D_P"]
    n = [t for t in data["added"]["tracks"] if t["net"] == "PA12_USB_D_N"]
    # travelling +x, the positive net runs on the left (+y)
    assert all(t["y1"] == pytest.approx(10 + d) and t["y2"] == pytest.approx(10 + d) for t in p if t["x1"] != t["x2"])
    assert all(t["y1"] == pytest.approx(10 - d) and t["y2"] == pytest.approx(10 - d) for t in n if t["x1"] != t["x2"])
    vp, vn = [v for v in data["added"]["vias"][-2:]]
    rules = R._rules(R._doc(data))
    assert math.hypot(vp["x"] - vn["x"], vp["y"] - vn["y"]) >= rules["via_diameter_mm"] + rules["clearance_mm"] - 1e-6
    assert R.undo(stripped)["undone"] == 1
    after = R.S.load(stripped, "route")[1]["added"]
    assert len(after["tracks"]) == len(data["added"]["tracks"]) - out["added"]["tracks"]
    assert len(after["vias"]) == len(data["added"]["vias"]) - 2
    with pytest.raises(InvalidArgument):
        R.draw_pair(stripped, "PA12_USB_D_P", "PA12_USB_D_P", centre)


def test_offset_mitres_a_corner_at_the_right_distance():
    from altium_agent.interactive.routing import _offset_path

    pts = [_pt(0, 0, "TopLayer"), _pt(10, 0, "TopLayer"), _pt(10, 10, "TopLayer")]
    left = _offset_path(pts, 1.0, 1.0)
    # left of +x then +y: the corner moves inward to (9, 1)
    assert (left[0]["x_mm"], left[0]["y_mm"]) == pytest.approx((0, 1))
    assert (left[1]["x_mm"], left[1]["y_mm"]) == pytest.approx((9, 1))
    assert (left[2]["x_mm"], left[2]["y_mm"]) == pytest.approx((9, 10))


@wifi
def test_view_draws_one_layer_or_one_window(stripped):
    from altium_agent.interactive import routing as R

    R.route(stripped, ["PA12_USB_D_P", "PA12_USB_D_N"], width_mm=0.25)
    one = R.view(stripped, layers=["BottomLayer"])
    zoom = R.view(stripped, region_mm=[12, 0, 23, 9])
    for v in (one, zoom):
        assert v.get("png") or v.get("svg")
    assert "BottomLayer" in (one.get("png") or one["svg"]) and "zoom" in (zoom.get("png") or zoom["svg"])
    with pytest.raises(InvalidArgument):
        R.view(stripped, layers=["Top Overlay"])
    with pytest.raises(InvalidArgument):
        R.view(stripped, region_mm=[5, 5, 1, 1])
