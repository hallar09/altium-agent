# SPDX-License-Identifier: Apache-2.0
# Altium Agent (2026): see docs/PROVENANCE.md
"""Routing that does not eat the usage limit.

The SODIMM job spent most of its tokens on replies and on agents that
started cold: a DRC reply of 100 full violations per run, 40-60 violation
rows on every session call, nine planners re-reading the whole board. These
pin the cheap defaults (summaries, with the full lists one call away) and
the work packets that let a few planners route disjoint parts of a board
and merge back.
"""
from __future__ import annotations

import json

import pytest

from altium_agent.tools.pcb import _compact_drc

from tests.test_interactive_sessions import copy, wifi  # noqa: F401  (fixture)


def _v(rule, net="A", other="B"):
    return {"name": rule, "rule": rule, "x_mils": 1, "y_mils": 2, "layer": "Top Layer",
            "description": "x" * 200,
            "primitive1": {"detail": "Track " * 20, "net": net}, "primitive2": {"detail": "Pad " * 20, "net": other}}


def test_a_drc_reply_is_a_summary_by_default():
    rows = [_v("SilkToSilkClearance")] * 60 + [_v("Clearance", "DQ1", "GND")] * 30 + [_v("UnRoutedNet")] * 10
    full = {"violation_count": 742, "drc_confirmed": True, "violations": rows}
    out = _compact_drc(full)
    assert out["violation_count"] == 742 and out["drc_confirmed"] is True
    assert out["violations_by_rule"] == {"SilkToSilkClearance": 60, "Clearance": 30, "UnRoutedNet": 10}
    assert len(out["violations"]) == 10 and out["noise_rows"] == 60
    assert out["violations"][0] == {"rule": "Clearance", "x_mils": 1, "y_mils": 2, "layer": "Top Layer",
                                    "nets": ["DQ1", "GND"]}
    assert len(json.dumps(out)) < len(json.dumps(full)) / 10


def test_a_reply_without_violations_is_left_alone():
    assert _compact_drc({"ok": False, "reason": "x"}) == {"ok": False, "reason": "x"}


@wifi
def test_packets_split_route_and_merge(copy, tmp_path, monkeypatch):  # noqa: F811
    from altium_agent.bench import mask
    from altium_agent.interactive import routing as R
    from altium_agent.offline.errors import InvalidArgument

    pcb = copy / "WiFi.PcbDoc"
    monkeypatch.setenv(mask.ENV, str(mask.write(tmp_path / "mask.json", {str(pcb): {"strip_routing": True}})))
    s = R.start(pcb=str(pcb), name="t")["session"]

    c = R.check(s)
    assert c["open_net_count"] >= len(c["open_nets"]) and len(c["violation_list"]) <= 12
    assert len(R.check(s, detail="full")["open_nets"]) == c["open_net_count"]
    with pytest.raises(InvalidArgument):
        R.check(s, detail="everything")

    p = R.packets(s, count=2, margin_mm=0.5)
    assert p["packets"], "the WiFi board should give at least one packet"
    all_nets = [n for k in p["packets"] for n in R.S.load(k["session"], "route")[1]["packet"]["nets"]]
    assert len(all_nets) == len(set(all_nets)), "a net is in two packets"
    assert not set(all_nets) & set(p["shared"])
    assert "{session}" in p["brief"]

    first = p["packets"][0]
    child = first["session"]
    own = R.S.load(child, "route")[1]["packet"]["nets"]
    if p["shared"]:
        with pytest.raises(InvalidArgument, match="packet"):
            R.route(child, [p["shared"][0]])
    with pytest.raises(InvalidArgument):
        R.packets(child)                                   # a packet is not split again
    r = R.route(child, own[:1], width_mm=0.25)
    assert r["added"]["tracks"] > 0

    before = R.check(s)["open_connections"]
    m = R.merge(s, [child])
    assert m["merged"][0]["tracks"] == r["added"]["tracks"] and m["dropped_foreign"] == 0
    assert R.check(s)["open_connections"] < before
    assert R.undo(s)["undone"] == 1 and R.check(s)["open_connections"] == before

    other = R.start(pcb=str(pcb), name="u")["session"]
    with pytest.raises(InvalidArgument, match="not a packet"):
        R.merge(other, [child])
