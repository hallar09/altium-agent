# SPDX-License-Identifier: Apache-2.0
"""Blind and buried vias from the fan-out and the router, not only by hand.

Before, a via type was used only where an agent drew a path with
``via_span="span"``: the power fan-out and the router placed through vias
even on a board with drill pairs, so under BGAs on both sides every power
via and every escape via took a site on every layer. Now, with via types
(the board's drill pairs, or route_session_via_types before they exist):

* a power or ground pad's fan-out via is the shortest pair that reaches
  its pour, and is kept clear of other copper on those layers only;
* each via the router lays is shrunk to the shortest pair spanning what
  its net uses there, and the nets still open get one more try.
"""

from __future__ import annotations

import pytest

from altium_agent.offline.errors import InvalidArgument
from altium_agent.routing.fanout import _clear, _Disc, _Index, _Seg, _bbox, _gap_disc, best_span, span_layers

from tests.test_interactive_sessions import copy, wifi  # noqa: F401  (fixture)

ORDER = [1, 2, 3, 4, 5, 32]          # a six-layer board's legacy ids, top to bottom


def test_the_shortest_pair_that_spans_the_layers_wins():
    pairs = [(1, 2), (1, 3), (4, 32), (2, 5)]
    assert best_span(ORDER, pairs, {1, 2}) == (1, 2)
    assert best_span(ORDER, pairs, {1, 3}) == (1, 3)
    assert best_span(ORDER, pairs, {3, 4}) == (2, 5)
    assert best_span(ORDER, pairs, {1, 32}) is None        # only a through via
    assert best_span(ORDER, [(1, 32)], {1, 2}) is None     # a "pair" from top to bottom IS through
    assert span_layers(ORDER, 4, 32) == {4, 5, 32}


def test_a_blind_via_clears_only_its_own_layers():
    idx = _Index()
    track = _Seg("OTHER", 32, -1, 0, 1, 0, 0.2)            # on the bottom, right under the via
    idx.add(track, *_bbox(track))
    blind = _Disc("GND", 0, 0, 0.2, frozenset({1, 2}))
    assert _clear(idx, blind, _gap_disc, 0.1, frozenset({1, 2}))
    through = _Disc("GND", 0, 0, 0.2)
    assert not _clear(idx, through, _gap_disc, 0.1, None)


@pytest.fixture
def stripped(copy, tmp_path, monkeypatch):  # noqa: F811
    from altium_agent.bench import mask
    from altium_agent.interactive import routing as R

    pcb = copy / "WiFi.PcbDoc"
    monkeypatch.setenv(mask.ENV, str(mask.write(tmp_path / "mask.json", {str(pcb): {"strip_routing": True}})))
    return R.start(pcb=str(pcb), name="t")["session"]


@wifi
def test_fanout_gives_a_bottom_pad_a_blind_via_to_its_pour(stripped):
    """WiFi's 3V3 is poured on int1_power (MidLayer1) and its pads that need a
    via are on the bottom: with a MidLayer1-Bottom via type, those vias span
    exactly that."""
    from altium_agent.interactive import routing as R

    t = R.via_types(stripped, [["MidLayer1", "BottomLayer"]])
    assert t["via_types"] == ["MidLayer1-BottomLayer"]
    assert t["add_before_import"] == [{"tool": "pcb_add_drill_pair",
                                       "arguments": {"low_layer": "MidLayer1", "high_layer": "BottomLayer"}}]
    f = R.fanout(stripped)
    vias = R.S.load(stripped, "route")[1]["added"]["vias"]
    blind = [v for v in vias if "layer_start" in v]
    assert f["blind_or_buried_placed"] == len(blind) > 0
    assert f["blind_or_buried_vias"] == ["MidLayer1-BottomLayer"]
    assert all((v["layer_start"], v["layer_end"]) == (2, 32) and v["net"] == "3V3" for v in blind)
    assert f["missing_drill_pairs"][0]["arguments"] == {"low_layer": "MidLayer1", "high_layer": "BottomLayer"}

    plain = R.start(pcb=R.S.load(stripped, "route")[1]["source"], name="u")["session"]
    R.via_types(plain, [["MidLayer1", "BottomLayer"]])
    assert R.fanout(plain, blind_vias=False)["blind_or_buried_placed"] == 0


@wifi
def test_routed_vias_are_shrunk_to_a_pair(stripped):
    from altium_agent.interactive import routing as R

    R.via_types(stripped, [["TopLayer", "MidLayer1"], ["MidLayer2", "BottomLayer"]])
    R.fanout(stripped)
    nets = list(R.check(stripped, detail="full")["open_nets"])[:12]
    r = R.route(stripped, nets)
    assert r["blind_or_buried"]["vias_shrunk"] > 0
    vias = R.S.load(stripped, "route")[1]["added"]["vias"]
    spans = {(v["layer_start"], v["layer_end"]) for v in vias if "layer_start" in v}
    assert spans and spans <= {(1, 2), (3, 32)}
    assert isinstance(R.check(stripped)["violations"], int)      # the check reads the spans


def test_shrink_reads_what_the_net_uses_at_the_via():
    from altium_agent.interactive.routing import shrink_spans
    from altium_agent.offline.files.pcbdoc import PcbDocument, StackLayer

    stack = [StackLayer(index=i, name=n, kind="copper", layer_id=lid, copper_thickness_mm=0.035)
             for i, (n, lid) in enumerate((("Top", 0x01000001), ("Mid1", 0x01000002), ("Mid2", 0x01000003),
                                           ("Bottom", 0x0100FFFF)))]
    doc = PcbDocument.__new__(PcbDocument)
    doc.stack, doc.pads, doc.nets, doc.polygons, doc.board = stack, [], ["A"], [], {}
    tracks = [{"net": "A", "layer": 1, "x1": -1, "y1": 0, "x2": 0, "y2": 0, "width": 0.2},
              {"net": "A", "layer": 2, "x1": 0, "y1": 0, "x2": 0, "y2": 2, "width": 0.2}]
    vias = [{"net": "A", "x": 0, "y": 0, "diameter": 0.4, "hole": 0.2},
            {"net": "A", "x": 5, "y": 5, "diameter": 0.4, "hole": 0.2}]       # joins nothing: left alone
    assert shrink_spans(doc, tracks, vias, [(1, 2), (3, 32)]) == 1
    assert (vias[0]["layer_start"], vias[0]["layer_end"]) == (1, 2) and "layer_start" not in vias[1]
    v = [{"net": "A", "x": 0, "y": 0, "diameter": 0.4, "hole": 0.2}]
    assert shrink_spans(doc, tracks, v, [(3, 32)]) == 0                         # no pair spans Top+Mid1


@wifi
def test_via_types_refuse_names_that_are_not_copper(stripped):
    from altium_agent.interactive import routing as R

    with pytest.raises(InvalidArgument):
        R.via_types(stripped, [["TopLayer"]])
    with pytest.raises(InvalidArgument):
        R.via_types(stripped, [["Top Overlay", "BottomLayer"]])
    R.via_types(stripped, [["TopLayer", "MidLayer1"]])
    assert R.via_types(stripped, clear=True)["via_types"] == ["TopLayer-BottomLayer"]   # the board's own
    assert "drill_pairs" not in R.S.load(stripped, "route")[1]
