# SPDX-License-Identifier: Apache-2.0
"""Power and ground go to pours and planes: the broker's order and the tool.

Routing every net at once on Altium's WiFi example made 66% of the signal
connections: GND and 3V3, poured over the whole board, were routed as
tracks among the signals. A via from each power and ground pad to its plane
first, then the signals, made all of them. These hold the broker's plan to
that order, and route_power_fanout to running from the saved file, with no
Altium, into a job route_import_ses can write.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from altium_agent.offline.files.pcbdoc import NONE16, Pad, PcbComponent, PcbDocument, StackLayer

_PUBLIC = Path(os.environ.get("PUBLIC") or Path(os.environ.get("SystemDrive", "C:") + os.sep) / "Users" / "Public")
WIFI = _PUBLIC / "Documents" / "Altium" / "AD26" / "Examples" / "Mini PC" / "Mini PC - WiFi" / "WiFi.PcbDoc"


def _tools():
    from altium_agent.tools.registry import ToolRegistry
    from altium_agent.tools.routing_broker import register_routing_broker_tools

    reg = ToolRegistry()
    register_routing_broker_tools(reg)
    return reg


def test_prior_routing_lands_on_the_board_as_copper(tmp_path):
    from altium_agent.routing.service import with_prior_routing

    doc = PcbDocument(Path("b.PcbDoc"), {}, (0.0, 0.0), [],
                      [StackLayer(0, "Top", 0x01000001, "copper")],
                      [PcbComponent(0, "R1", "FP", "", 10, 10, 0, "top", False, 1, "", "", "")],
                      ["A", "B"], [], [], [], [])
    doc.pads = [Pad("1", 1, 0, 0, 9, 10, 1, 1, 0, False, 2, 0)]
    ses = tmp_path / "first.ses"
    ses.write_text("(session s (routes (resolution um 10) (network_out (net A "
                   "(wire (path Top 2500 90000 100000 290000 100000)) (via V 110000 150000)))))")
    out = with_prior_routing(doc, [{"ses": str(ses), "layer_map": {"Top": 1}, "rules": {"via_hole_mm": 0.3}}])
    assert doc.tracks == [] and doc.vias == []                    # the board read is not edited
    (t,) = out.tracks
    assert (t.layer, t.net, t.component) == (1, 0, NONE16)
    assert (t.x1, t.y1, t.x2, t.y2) == pytest.approx((9, 10, 29, 10))
    (v,) = out.vias
    assert (v.net, v.x, v.y, v.hole) == (0, pytest.approx(11), pytest.approx(15), 0.3)



@pytest.mark.skipif(not WIFI.exists(), reason="Altium's WiFi example is not installed")
def test_the_fanout_tool_runs_from_the_saved_file_and_never_altium(tmp_path, monkeypatch):
    import shutil

    import altium_agent.bridge as bridge_mod
    from altium_agent.bench import mask
    from altium_agent.design.jobs import get_job_store
    from altium_agent.routing.service import ses_rows_for

    folder = tmp_path / "wifi"
    shutil.copytree(WIFI.parent, folder, ignore=shutil.ignore_patterns("History", "Project Outputs*"))
    pcb = folder / "WiFi.PcbDoc"
    monkeypatch.setenv(mask.ENV, str(mask.write(tmp_path / "m.json", {str(pcb): {"strip_routing": True}})))
    monkeypatch.setenv("ALTIUM_AGENT_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(bridge_mod, "get_bridge", lambda: (_ for _ in ()).throw(AssertionError("no Altium")))
    r = asyncio.run(_tools().get("route_power_fanout").fn(expect_file=str(pcb)))
    assert r["ok"] and r["vias_placed"] > 5 and set(r["nets"]) == {"GND", "3V3"}
    rows = ses_rows_for(get_job_store().get(r["job_id"]).result)
    assert rows.summary()["vias"] == r["vias_placed"]
    again = asyncio.run(_tools().get("route_power_fanout").fn(expect_file=str(pcb), pads=False,
                                                              stitch_spacing_mm=5.0))
    assert again["ok"] and again["vias_placed"] == 0 and again["rules"]["stitching_vias"] > 0


@pytest.mark.skipif(not WIFI.exists(), reason="Altium's WiFi example is not installed")
def test_the_plan_fans_power_out_before_the_bulk_and_stitches_after():
    from altium_agent.bench.transform import strip_routing
    from altium_agent.offline.files.pcbdoc import read_pcbdoc
    from altium_agent.routing.broker import plan

    p = plan(strip_routing(read_pcbdoc(WIFI)))
    tier = [s.get("tier") for s in p["steps"]]
    fan = p["steps"][tier.index("power")]
    assert fan["tool"] == "route_power_fanout" and set(fan["args"]["nets"]) == {"GND", "3V3"}
    bulk = p["steps"][tier.index("bulk")]
    assert not {"GND", "3V3"} & set(bulk["args"]["nets"])
    assert tier.index("power") < tier.index("bulk") < tier.index("stitch")
    assert p["steps"][tier.index("stitch")]["args"] == {"expect_file": str(WIFI), "nets": ["3V3", "GND"],
                                                        "pads": False, "stitch_spacing_mm": 5.0}
