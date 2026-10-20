# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tests/test_analysis.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
from __future__ import annotations

from pathlib import Path

import pytest

from altium_agent.analysis import si
from altium_agent.analysis.bom import build_bom, bom_to_csv
from altium_agent.analysis.dfm import PROFILES, run_dfm
from altium_agent.analysis.model import DesignModel
from altium_agent.offline.files.pcbdoc import PcbDocument, StackLayer, Track, Via
from altium_agent.offline.files.prjpcb import AltiumProject, ProjectVariant, VariantChange
from altium_agent.offline.files.schdoc import SchComponent, SchDocument, SchPin


# ---------------------------------------------------------------- signal integrity

def test_microstrip_reference_values():
    # 0.3 mm on 0.2 mm FR-4 (er 4.3): field solvers give ~52-56 ohm.
    z = si.impedance("microstrip", 0.3, 0.2, 4.3)["z0_ohm"]
    assert 50 < z < 58


def test_stripline_and_solver_roundtrip():
    res = si.solve_width(50, "stripline", 0.2, 4.2)
    assert abs(res["z0_ohm"] - 50) < 0.2
    again = si.impedance("stripline", res["solved_width_mm"], 0.2, 4.2)
    assert abs(again["z0_ohm"] - 50) < 0.2


def test_differential_lower_than_twice_single_ended():
    r = si.impedance("microstrip", 0.15, 0.1, 4.2, spacing_mm=0.15)
    assert r["zdiff_ohm"] < 2 * r["z0_ohm"]


def test_ipc2221_inverse():
    w = si.ipc2221_width_for_current(3.0)["width_mm"]
    assert abs(si.ipc2221_current(w)["current_a"] - 3.0) < 0.01


def test_board_geometry_from_stack():
    stack = [StackLayer(0, "Top", 0x01000001, "copper", copper_thickness_mm=0.035),
             StackLayer(1, "PP", 0, "dielectric", dielectric_height_mm=0.2, dielectric_constant=4.2),
             StackLayer(2, "GND", 0x01000002, "copper", copper_thickness_mm=0.035),
             StackLayer(3, "Core", 0, "dielectric", dielectric_height_mm=1.0, dielectric_constant=4.6),
             StackLayer(4, "Bottom", 0x01000003, "copper", copper_thickness_mm=0.035)]
    doc = PcbDocument(Path("x.PcbDoc"), {}, (0, 0), [], stack, [], [], [], [], [], [])
    assert si.board_geometry(doc, "Top") == {"geometry": "microstrip", "height_mm": 0.2, "er": 4.2, "thickness_mm": 0.035}
    mid = si.board_geometry(doc, "GND")
    assert mid["geometry"] == "stripline" and mid["height_mm"] == 0.2 and mid["height2_mm"] == 1.0


# ---------------------------------------------------------------- DFM

def _board(tracks=(), vias=()) -> PcbDocument:
    outline = [{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 50, "y": 40}, {"x": 0, "y": 40}]
    stack = [StackLayer(0, "Top", 0x01000001, "copper"), StackLayer(1, "Bottom", 0x01000002, "copper")]
    return PcbDocument(Path("x.PcbDoc"), {}, (0, 0), outline, stack, [], ["GND", "SIG"], [], [], [], [],
                       tracks=list(tracks), vias=list(vias))


def test_dfm_flags_thin_tracks_and_small_vias():
    doc = _board(tracks=[Track(1, 1, 0xFFFF, 0, 0, 10, 0, 0.1), Track(1, 1, 0xFFFF, 0, 0, 10, 0, 0.2)],
                 vias=[Via(0, 5, 5, 0.45, 0.2, 1, 32)])
    res = run_dfm(doc, "jlcpcb_standard")
    checks = {f["check"] for f in res["findings"]}
    assert {"min_track_width", "min_via_drill", "min_via_diameter"} <= checks
    assert not res["passed"]
    assert res["findings"][0]["examples"][0]["net"] == "SIG"


def test_dfm_clean_board_passes_and_profiles_are_complete():
    assert run_dfm(_board(tracks=[Track(1, 1, 0xFFFF, 0, 0, 10, 0, 0.3)]), "generic_standard")["passed"]
    keys = {"min_track_mm", "min_space_mm", "min_drill_mm", "min_annular_ring_mm", "min_via_diameter_mm",
            "max_layers", "max_board_mm"}
    assert all(keys <= set(p) for p in PROFILES.values())
    with pytest.raises(ValueError):
        run_dfm(_board(), "no_such_fab")


# ---------------------------------------------------------------- BOM

def _comp(ref, comment, fp="0603", params=None, kind=0):
    pins = [SchPin("1", "", "passive", False, "", (0, 0), (0, 0), 1)]
    return SchComponent(0, ref, "RES", "", "", comment, fp, [fp], "", 1, 1, (0, 0), 0, False, "", params or {}, pins, kind)


def _model(components, variants=()):
    project = AltiumProject(Path("p.PrjPcb"), [], {}, list(variants), {}, [], [], {})
    sheet = SchDocument(Path("a.SchDoc"), {}, components, [], [], [], [], [], [], {}, {})
    return DesignModel(project, [sheet], [])


def test_bom_grouping_variants_and_csv():
    model = _model([
        _comp("R1", "10k", params={"Manufacturer Part Number": "RC0603-10K"}),
        _comp("R2", "10k", params={"Manufacturer Part Number": "RC0603-10K"}),
        _comp("R3", "1k"),
        _comp("LOGO1", "Logo", kind=2),
    ], [ProjectVariant("Lite", "Lite", [VariantChange("R2", "not_fitted")])])
    full = build_bom(model)
    assert full["line_items"] == 2 and full["total_fitted_parts"] == 3
    assert full["missing_mpn"] == ["R3"] and full["excluded_non_bom"] == ["LOGO1"]
    lite = build_bom(model, variant="Lite")
    assert lite["not_fitted"] == ["R2"] and lite["total_fitted_parts"] == 2
    csv_text = bom_to_csv(full)
    assert csv_text.splitlines()[0].startswith("Item,Quantity,Designators")
    assert '"R1, R2"' in csv_text
    with pytest.raises(ValueError):
        build_bom(model, variant="Nope")
