# SPDX-License-Identifier: Apache-2.0
"""Phase (e): renders from the saved files, gate reviews, stage checkpoints
with undo, and design_status.

The journal tests are synthetic; the render, gate and status tests use a
copy of the Kame_FMU sample when ALTIUM_AGENT_KAME points at it. PNGs need a
Chromium-family browser (Edge on Windows); without one the SVG is still
checked.
"""

from __future__ import annotations

import asyncio
import copy
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from altium_agent.design.session import STAGES, SessionStore

KAME = os.environ.get("ALTIUM_AGENT_KAME", "")
needs_kame = pytest.mark.skipif(not (KAME and Path(KAME).is_file()), reason="Kame_FMU sample not available")


# ---------------------------------------------------------------- journal rewind

def test_rewind_reopens_the_stage_and_every_later_one(tmp_path):
    j = SessionStore(tmp_path).start("r", session_id="s1")
    for st in STAGES[:9]:
        j.enter_stage(st)
        j.stage_result(st, "ok")
    assert j.state().next_stage == STAGES[9]
    j.blocked("which plug?")
    j.rewind("placement", checkpoint_id="cp")
    s = j.state()
    assert s.next_stage == "placement" and s.current_stage == "placement"
    assert "placement" not in s.stage_status and "routing" not in s.stage_status
    assert s.stage_status["sch_to_pcb"] == "ok" and s.open_question is None
    with pytest.raises(ValueError):
        j.rewind("nonsense")


# ---------------------------------------------------------------- helpers on a Kame copy

@pytest.fixture
def kame_copy(tmp_path, monkeypatch):
    src = Path(KAME).parent
    dst = tmp_path / "proj"
    dst.mkdir()
    for f in src.iterdir():
        if f.suffix.lower() in (".prjpcb", ".pcbdoc", ".schdoc"):
            shutil.copy2(f, dst / f.name)
    monkeypatch.setenv("ALTIUM_AGENT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("ALTIUM_AGENT_OUTPUT_DIR", str(tmp_path / "out"))
    return dst / Path(KAME).name


def _tools():
    from altium_agent.tools import register_backend
    from altium_agent.tools.registry import ToolRegistry

    r = ToolRegistry()
    register_backend(r)
    return r


def _call(reg, name, **kw):
    return asyncio.run(reg.get(name).fn(**kw))


# ---------------------------------------------------------------- renders

@needs_kame
def test_board_geometry_and_svg_with_review_overlay():
    from altium_agent.offline.files.pcbdoc import NONE16, read_pcbdoc
    from altium_agent.render import PcbRenderOptions, render_pcb_svg
    from altium_agent.render.file_geometry import pcb_geometry, ratsnest_lines

    doc = read_pcbdoc(Path(KAME).with_suffix(".PcbDoc"))
    g = pcb_geometry(doc)
    assert len(g["tracks"]) == len(doc.tracks) and len(g["pads"]) == len(doc.pads)
    assert any(s.get("kind") == "arc" for s in g["outline"])          # Kame's rounded corners
    assert g["bbox"]["width"] == pytest.approx(31 / 0.0254, abs=1)
    stripped = copy.deepcopy(doc)
    gone = {i for i, n in enumerate(stripped.nets) if n in ("UART7_TX_MB", "SPWR_ON")}
    stripped.tracks = [t for t in stripped.tracks if t.net not in gone or t.component != NONE16]
    stripped.vias = [v for v in stripped.vias if v.net not in gone]
    lines = ratsnest_lines(stripped)
    assert lines and {ln["net"] for ln in lines} <= {"UART7_TX_MB", "SPWR_ON"}
    assert ratsnest_lines(doc) == []
    g["review"] = {"lines": lines, "markers": [{"x": 0, "y": 0, "label": "1", "severity": "error", "side": "top"},
                                               {"x": 10, "y": 10, "label": "2", "side": "bottom"}]}
    for side in ("top", "bottom"):
        svg = render_pcb_svg(g, PcbRenderOptions(interactive_legend=False, view_side=side))
        ET.fromstring(svg)
        assert 'class="review"' in svg and svg.count('class="ratsnest"') == len(lines)
        layer = svg[svg.index('class="review"'):]
        assert layer.count("<circle") == 1                               # each marker only on its side
        assert (">1</text>" in layer) == (side == "top") and (">2</text>" in layer) == (side == "bottom")


@needs_kame
def test_schematic_geometry_renders_every_sheet():
    from altium_agent.analysis.model import load_model
    from altium_agent.render import SchRenderOptions, render_sch_svg
    from altium_agent.render.file_geometry import sch_geometry

    m = load_model(Path(KAME))
    cpu = next(s for s in m.sheets if s.path.stem == "4_cpu")
    g = sch_geometry(cpu)
    assert len(g["components"]) == 37 and g["pins"] and g["wires"] and g["power_ports"]
    ET.fromstring(render_sch_svg(g, SchRenderOptions()))


# ---------------------------------------------------------------- gate review, undo, status

@needs_kame
def test_gate_review_marks_findings_and_journals_pictures(kame_copy):
    r = _tools()
    sid = _call(r, "design_session_start", requirement="t", project=str(kame_copy))["session_id"]
    g = _call(r, "design_gate_review", gate="placement")
    assert g["ok"] and g["session_id"] == sid and g["rubric"]
    assert [i["view"] for i in g["images"]] == ["board top", "board bottom"]
    assert all(Path(i["svg"]).is_file() for i in g["images"])
    assert g["numbers"]["placement"]["error"] >= 1                     # Kame's own locked overlaps
    assert {f["label"] for f in g["findings"]} >= {"1"}
    s = _call(r, "design_gate_review", gate="schematic", sheets=["4_cpu"])
    assert [i["view"] for i in s["images"]] == ["sheet 4_cpu.SchDoc"] and s["numbers"]["erc"]["errors"] == 0
    st = _call(r, "design_session_status")["state"]
    assert sum(a["kind"].startswith("render:") for a in st["artifacts"]) == 3
    bad = _call(r, "design_gate_review", gate="nonsense")
    assert bad["ok"] is False


@needs_kame
def test_stage_checkpoint_and_one_call_undo(kame_copy):
    r = _tools()
    _call(r, "design_session_start", requirement="t", project=str(kame_copy))
    e = _call(r, "design_session_log", event="stage_enter", stage="placement")
    assert e["stage_checkpoint"]["checkpoint"]["id"]
    pcb = kame_copy.with_suffix(".PcbDoc")
    before = pcb.read_bytes()
    pcb.write_bytes(before[:2048])
    extra = kame_copy.parent / "made_later.txt"
    extra.write_text("x")
    preview = _call(r, "design_undo_to_stage", stage="placement")
    assert preview["would"]["restore_checkpoint"] == e["stage_checkpoint"]["checkpoint"]["id"]
    assert pcb.read_bytes() != before                                   # nothing changed yet
    assert _call(r, "design_undo_to_stage", stage="routing", confirm=True, altium_closed=True)["ok"] is False
    u = _call(r, "design_undo_to_stage", stage="placement", confirm=True, altium_closed=True)
    assert u["ok"] and pcb.read_bytes() == before and not extra.exists()
    assert u["state"]["current_stage"] == "placement"
    assert u["safety_checkpoint"]["id"]


def test_undo_refuses_samples(tmp_path, monkeypatch):
    monkeypatch.setenv("ALTIUM_AGENT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("PUBLIC", str(tmp_path / "pub"))
    sample = tmp_path / "pub" / "Documents" / "Altium" / "S" / "S.PrjPcb"
    sample.parent.mkdir(parents=True)
    sample.write_text("[Design]\n")
    r = _tools()
    _call(r, "design_session_start", requirement="t", project=str(sample))
    out = _call(r, "design_undo_to_stage", stage="placement", confirm=True, altium_closed=True)
    assert "never edited" in out["refused"]


@needs_kame
def test_design_status_in_one_call(kame_copy):
    r = _tools()
    _call(r, "design_session_start", requirement="t", project=str(kame_copy))
    s = _call(r, "design_status", dfm_profile="jlcpcb_multilayer")
    assert s["ok"] and s["project"] == str(kame_copy)
    assert s["erc"]["errors"] == 0 and s["board"]["unrouted_connections"] == 0
    assert s["drc"]["report"] is None and s["dfm"]["profile"] == "jlcpcb_multilayer"
    assert s["next_action"]["stage"] == "requirement"
    assert "manufacture" in s["note"]
