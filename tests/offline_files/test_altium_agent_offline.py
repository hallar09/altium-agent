# SPDX-License-Identifier: Apache-2.0
"""How Altium Agent hosts the ported offline file tools.

The ported modules are tested by the files next to this one. These tests
hold what Altium Agent adds: every ported tool has a naming decision, the
published ones are honest offline/read-only tools, fabrication tools stay
out, writes never land beside a design or in Altium's folders, and the
readers agree with Altium's own example boards.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from altium_agent.offline import context
from altium_agent.offline.errors import InvalidArgument, PathNotAllowed
from altium_agent.tools.metadata import TOOL_PREFIXES, interaction_of, maturity_of
from altium_agent.tools.offline import RENAMES, offline_tool_names, register_offline_tools
from altium_agent.tools.offline.publish import _captured, _publishable
from altium_agent.tools.registry import ToolRegistry

from .conftest import KAME, SPIRIT, WIFI, requires_examples, requires_kame, requires_wifi


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_offline_tools(reg)
    return reg


def _call(reg: ToolRegistry, name: str, **kwargs):
    spec = next(t for t in asyncio.run(reg.list_tools()) if t.name == name)
    return asyncio.run(spec.fn(**kwargs))


# --- naming and badges -----------------------------------------------------------

def test_every_ported_tool_has_a_naming_decision():
    captured = {t.name for t in _captured()}
    assert captured <= set(RENAMES), sorted(captured - set(RENAMES))


def test_published_names_use_the_altium_agent_groups():
    for name in offline_tool_names():
        assert name.startswith(TOOL_PREFIXES), name


def test_published_tools_are_offline_and_read_only():
    for name in offline_tool_names():
        assert maturity_of(name) == "offline", name
        assert interaction_of(name) == "readonly", name


def test_fabrication_tools_are_not_published():
    published = {t.name for t in asyncio.run(_registry().list_tools())}
    assert RENAMES["outjob_run"] is None and RENAMES["release_package"] is None
    assert not any("release" in n or "outjob_run" in n for n in published)
    assert published == set(offline_tool_names())


def test_no_published_tool_is_named_like_a_live_get():
    """Reading a file is ``read``; ``get`` stays the live bridge verb."""
    for name in offline_tool_names():
        assert not name.split("_", 1)[1].startswith("get_"), name


def test_docstrings_name_altium_agent_tools_only():
    for spec in asyncio.run(_registry().list_tools()):
        doc = spec.description or ""
        for old in ("live_compile", "live_pcb_drc", "sch_pcb_consistency", "design_review"):
            assert old not in doc, (spec.name, old)


# --- the publish wrapper ---------------------------------------------------------

def test_typed_errors_become_ok_false():
    def boom(project=None):
        raise InvalidArgument("nope", hint="pass a project")

    out = asyncio.run(_publishable(boom, "analyze_boom")())
    assert out == {"ok": False, "error": "invalid_argument", "message": "nope", "hint": "pass a project"}


def test_a_dict_reply_says_ok():
    out = asyncio.run(_publishable(lambda: {"x": 1}, "analyze_x")())
    assert out == {"ok": True, "x": 1}


def test_the_wrapper_keeps_the_signature():
    def f(project: str | None = None, width_mm: float = 1.0) -> dict:
        return {}

    assert list(inspect.signature(_publishable(f, "analyze_f")).parameters) == ["project", "width_mm"]


# --- read anywhere, write only to the output folder -------------------------------

def test_reading_is_allowed_anywhere(tmp_path):
    f = tmp_path / "Board.PcbDoc"
    f.write_bytes(b"")
    assert context.safe_path(str(f), suffixes=(".pcbdoc",)) == f.resolve()


def test_a_project_folder_resolves_to_its_one_prjpcb(tmp_path):
    (tmp_path / "A.PrjPcb").write_text("[Design]\n")
    assert context.resolve_project(str(tmp_path)).name == "A.PrjPcb"


def test_writes_go_to_the_output_folder_not_beside_the_design(tmp_path):
    design = tmp_path / "design" / "Board.PrjPcb"
    out = context.output_dir_for(design, "bom")
    assert design.parent not in out.parents
    assert out.parts[-2:] == ("Board", "bom")


def test_writes_into_altiums_folders_are_refused(tmp_path, monkeypatch):
    public = tmp_path / "Public"
    monkeypatch.setenv("PUBLIC", str(public))
    monkeypatch.setenv("ALTIUM_AGENT_OUTPUT_DIR", str(public / "Documents" / "Altium" / "AD26" / "Examples"))
    with pytest.raises(PathNotAllowed):
        context.output_dir_for(Path("x.PrjPcb"), "bom")


def test_gate_verdicts_never_claim_readiness():
    from altium_agent.tools.offline.quality import OFFLINE_GATE_SCOPE

    assert "not that the design is manufacturing-ready" in OFFLINE_GATE_SCOPE


# --- the stack-up layer name is matched loosely ----------------------------------

def test_board_layer_names_match_without_spaces_or_case():
    from altium_agent.analysis import si
    from altium_agent.offline.files.pcbdoc import PcbDocument, StackLayer

    stack = [StackLayer(0, "TopLayer", 0x01000001, "copper", copper_thickness_mm=0.035),
             StackLayer(1, "PP", 0, "dielectric", dielectric_height_mm=0.2, dielectric_constant=4.2),
             StackLayer(2, "BottomLayer", 0x01000002, "copper", copper_thickness_mm=0.035)]
    doc = PcbDocument(Path("x.PcbDoc"), {}, (0, 0), [], stack, [], [], [], [], [], [])
    assert si.board_geometry(doc, "Top Layer") == si.board_geometry(doc, "toplayer")


# --- against Altium's own boards ---------------------------------------------------

@requires_kame
def test_kame_schematic_and_board_agree_net_for_net():
    from altium_agent.analysis.model import load_model

    model = load_model(KAME)
    sch = {frozenset(f"{m.designator}-{m.pin}" for m in n.members) for n in model.netlist().nets}
    pcb = {frozenset(v) for v in model.pcb().pad_netlist().values()}
    assert len(pcb) == 158 and sch == pcb


@requires_kame
def test_kame_through_the_published_tools():
    reg = _registry()
    sync = _call(reg, "analyze_sch_pcb_sync", project=str(KAME))
    assert sync["ok"] and sync["in_sync"]
    summary = _call(reg, "pcb_read_summary", project=str(KAME))
    assert summary["copper_layer_count"] == 6 and summary["components"] == 102


@requires_wifi
def test_an_alternate_footprint_model_is_named_as_such():
    from altium_agent.analysis.consistency import compare
    from altium_agent.analysis.model import load_model

    model = load_model(WIFI)
    mism = {m["designator"]: m for m in compare(model, model.pcb())["footprint_mismatches"]}
    assert mism["R9"]["pcb_is_an_alternate_model"] is True


@requires_examples
def test_board_impedance_accepts_the_display_layer_name():
    out = _call(_registry(), "analyze_si_board_impedance", layer="Top Layer", width_mm=0.25,
                project=str(SPIRIT))
    assert out["ok"] and out["z0_ohm"] > 0
