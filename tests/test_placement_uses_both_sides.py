# SPDX-License-Identifier: Apache-2.0
"""A placement that fits: both sides when one is not enough, caps at their pins.

On the benchmark boards (Altium's examples, laid out by people) the
people put 50-60% of the parts underneath, and the placer, which kept
everything on top by default, had to fit 0.73-1.0 of the board's area on
one side: Kame ended with 67 overlapping pairs and 92 parts it could not
fit, WiFi with 9 and 23, SL1 with 18 and 61. ``bottom="auto"`` (the
default) uses the bottom when one side would be over half full, and every
decoupling cap is moved to the nearest legal spot by its IC's supply pin
after the anneal (half of them had ended over 3 mm away).
"""

from __future__ import annotations

import math

from altium_agent.placement.check import check_plan
from altium_agent.placement.placer import (AUTO_ALL_AT, AUTO_PASSIVES_AT, Levers, auto_bottom, place_board,
                                           sanitize_levers)
from altium_agent.placement.shape import rect_outline

from tests.test_placement_house_style import _board, _part

FAST = {"effort": 0.03, "seed": 3, "spacing_mm": 0.3, "edge_gap_mm": 0.5}


def test_auto_is_the_default_and_reads_the_density():
    assert Levers().bottom == "auto"
    assert sanitize_levers({"bottom": "auto"}) == {"bottom": "auto"}
    assert auto_bottom(0.3) == "none"
    assert auto_bottom(AUTO_PASSIVES_AT + 0.01) == "passives"
    assert auto_bottom(AUTO_ALL_AT + 0.01) == "all"


def _dense():
    """Twelve 4x4 mm ICs and 40 passives on a board one side cannot hold."""
    parts = [_part(f"U{i}", 4, 4, [f"N{i}", f"M{i}", "VCC", "GND", f"P{i}", f"Q{i}", f"S{i}", f"T{i}"],
                   footprint="QFN") for i in range(1, 13)]
    parts += [_part(f"C{i}", 1.6, 0.8, ["VCC", "GND"]) for i in range(1, 21)]
    parts += [_part(f"R{i}", 1.6, 0.8, [f"N{1 + i % 12}", f"P{1 + (i + 5) % 12}"]) for i in range(1, 21)]
    return parts


def test_a_full_board_uses_the_bottom_and_fits():
    parts = _dense()
    out = rect_outline(20, 16, 0)
    one = place_board(parts, {**FAST, "bottom": "none"}, outline=out)
    auto = place_board(parts, FAST, outline=out)
    assert auto["levers"]["bottom_used"] in ("passives", "all")
    assert any("bottom side" in n for n in auto["notes"])
    assert auto["score"]["bottom_parts"] > 0
    assert len(one["score"]["unplaced"]) > 20 and one["score"]["overlaps"] > 10   # one side cannot hold it
    assert auto["score"]["unplaced"] == [] and auto["score"]["overlaps"] == 0
    res = check_plan(parts, auto, baseline=False)
    assert not any(f["code"] == "overlap" for f in res["findings"])


def test_a_roomy_board_stays_on_top_and_none_is_honoured():
    parts = _board()
    roomy = place_board(parts, FAST, outline=rect_outline(40, 30, 0))
    assert roomy["levers"]["bottom_used"] == "none" and roomy["score"]["bottom_parts"] == 0
    forced = place_board(_dense(), {**FAST, "bottom": "none"}, outline=rect_outline(18, 15, 0))
    assert forced["levers"]["bottom_used"] == "none" and forced["score"]["bottom_parts"] == 0


def test_decaps_are_moved_to_their_supply_pin():
    parts = _board()
    plan = place_board(parts, {**FAST, "bottom": "none", "decap_weight": 0.0}, outline=rect_outline(30, 30, 0))
    at = {p["ref"]: p for p in plan["placements"]}
    u1 = at["U1"]
    # U1's VCC pin is its first pin: local (-2.2, 0); rotation k*90 on top
    k = int(u1["rotation"]) // 90 % 4
    lx, ly = -2.2, 0.0
    for _ in range(k):
        lx, ly = -ly, lx
    vx, vy = u1["x_mm"] + lx, u1["y_mm"] + ly
    caps = [at[f"C{i}"] for i in range(1, 7)]
    near = [c for c in caps if math.hypot(c["x_mm"] - vx, c["y_mm"] - vy) < 4.0]
    # even with no pull in the anneal at all, the pass brings them in
    assert len(near) >= 3, (vx, vy, caps)
    assert any("moved next to their IC" in n for n in plan["notes"])
    res = check_plan(parts, plan, baseline=False)
    assert not any(f["code"] == "overlap" for f in res["findings"])
