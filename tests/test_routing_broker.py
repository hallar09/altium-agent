# SPDX-License-Identifier: Apache-2.0
"""Phase (d): the routing broker, Specctra round trip, Freerouting runner,
convergence and via styles.

Everything here runs offline. A real Freerouting run happens only when
ALTIUM_AGENT_FREEROUTING points at an installation (it is never shipped).
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import zipfile
from pathlib import Path

import pytest

from altium_agent.offline.files.pcbdoc import NONE16, read_pcbdoc
from altium_agent.routing import freerouting as fr
from altium_agent.routing.broker import classify, pair_nets, plan
from altium_agent.routing.converge import converge
from altium_agent.routing.ses_import import layer_token, ses_rows
from altium_agent.routing.specctra import DsnOptions, export_dsn
from altium_agent.routing.via_style import check as via_check
from altium_agent.routing.via_style import read_back
from altium_agent.units import MILS_PER_MM

KAME = os.environ.get("ALTIUM_AGENT_KAME", "")
KAME_PCB = str(Path(KAME).with_suffix(".PcbDoc")) if KAME else ""
EXAMPLES = os.environ.get("ALTIUM_AGENT_EXAMPLES", "")
WIFI_PCB = str(Path(EXAMPLES) / "Mini PC" / "Mini PC - WiFi" / "WiFi.PcbDoc") if EXAMPLES else ""
needs_kame = pytest.mark.skipif(not (KAME_PCB and Path(KAME_PCB).is_file()), reason="Kame_FMU sample not available")
needs_wifi = pytest.mark.skipif(not (WIFI_PCB and Path(WIFI_PCB).is_file()), reason="WiFi example not available")


def _stripped(doc, keep=()):
    """A copy of the board with its free copper taken up, except on ``keep`` nets.

    A copy: read_pcbdoc caches documents, so changing one in place would
    change it for every later reader."""
    doc = copy.deepcopy(doc)
    doc.tracks = [t for t in doc.tracks if (t.component != NONE16 or t.polygon != NONE16)
                  or (t.net != NONE16 and doc.nets[t.net] in keep)]
    doc.arcs = [a for a in doc.arcs if a.component != NONE16]
    doc.vias = [v for v in doc.vias if v.component != NONE16]
    return doc


# ---------------------------------------------------------------- SES import

SES = """(session test
  (routes (resolution um 10)
    (library_out (padstack "Via[0-5]_400:200_um" (shape (circle Top 4000 0 0)) (shape (circle Bottom 4000 0 0))))
    (network_out
      (net "SIG1" (wire (path Top 1500 0 0 10000 0 10000 5000))
                  (via "Via[0-5]_400:200_um" 10000 5000))
      (net "SIG2" (wire (path L3 1500 0 20000 10000 20000)))
      (net "OLD" (wire (path Top 1500 0 30000 10000 30000)))
      (net "GND" (wire (path GND2 1500 0 40000 10000 40000))))))
"""


class _Doc:
    """Just what ses_rows reads of an existing board."""

    def __init__(self, tracks=(), vias=()):
        self.tracks, self.vias = list(tracks), list(vias)


class _T:
    def __init__(self, x1, y1, x2, y2, layer):
        self.x1, self.y1, self.x2, self.y2, self.layer = x1, y1, x2, y2, layer


def test_ses_rows_absolute_mils_dedupe_planes_and_width_floor(tmp_path):
    ses = tmp_path / "b.ses"
    ses.write_text(SES)
    layer_map = {"Top": 1, "GND2": 2, "L3": 3, "Bottom": 32}
    rules = {"via_hole_mm": 0.2, "min_width_mm": 0.2, "net_min_width_mm": {}}
    existing = _Doc(tracks=[_T(0, 3.0, 1.0, 3.0, 1)])
    rows = ses_rows(ses, layer_map, rules, (1000.0, 2000.0), existing=existing)
    s = rows.summary()
    assert s["skipped_existing"] == 1          # OLD was on the board already
    assert s["widths_clamped"] == 4            # 0.15 mm -> the 0.2 mm floor
    assert rows.tracks[0].split(",")[:6] == ["1000", "2000", f"{1000 + MILS_PER_MM:.4f}".rstrip("0").rstrip("."),
                                             "2000", f"{0.2 * MILS_PER_MM:.4f}".rstrip("0").rstrip("."), "TopLayer"]
    assert any(",MidLayer2," in r for r in rows.tracks)   # L3 is legacy 3
    assert any(",MidLayer1,GND" in r for r in rows.tracks)
    v = rows.vias[0].split(",")
    assert v[4:] == ["TopLayer", "BottomLayer", "SIG1"] and float(v[2]) == pytest.approx(0.4 * MILS_PER_MM, abs=1e-3)
    only = ses_rows(ses, layer_map, rules, (0.0, 0.0), only_nets={"SIG2"})
    assert len(only.tracks) == 1 and only.vias == [] and only.other_nets_dropped == 5


def test_layer_tokens():
    assert [layer_token(i) for i in (1, 2, 5, 32)] == ["TopLayer", "MidLayer1", "MidLayer4", "BottomLayer"]


# ---------------------------------------------------------------- Freerouting discovery

def _fake_jar(path: Path, class_major: int):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nMain-Class: app.freerouting.Freerouting\n")
        z.writestr("app/freerouting/Freerouting.class", b"\xca\xfe\xba\xbe\x00\x00" + class_major.to_bytes(2, "big"))


def test_jar_java_version_is_checked(tmp_path, monkeypatch):
    jar = tmp_path / "freerouting-9.9.jar"
    _fake_jar(jar, 69)
    assert fr.jar_java_needed(jar) == 25
    java = tmp_path / "java"
    java.write_text("#!/bin/sh\necho 'openjdk version \"21.0.2\" 2024-01-16' 1>&2\n")
    java.chmod(0o755)
    monkeypatch.setenv("ALTIUM_AGENT_FREEROUTING", str(jar))
    monkeypatch.setenv("ALTIUM_AGENT_JAVA", str(java))
    f = fr.find()
    assert f.kind == "jar" and f.java_major == 21 and not f.usable
    assert "needs Java 25" in f.problems[0]
    with pytest.raises(fr.FreeroutingError):
        fr.run(tmp_path / "a.dsn", tmp_path / "a.ses", fr=f)


def test_not_installed_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("ALTIUM_AGENT_FREEROUTING", raising=False)
    monkeypatch.delenv("ALTIUM_AGENT_FREEROUTING_JAR", raising=False)
    monkeypatch.setattr(fr, "search_dirs", lambda: [tmp_path / "none"])
    monkeypatch.setattr(fr.shutil, "which", lambda name: None)
    assert fr.find() is None
    with pytest.raises(fr.FreeroutingError) as e:
        fr.run(tmp_path / "a.dsn", tmp_path / "a.ses")
    assert "not shipped" in e.value.hint


def test_run_flags_keep_it_headless_and_private(tmp_path):
    script = tmp_path / "freerouting"
    script.write_text("#!/bin/sh\necho \"$@\" > \"$(dirname \"$0\")/args\"\n"
                      "while [ $# -gt 0 ]; do if [ \"$1\" = -do ]; then echo '(session x)' > \"$2\"; fi; shift; done\n")
    script.chmod(0o755)
    f = fr.Freerouting([str(script)], "native", str(script))
    r = fr.run(tmp_path / "a.dsn", tmp_path / "a.ses", fr=f, max_passes=7)
    args = (tmp_path / "args").read_text()
    for flag in ("--gui.enabled=false", "--usage_and_diagnostic_data.disable_analytics=true",
                 "--router.automatic_neckdown=false", "--router.optimizer.enabled=false", "-mp 7"):
        assert flag in args
    assert Path(r["ses"]).is_file()


# ---------------------------------------------------------------- broker

@needs_kame
def test_kame_fully_routed_needs_nothing():
    p = plan(read_pcbdoc(Path(KAME_PCB)), freerouting_ready=True)
    assert p["steps"] == [] and "Nothing to route" in p["notes"][0]


@needs_kame
def test_kame_stripped_plan_tiers_and_protection():
    doc = _stripped(read_pcbdoc(Path(KAME_PCB)), keep={"FMU_VBAT"})
    p = plan(doc, freerouting_ready=True, batch_size=40)
    assert p["pairs"] == [{"name": "USB", "positive": "USB_P", "negative": "USB_N"}]
    assert p["protected"]["partial"] == ["FMU_VBAT"]
    assert set(p["tiers"]["poured"]) >= {"GND"}
    tools = [s["tool"] for s in p["steps"]]
    assert tools[:3] == ["app_checkpoint", "pcb_lock_net_routing", "pcb_autoroute"]
    assert p["steps"][2]["args"]["nets"] == ["USB_N", "USB_P"] and p["steps"][2]["args"]["diff_pairs"]
    assert "route_freerouting" in tools and tools[-3:] == ["pcb_repour_polygons", "pcb_run_drc", "route_converge"]
    fr_steps = [s for s in p["steps"] if s["tool"] == "route_freerouting"]
    assert all(len(s["args"]["route_only"]) <= 40 for s in fr_steps)
    routed = set(p["routed_by_plan"])
    assert "GND" not in routed and routed.isdisjoint(p["tiers"]["poured"])
    p2 = plan(doc, freerouting_ready=False)
    assert "route_freerouting" not in [s["tool"] for s in p2["steps"]]


@needs_wifi
def test_wifi_defined_pair():
    assert pair_nets(read_pcbdoc(Path(WIFI_PCB))) == [("PA12_USB_D", "PA12_USB_D_P", "PA12_USB_D_N")]


@needs_kame
def test_classify_skips_nets_joined_by_pours():
    doc = _stripped(read_pcbdoc(Path(KAME_PCB)))
    c = classify(doc)
    every = [n for v in c["tiers"].values() for n in v]
    assert len(every) == len(set(every))


# ---------------------------------------------------------------- converge

def _v(n1, n2):
    return {"name": "Clearance Constraint", "rule": "Clearance",
            "description": "Clearance Constraint: (0.08mm < 0.1mm) Between Track on Top Layer And Track on Top Layer",
            "x_mils": 1, "y_mils": 2, "primitive1": {"type": "Track", "net": n1, "x_mils": 1, "y_mils": 2},
            "primitive2": {"type": "Track", "net": n2, "x_mils": 3, "y_mils": 4}}


PLAN = {"source": "C:/b.PcbDoc", "freerouting": True, "routed_by_plan": ["A", "B", "C"],
        "protected": {"routed": ["H"], "partial": ["C"]},
        "steps": [{"engine": "freerouting", "args": {"route_only": ["A", "B"]}},
                  {"engine": "inhouse", "args": {"nets": ["C"]}}]}


def test_converge_never_rips_protected_and_switches_engine(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(PLAN))
    r = converge(PLAN, {"violations": [_v("A", "H"), _v("A", "B"), _v("C", "H"), _v("H", "X")]}, plan_path=path)
    unroute = next(s for s in r["steps"] if s["tool"] == "pcb_unroute")
    assert set(unroute["args"]["nets"]) <= {"A", "B", "C"} and unroute["args"]["include_locked"] is False
    assert "H" not in unroute["args"]["nets"]
    assert any("only copper this plan did not route" in e for e in r["escalations"])
    eng = {s["engine"]: (s["args"].get("nets") or s["args"].get("route_only")) for s in r["steps"] if s.get("engine")}
    assert "A" in eng.get("inhouse", []) and "C" in eng.get("freerouting", [])


def test_converge_bounds_rounds_and_repeat_rips(tmp_path):
    path = tmp_path / "p.json"
    for _ in range(2):
        converge(PLAN, {"violations": [_v("A", "B")]}, plan_path=path, max_rounds=5)
    r = converge(PLAN, {"violations": [_v("A", "B")]}, plan_path=path, max_rounds=5)
    assert any("ripped up and re-routed 2 times" in e for e in r["escalations"])
    r = converge(PLAN, {"violations": [_v("A", "B")]}, plan_path=path, max_rounds=3)
    assert r["done"] and not r["converged"]
    assert converge(PLAN, {"violations": []})["converged"] is True


# ---------------------------------------------------------------- vias

@needs_kame
def test_kame_tenting_is_read_from_the_file():
    doc = read_pcbdoc(Path(KAME_PCB))
    free = [v for v in doc.vias if v.component == NONE16]
    assert len(free) == 375 and all(v.tent_top and v.tent_bottom for v in free)
    assert via_check(doc)["matches"] is True
    assert read_back(doc, net="GND", top=True)["verified"] is True
    assert read_back(doc, net="GND", top=False)["verified"] is False


@needs_kame
@needs_wifi
def test_wifi_vias_against_kame_style():
    from altium_agent.reference.profile import profile_board

    prof = profile_board(read_pcbdoc(Path(KAME_PCB)))
    assert prof["routing"]["vias"]["tenting"] == {"top_share": 1.0, "bottom_share": 1.0}
    r = via_check(read_pcbdoc(Path(WIFI_PCB)), prof)
    assert [f["code"] for f in r["findings"]] == ["via_size_outside_style"]


# ---------------------------------------------------------------- live tools refuse

class _Bridge:
    def __init__(self, file):
        self.file, self.sent = file, []

    async def send_command_async(self, cmd, params, timeout=None):
        self.sent.append(cmd)
        if cmd == "pcb.get_layout_model":
            return {"file": self.file, "origin": [0, 0]}
        return {"vias_matched": 3, "tented_top_before": 3}


def _tools():
    from altium_agent.tools.registry import ToolRegistry
    from altium_agent.tools.routing_broker import register_routing_broker_tools

    reg = ToolRegistry()
    register_routing_broker_tools(reg)
    return reg


def test_tenting_and_import_refuse_samples_and_the_wrong_board(tmp_path, monkeypatch):
    import altium_agent.bridge as bridge_mod

    monkeypatch.setenv("PUBLIC", str(tmp_path))
    br = _Bridge("C:/other/b.PcbDoc")
    monkeypatch.setattr(bridge_mod, "get_bridge", lambda: br)
    t = _tools()
    sample = str(tmp_path / "Documents" / "Altium" / "x" / "b.PcbDoc")
    r = asyncio.run(t.get("route_set_via_tenting").fn(expect_file=sample, top=True, dry_run=False))
    assert "sample or installed" in r["refused"]
    r = asyncio.run(t.get("route_set_via_tenting").fn(expect_file="C:/mine/b.PcbDoc", top=True, dry_run=False))
    assert "focused board" in r["refused"] and "pcb.set_via_tenting" not in br.sent
    r = asyncio.run(t.get("route_import_ses").fn(job_id="job-x", expect_file=sample))
    assert "sample or installed" in r["refused"]
    r = asyncio.run(t.get("route_freerouting").fn(expect_file=sample))
    assert "sample or installed" in r["refused"]


def test_tenting_dry_run_sends_dry_run(monkeypatch):
    import altium_agent.bridge as bridge_mod

    br = _Bridge("C:/mine/b.PcbDoc")
    sent = {}

    async def send(cmd, params, timeout=None):
        sent[cmd] = params
        return await _Bridge.send_command_async(br, cmd, params)

    br.send_command_async = send
    monkeypatch.setattr(bridge_mod, "get_bridge", lambda: br)
    r = asyncio.run(_tools().get("route_set_via_tenting").fn(expect_file="C:/mine/b.PcbDoc", top=True,
                                                             diameter_mm=0.4))
    assert r["ok"] and sent["pcb.set_via_tenting"]["dry_run"] == "true"
    assert float(sent["pcb.set_via_tenting"]["diameter_mils"]) == pytest.approx(0.4 * MILS_PER_MM, abs=1e-3)


# ---------------------------------------------------------------- DSN and a real Freerouting run

@needs_kame
def test_dsn_route_only_puts_the_rest_in_the_fixed_class():
    doc = read_pcbdoc(Path(KAME_PCB))
    e = export_dsn(doc, DsnOptions(route_only=["USB_P", "USB_N"]))
    assert "(class AGENT_FIXED" in e.text and '(host_cad "Altium Agent")' in e.text


REAL_FR = fr.find() if os.environ.get("ALTIUM_AGENT_FREEROUTING") else None


@needs_kame
@pytest.mark.skipif(not (REAL_FR and REAL_FR.usable), reason="no usable Freerouting (set ALTIUM_AGENT_FREEROUTING)")
def test_real_freerouting_batch_on_kame(tmp_path):
    """Take up a handful of Kame's nets and let Freerouting put them back."""
    nets = ["UART7_TX_MB", "UART7_RX_MB", "SPWR_ON"]
    doc = copy.deepcopy(read_pcbdoc(Path(KAME_PCB)))
    gone = {i for i, n in enumerate(doc.nets) if n in nets}
    doc.tracks = [t for t in doc.tracks if t.net not in gone or t.component != NONE16]
    doc.arcs = [a for a in doc.arcs if a.net not in gone or a.component != NONE16]
    doc.vias = [v for v in doc.vias if v.net not in gone or v.component != NONE16]
    e = export_dsn(doc, DsnOptions(route_only=nets))
    dsn, ses = tmp_path / "k.dsn", tmp_path / "k.ses"
    dsn.write_text(e.text)
    fr.run(dsn, ses, max_passes=10, timeout_s=900,
           extra=("--router.fanout.enabled=false", "--router.autorouter.ignore_net_classes=AGENT_FIXED"))
    rows = ses_rows(ses, e.layer_map, e.rules, doc.origin_mil, existing=doc, only_nets=set(nets))
    assert rows.tracks and rows.nets <= set(nets)
