# SPDX-License-Identifier: Apache-2.0
# Altium Agent (2026): see docs/PROVENANCE.md
"""Blind and buried vias through a routing session, end to end.

A session used to know through vias only: a via drawn L1->L3 was checked
as a disc on every layer, written to the SES as the one through padstack,
and imported Top to Bottom. That is what kept the SODIMM board's
back-to-back DRAMs fighting over the same via sites. Now a via carries its
span through the checks (it connects and blocks only the layers it passes),
the SES (a padstack with copper only on those layers, the Specctra way) and
the import rows (low and high layer for pcb.place_vias).
"""
from __future__ import annotations

import pytest

from altium_agent.bench.connectivity import (
    ALL, Area, Copper, Dot, PadShape, Seg, connect, copper_order, legacy_of, via_span)
from altium_agent.interactive.routing import _span_of
from altium_agent.offline.errors import InvalidArgument
from altium_agent.routing.fanout import FanoutResult, write_ses
from altium_agent.routing.ses_import import ses_rows, via_span_tokens
from altium_agent.routing.specctra import parse_ses

#: An 8-layer board, legacy ids top to bottom.
ORDER = [1, 2, 3, 4, 5, 6, 7, 32]
LMAP = {"TopLayer": 1, "MidLayer1": 2, "MidLayer2": 3, "MidLayer3": 4, "MidLayer4": 5,
        "MidLayer5": 6, "MidLayer6": 7, "BottomLayer": 32}


class _S:
    def __init__(self, kind, layer_id):
        self.kind, self.layer_id = kind, layer_id


class _Doc:
    stack = ([_S("copper", 0x01000001)] + [_S("dielectric", 0x01040001)]
             + [_S("copper", 0x01000001 + i) for i in range(1, 7)] + [_S("copper", 0x0100FFFF)])


def test_layer_ids_and_spans():
    assert legacy_of(0x01000001) == 1 and legacy_of(0x0100FFFF) == 32 and legacy_of(0x01010002) == 40
    assert copper_order(_Doc()) == ORDER
    assert via_span(ORDER, 1, 3) == frozenset({1, 2, 3})
    assert via_span(ORDER, 32, 6) == frozenset({6, 7, 32})
    assert via_span(ORDER, 1, 32) is None            # through
    assert via_span(ORDER, 1, 9) is None             # a layer the board lacks


def _chain(dot_layers):
    """Pad on Top -> track -> via -> track on Bottom -> pad on Bottom."""
    c = Copper()
    c.pads += [PadShape("N", 1, 0, 0, 0.2, 0.2, 0), PadShape("N", 32, 2, 0, 0.2, 0.2, 0)]
    c.segs += [Seg("N", 1, 0, 0, 1, 0, 0.1), Seg("N", 32, 1, 0, 2, 0, 0.1)]
    c.dots.append(Dot("N", 1, 0, 0.4, dot_layers))
    return connect(c)["N"].missing


def test_a_blind_via_does_not_reach_the_far_side():
    assert _chain(None) == 0                          # through: joined
    assert _chain(frozenset({1, 2, 3})) == 1          # L1-L3: the bottom pad is still open
    assert _chain(frozenset({6, 7, 32})) == 1         # L6-L8: the top pad is still open


def test_a_blind_via_joins_a_plane_only_within_its_span():
    def missing(layers):
        c = Copper()
        c.pads += [PadShape("G", 1, 0, 0, 0.2, 0.2, 0), PadShape("G", ALL, 5, 5, 0.3, 0.3, 0)]
        c.segs.append(Seg("G", 1, 0, 0, 1, 0, 0.1))
        c.dots.append(Dot("G", 1, 0, 0.4, layers))
        c.areas.append(Area("G", ALL, plane=40))
        return connect(c)["G"].missing
    assert missing(None) == 0
    assert missing(frozenset({1, 40})) == 0
    assert missing(frozenset({1, 2})) == 1


def test_the_span_survives_ses_and_becomes_import_rows(tmp_path):
    res = FanoutResult(tracks=[{"net": "DQ1", "layer": 1, "x1": 0, "y1": 0, "x2": 1, "y2": 0, "width": 0.1}],
                       vias=[{"net": "DQ1", "x": 1, "y": 0, "diameter": 0.25, "hole": 0.1,
                              "layer_start": 1, "layer_end": 3},
                             {"net": "DQ2", "x": 3, "y": 0, "diameter": 0.25, "hole": 0.1,
                              "layer_start": 3, "layer_end": 6},
                             {"net": "GND", "x": 5, "y": 0, "diameter": 0.4, "hole": 0.2}])
    res.rules = {"via_diameter_mm": 0.4, "via_hole_mm": 0.2}
    ses = write_ses(res, tmp_path / "s.ses", LMAP, "b")
    parsed = parse_ses(ses, 0.2)
    by_net = {v["net"]: v for v in parsed.vias}
    assert by_net["DQ1"]["layers"] == ["TopLayer", "MidLayer1", "MidLayer2"]
    assert abs(by_net["DQ1"]["hole"] - 0.1) < 1e-9 and abs(by_net["GND"]["hole"] - 0.2) < 1e-9
    rows = ses_rows(ses, LMAP, {"via_hole_mm": 0.2}, (0.0, 0.0))
    spans = {r.split(",")[-1]: tuple(r.split(",")[4:6]) for r in rows.vias}
    assert spans == {"DQ1": ("TopLayer", "MidLayer2"), "DQ2": ("MidLayer2", "MidLayer5"),
                     "GND": ("TopLayer", "BottomLayer")}
    assert rows.summary()["spanned_vias"] == 2


@pytest.mark.parametrize("layers, want", [
    ([], ("TopLayer", "BottomLayer")),
    (["TopLayer"], ("TopLayer", "BottomLayer")),
    (["TopLayer", "BottomLayer"], ("TopLayer", "BottomLayer")),
    (list(LMAP), ("TopLayer", "BottomLayer")),
    (["MidLayer5", "MidLayer6", "BottomLayer"], ("MidLayer5", "BottomLayer")),
])
def test_third_party_padstacks_read_as_expected(layers, want):
    assert via_span_tokens(layers, LMAP) == want


def test_draw_spans():
    pt = {}
    assert _span_of(ORDER, "through", 1, 3, pt) == (1, 32)
    assert _span_of(ORDER, "span", 1, 3, pt) == (1, 3)
    assert _span_of(ORDER, "span", 3, 1, pt) == (1, 3)
    assert _span_of(ORDER, "span", 3, 6, pt) == (3, 6)            # buried
    assert _span_of(ORDER, "span", 1, 32, pt) == (1, 32)          # all of it is through
    assert _span_of(ORDER, "through", 1, 2,
                    {"via_low_layer": "TopLayer", "via_high_layer": "MidLayer2"}) == (1, 3)
    with pytest.raises(InvalidArgument):
        _span_of(ORDER, "through", 1, 6, {"via_low_layer": "TopLayer", "via_high_layer": "MidLayer2"})
    with pytest.raises(InvalidArgument):
        _span_of(ORDER, "blind", 1, 3, pt)
