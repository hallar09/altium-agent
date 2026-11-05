# SPDX-License-Identifier: Apache-2.0
# Altium Agent (2026): see docs/PROVENANCE.md
"""The rule, stack-up and via additions of 2026-11.

Each one closes a gap an agent hit while building a board (SODIMM,
MiniPC, MacroStick): a pair rule whose width could not be set, a 50 mil
default via the router kept using, a dielectric read back one off, a
stack that could not be built layer by layer. These pin what was added:
the Pascal arms use only identifiers already proven on AD26, the Python
tools send what the handlers read, and the pure helpers answer right.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from altium_agent.analysis.rule_audit import audit_rules
from altium_agent.offline.files import stack_params as sp
from altium_agent.offline.files.pcbdoc import DIELTYPE_NAMES, _stack
from altium_agent.tools import pcb as pcb_mod
from altium_agent.tools.registry import CORE_TOOLS, ToolRegistry

from tests.pascal_source import load

_PCB_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"
from altium_agent.units import MM_PER_MIL as MIL

#: The rule identifiers the Kame FMU PcbAutomation.pas sample used on this
#: AD26 and logged OK. A new arm may use these and nothing else: an
#: undeclared identifier halts the polling loop where Try cannot catch it.
_PROVEN = {
    "routing_via": ("eRule_RoutingViaStyle", ("MaxHoleWidth", "MinHoleWidth", "PreferedHoleWidth",
                                              "MaxWidth", "MinWidth", "PreferedWidth")),
    "polygon_connect": ("eRule_PolygonConnectStyle", ("ConnectStyle", "ReliefConductorWidth",
                                                      "ReliefAirGap", "ReliefEntries")),
    "plane_connect": ("eRule_PowerPlaneConnectStyle", ("PlaneConnectStyle", "ReliefConductorWidth",
                                                       "ReliefAirGap", "ReliefExpansion", "ReliefEntries")),
    "plane_clearance": ("eRule_PowerPlaneClearance", ("Clearance",)),
    "annular_ring": ("eRule_MinimumAnnularRing", ("Minimum",)),
    "solder_mask_sliver": ("eRule_MinimumSolderMaskSliver", ("MinSolderMaskSliver",)),
}


class _Recorder:
    def __init__(self, reply=None):
        self.sent = []
        self.reply = reply or {"created": True}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params or {}))
        return self.reply(command) if callable(self.reply) else self.reply


@pytest.fixture()
def tools(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: recorder)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    fns = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    fns["_recorder"] = recorder
    return fns


@pytest.fixture(scope="module")
def handlers() -> dict:
    return load(_PCB_PAS, minimum=100)


def _arm(handler: str, rule_type: str) -> str:
    guard = f"RuleTypeStr = '{rule_type}' Then"
    assert guard in handler, f"{rule_type} is not dispatched"
    rest = handler.split(guard, 1)[1]
    end = rest.find(chr(10) + "        Else If RuleTypeStr")
    if end < 0:
        end = rest.find(chr(10) + "        Else" + chr(13))
    return rest[:end] if end > 0 else rest


# --------------------------------------------------------------- Pascal arms

@pytest.mark.parametrize("rule_type", sorted(_PROVEN))
def test_each_new_kind_uses_only_proven_identifiers(handlers, rule_type):
    enum, props = _PROVEN[rule_type]
    arm = _arm(handlers["PCB_CreateDesignRule"], rule_type)
    assert f"PCBRuleFactory({enum})" in arm
    written = {seg.split(":=")[0].strip().split(".")[-1]
               for seg in arm.split(chr(10)) if ":=" in seg and "RuleAny." in seg}
    unknown = written - set(props) - {"Name", "Scope1Expression"}
    assert not unknown, f"{rule_type} writes unproven properties {sorted(unknown)}"


def test_pair_rule_writes_its_widths(handlers):
    arm = _arm(handlers["PCB_CreateDesignRule"], "differential_pairs")
    for prop in ("MinWidth(L)", "MaxWidth(L)", "PreferedWidth(L)", "MinGap(L)"):
        assert f"RuleDiff.{prop} :=" in arm


def test_set_rule_properties_edits_a_pair_rule_and_reads_it_back(handlers):
    h = handlers["PCB_SetRuleProperties"]
    assert "Kind <> eRule_DifferentialPairsRouting" in h
    for prop in ("MinWidth", "MaxWidth", "PreferedWidth", "MinGap", "MaxGap", "PreferedGap"):
        assert f"RuleDiffIter.{prop}(L) := CW" in h
        assert f"RuleDiffIter.{prop}(eTopLayer) = CW" in h
    assert "RuleDiffIter : IPCB_DifferentialPairsRoutingRule" in h


def test_import_layer_stack_is_dispatched_and_reads_the_file_safely(handlers):
    h = handlers["PCB_ImportLayerStack"]
    assert "MasterLayerStack.Import_FromParameters(Content)" in h
    assert "ReadFileContent(PathStr)" in h and "LoadFromFile" not in h
    assert "Export_ToParameters" not in h.split("Begin", 1)[1]
    src = _PCB_PAS.read_text(encoding="utf-8")
    assert "'import_layer_stack':      Result := PCB_ImportLayerStack(Params, RequestId);" in src


def test_modify_layer_keeps_fractional_mils(handlers):
    h = handlers["PCB_ModifyLayer"]
    assert "MilsToCoordF(StrToFloatDef(ThickStr, 0))" in h
    assert "MilsToCoordF(StrToFloatDef(HeightStr, 0))" in h
    assert "StrToIntDef(ThickStr" not in h and "StrToIntDef(HeightStr" not in h


# --------------------------------------------------------------- Python tools

def test_values_mm_are_sent_as_mm_params(tools):
    asyncio.run(tools["pcb_create_design_rule"](
        name="DP_DDR", rule_type="differential_pairs", scope="IsDifferentialPair",
        values_mm={"min_width": 0.1, "pref_width": 0.127, "max_width": 0.15,
                   "min_gap": 0.1, "max_gap": 0.2, "pref_gap": 0.127}))
    cmd, params = tools["_recorder"].sent[-1]
    assert cmd == "pcb.create_design_rule"
    assert params["mm_pref_width"] == "0.127" and params["mm_min_gap"] == "0.1"


@pytest.mark.parametrize("kwargs, words", [
    ({"rule_type": "width", "values_mm": {"via_min": 0.4}}, "do not apply"),
    ({"rule_type": "routing_via"}, "values_mm"),
    ({"rule_type": "routing_via", "values_mm": {"via_min": 0.2, "hole_min": 0.2}}, "larger than"),
    ({"rule_type": "width", "values_mm": {"min_width": 0.2, "max_width": 0.1}}, "above"),
    ({"rule_type": "polygon_connect", "style": "spokes"}, "relief"),
    ({"rule_type": "clearance", "style": "direct"}, "applies to"),
    ({"rule_type": "max_via_count"}, "unknown rule_type"),
])
def test_bad_rule_calls_are_refused_before_sending(tools, kwargs, words):
    out = asyncio.run(tools["pcb_create_design_rule"](name="X", **kwargs))
    assert out["created"] is False and words in out["reason"]
    assert tools["_recorder"].sent == []


def test_scope2_and_style_reach_the_handler(tools):
    asyncio.run(tools["pcb_create_design_rule"](
        name="PolyGNDVia", rule_type="polygon_connect", style="direct",
        scope="IsPolygon", scope2="InNetClass('GND') And IsVia"))
    params = tools["_recorder"].sent[-1][1]
    assert params["scope2"] == "InNetClass('GND') And IsVia" and params["style"] == "direct"


def test_set_rule_properties_forwards_pair_values(tools):
    asyncio.run(tools["pcb_set_rule_properties"](
        name="DiffPairsRouting", dp_min_width_mm=0.1, dp_pref_width_mm=0.127, dp_max_gap_mm=0.2))
    params = tools["_recorder"].sent[-1][1]
    assert params["dp_min_width_mm"] == "0.1" and params["dp_pref_width_mm"] == "0.127"


def test_a_pair_value_that_did_not_take_fails_the_call(monkeypatch):
    rec = _Recorder({"name": "DP", "diff_pair_written": False})
    monkeypatch.setattr(pcb_mod, "get_bridge", lambda: rec)
    registry = ToolRegistry()
    pcb_mod.register_pcb_tools(registry)
    fns = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    out = asyncio.run(fns["pcb_set_rule_properties"](name="DP", dp_min_width_mm=0.1))
    assert out["ok"] is False


def test_modify_layer_takes_mm_and_fractions(tools):
    asyncio.run(tools["pcb_modify_layer"](layer="MidLayer1", copper_thickness_mm=0.0152,
                                          dielectric_height_mils=2.95))
    params = tools["_recorder"].sent[-1][1]
    assert abs(float(params["copper_thickness_mils"]) - 0.0152 / MIL) < 1e-3
    assert params["dielectric_height_mils"] == "2.9500"


def test_run_drc_focuses_the_board_first(tools):
    asyncio.run(tools["pcb_run_drc"](allow_modal=True, board_path=r"C:\b\x.PcbDoc"))
    sent = [c for c, _p in tools["_recorder"].sent]
    assert sent == ["pcb.focus_board", "pcb.run_drc"]


def test_new_tools_are_in_core():
    for name in ("pcb_import_layer_stack", "pcb_audit_rules", "pcb_set_rule_properties",
                 "pcb_delete_design_rule", "pcb_set_rules_enabled", "pcb_place_vias"):
        assert name in CORE_TOOLS


# --------------------------------------------------------------- stack import

def _saved_stack() -> str:
    ctx = "V9_STACK_LAYER{n}_{{AAAA}}CONTEXT=0|V9_STACK_LAYER{n}_{{AAAA}}USEDBYPRIMS=TRUE"
    rows = [
        ("Top Solder", 16973834, "|V9_STACK_LAYER{n}_DIELTYPE=3|V9_STACK_LAYER{n}_DIELHEIGHT=0.4mil"),
        ("Top Layer", sp.TOP_ID, "|V9_STACK_LAYER{n}_COPTHICK=1.4mil"),
        ("Dielectric 1", sp.DIEL1_ID, "|V9_STACK_LAYER{n}_DIELTYPE=1|V9_STACK_LAYER{n}_DIELHEIGHT=59mil"),
        ("Bottom Layer", sp.BOTTOM_ID, "|V9_STACK_LAYER{n}_COPTHICK=1.4mil"),
        ("Bottom Solder", 16973835, "|V9_STACK_LAYER{n}_DIELTYPE=3|V9_STACK_LAYER{n}_DIELHEIGHT=0.4mil"),
    ]
    items = ["V9_MASTERSTACK_ID={MMMM}"]
    for n, (name, lid, extra) in enumerate(rows):
        items.append(ctx.format(n=n) + f"|V9_STACK_LAYER{n}_NAME={name}|V9_STACK_LAYER{n}_LAYERID={lid}"
                     + extra.format(n=n))
    items.append("V9_CACHE_LAYER0_NAME=x")
    return "|".join(items)


_PLAN = [
    {"type": "copper", "name": "L1_Top", "thickness_mm": 0.035},
    {"type": "dielectric", "kind": "prepreg", "height_mm": 0.0762, "er": 3.9, "material": "1080"},
    {"type": "copper", "name": "L2_GND", "thickness_mm": 0.0152},
    {"type": "dielectric", "kind": "core", "height_mm": 1.0, "er": 4.4},
    {"type": "copper", "name": "L3_PWR", "thickness_mm": 0.0152},
    {"type": "dielectric", "kind": "prepreg", "height_mm": 0.0762, "er": 3.9},
    {"type": "copper", "name": "L4_Bottom", "thickness_mm": 0.035},
]


def test_stack_text_builds_named_layers_with_altiums_codes():
    text, names = sp.build_stack_params(_saved_stack(), _PLAN)
    assert names == ["Top Solder", "L1_Top", "Dielectric 1", "L2_GND", "Dielectric 2",
                     "L3_PWR", "Dielectric 3", "L4_Bottom", "Bottom Solder"]
    props = dict(item.split("=", 1) for item in text.split("|"))
    assert props["V9_STACK_LAYER2_DIELTYPE"] == "2"   # prepreg
    assert props["V9_STACK_LAYER4_DIELTYPE"] == "1"   # core
    assert props["V9_STACK_LAYER3_LAYERID"] == str(sp.MID1_ID)
    assert props["V9_STACK_LAYER5_LAYERID"] == str(sp.MID1_ID + 1)
    assert props["V9_STACK_LAYER3_COPTHICK"] == "0.5984mil"
    assert props["V9_STACK_LAYER1_NAME"] == "L1_Top"
    assert "V9_CACHE_LAYER0_NAME" in props and props["V9_MASTERSTACK_ID"] == "{MMMM}"
    # the new layers carry the substack context of Top, cleared
    assert props["V9_STACK_LAYER3_{AAAA}USEDBYPRIMS"] == "FALSE"


@pytest.mark.parametrize("bad, words", [
    (_PLAN[:2], "at least"),
    ([_PLAN[0], _PLAN[0], _PLAN[0]], "expected dielectric"),
    ([_PLAN[0], dict(_PLAN[1], kind="glass"), _PLAN[6]], "core or prepreg"),
    ([dict(_PLAN[0], thickness_mm=1.0), _PLAN[1], _PLAN[6]], "thickness_mm"),
])
def test_bad_plans_are_named(bad, words):
    assert any(words in p for p in sp.validate_plan(bad))


def test_layers_the_new_stack_drops_are_reported():
    saved = _saved_stack().replace(
        "V9_CACHE_LAYER0_NAME=x",
        "V9_STACK_LAYER9_NAME=GND Plane|V9_STACK_LAYER9_LAYERID=16842753|V9_CACHE_LAYER0_NAME=x")
    assert sp.removed_layers(saved, _PLAN) == ["GND Plane"]


def test_import_dry_run_touches_nothing(tools, monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "read_stack_params", lambda p: _saved_stack())
    out = asyncio.run(tools["pcb_import_layer_stack"](pcb_path="x.PcbDoc", layers=_PLAN))
    assert out["ok"] and out["dry_run"] and out["copper_layers"] == 4
    assert abs(out["board_thickness_mm"] - 1.2528) < 1e-6
    assert tools["_recorder"].sent == []


def test_import_sends_the_file(tools, monkeypatch, tmp_path):
    from altium_agent import config as cfg
    monkeypatch.setattr(sp, "read_stack_params", lambda p: _saved_stack())
    monkeypatch.setattr(cfg.get_config(), "workspace_dir", tmp_path, raising=False)
    tools["_recorder"].reply = {"imported": True, "chars": 10}
    out = asyncio.run(tools["pcb_import_layer_stack"](pcb_path="x.PcbDoc", layers=_PLAN,
                                                      dry_run=False))
    cmd, params = tools["_recorder"].sent[-1]
    assert cmd == "pcb.import_layer_stack" and out["imported"] is True
    assert "V9_STACK_LAYER" in Path(params["path"]).read_text(encoding="cp1252")


# --------------------------------------------------------------- dielectric codes

def test_dieltype_codes_are_altiums():
    """Kame_FMU, saved by Altium: Core-009 is 1, PP-006 is 2, Solder Resist 3."""
    assert DIELTYPE_NAMES == {"0": "none", "1": "core", "2": "prepreg", "3": "surface"}
    board = {
        "V9_STACK_LAYER0_NAME": "Dielectric 1", "V9_STACK_LAYER0_LAYERID": str(sp.DIEL1_ID),
        "V9_STACK_LAYER0_DIELTYPE": "1", "V9_STACK_LAYER0_DIELHEIGHT": "20mil",
        "V9_STACK_LAYER1_NAME": "Dielectric 2", "V9_STACK_LAYER1_LAYERID": str(sp.DIEL1_ID + 1),
        "V9_STACK_LAYER1_DIELTYPE": "2", "V9_STACK_LAYER1_DIELHEIGHT": "6mil",
    }
    kinds = [lyr.dielectric_type for lyr in _stack(board)]
    assert kinds == ["core", "prepreg"]


# --------------------------------------------------------------- rule audit

def _rule(name, kind, values=None, scope1="All", priority=1, enabled=True):
    return {"name": name, "kind": kind, "enabled": enabled, "priority": priority,
            "scope1": scope1, "scope2": "All", "values": values or {}}


def test_audit_names_the_defaults_that_fight_the_design():
    rules = [
        _rule("Width", "Width", {"minlimit_mm": 10 * MIL, "preferedwidth_mm": 10 * MIL,
                                 "maxlimit_mm": 10 * MIL}, priority=3),
        _rule("RoutingVias", "RoutingVias", {"width_mm": 1.27, "holewidth_mm": 0.7112}),
        _rule("DiffPairsRouting", "DiffPairsRouting",
              {"minlimit_mm": 0.254, "maxlimit_mm": 0.254, "dp_min_width_mm": 0.381,
               "dp_pref_width_mm": 0.381, "dp_max_width_mm": 0.381}, priority=2),
        _rule("MinimumSolderMaskSliver", "MinimumSolderMaskSliver", {"minsoldermaskwidth_mm": 0.254}),
        _rule("Width_DDR", "Width", {"minlimit_mm": 0.1, "maxlimit_mm": 0.2}, "InNetClass('DDR')"),
    ]
    out = audit_rules(rules, min_width_mm=0.1, via_diameter_mm=0.4, via_hole_mm=0.2,
                      mask_sliver_mm=0.075, diff_pair_width_mm=0.127)
    fails = {f["rule"]: f for f in out["findings"] if f["severity"] == "fail"}
    assert set(fails) == {"Width", "RoutingVias", "DiffPairsRouting", "MinimumSolderMaskSliver"}
    assert fails["Width"]["fix"]["arguments"]["min_width_mm"] == 0.1
    assert fails["DiffPairsRouting"]["fix"]["arguments"]["dp_pref_width_mm"] == 0.127
    assert fails["MinimumSolderMaskSliver"]["fix"] == {
        "tool": "pcb_set_rule_properties",
        "arguments": {"name": "MinimumSolderMaskSliver", "gap_mm": 0.075}}
    assert fails["RoutingVias"]["alternative"]["arguments"]["rule_type"] == "routing_via"
    assert out["ok"] is False


def test_audit_flags_a_pair_wider_than_every_width_rule():
    rules = [
        _rule("DP", "DiffPairsRouting", {"dp_min_width_mm": 0.381, "dp_pref_width_mm": 0.381,
                                         "dp_max_width_mm": 0.381}, "IsDifferentialPair"),
        _rule("Width_DDR", "Width", {"minlimit_mm": 0.1, "maxlimit_mm": 0.2032}, "InNetClass('DDR')"),
    ]
    out = audit_rules(rules)
    assert any(f["severity"] == "fail" and "wider than every class Width" in f["issue"]
               for f in out["findings"])


def test_audit_finds_shadowed_rules_and_disabled_signoff_rules():
    rules = [
        _rule("SME_2mil", "SolderMaskExpansion", {"expansion_mm": 0.05}, priority=1),
        _rule("SolderMaskExpansion", "SolderMaskExpansion", {"expansion_mm": 0.0}, priority=2),
        _rule("UnRoutedNet", "UnRoutedNet", enabled=False),
    ]
    routing = audit_rules(rules)
    shadow = [f for f in routing["findings"] if "never applies" in f["issue"]]
    assert shadow and shadow[0]["rule"] == "SolderMaskExpansion"
    assert [f["severity"] for f in routing["findings"] if f["rule"] == "UnRoutedNet"] == ["info"]
    signoff = audit_rules(rules, signoff=True)
    assert [f["severity"] for f in signoff["findings"] if f["rule"] == "UnRoutedNet"] == ["fail"]


def test_a_clean_board_passes():
    rules = [_rule("Width", "Width", {"minlimit_mm": 0.1, "preferedwidth_mm": 0.15, "maxlimit_mm": 1.0}),
             _rule("RoutingVias", "RoutingVias", {"width_mm": 0.4, "holewidth_mm": 0.2})]
    out = audit_rules(rules, min_width_mm=0.1, via_diameter_mm=0.4, via_hole_mm=0.2)
    assert out["ok"] and out["counts"]["fail"] == 0
