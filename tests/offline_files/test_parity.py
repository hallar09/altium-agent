# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tests/test_parity.py (the read-only cases). Modified for
# Altium Agent (2026): see docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Regression tests for the ported kicad-mcp-pro parity features."""

from __future__ import annotations

from altium_agent.analysis.copper import ratsnest
from altium_agent.analysis.dfm import PROFILES, estimate_cost
from altium_agent.analysis.model import load_model
from altium_agent.offline.files.pcbdoc import read_pcbdoc

from .conftest import KAME, SPIRIT, requires_examples, requires_kame


def test_profiles_merged_with_pricing():
    assert {"jlcpcb_standard", "jlcpcb_advanced", "pcbway_advanced", "nextpcb_standard", "eurocircuits_pool"} <= set(PROFILES)
    assert "pricing" in PROFILES["jlcpcb_standard"] and PROFILES["jlcpcb_standard"]["copper_to_edge_mm"] > 0


@requires_examples
def test_ratsnest_matches_altium_drc_on_routed_board():
    r = ratsnest(read_pcbdoc(SPIRIT.with_suffix(".PcbDoc")))
    assert r["unrouted_connections"] == 0 and r["nets_checked"] == 126


@requires_examples
def test_pour_regions_inherit_polygon_net():
    doc = read_pcbdoc(SPIRIT.with_suffix(".PcbDoc"))
    gnd = doc.nets.index("GND")
    assert sum(1 for r in doc.regions if r.net == gnd) >= 3
    assert {p["name"] for p in doc.polygons} >= {"Top Layer-GND", "Bottom Layer-GND"}


@requires_examples
def test_cost_estimate():
    c = estimate_cost(read_pcbdoc(SPIRIT.with_suffix(".PcbDoc")), "jlcpcb_standard", 10)
    assert c["copper_layers"] == 2 and c["total_usd"] > 0


@requires_kame
def test_variant_unique_paths_match_altium():
    model = load_model(KAME)
    assert model.unique_path("R11") == "\\TAWJPWKS\\HTJTOFQJ"
    assert model.unique_path("CN4") == "\\KBBYMHAH\\SZJEZVSO"


def test_engines_import_and_run():
    from altium_agent.analysis.engines.svc_voltage_drop import PowerIntegrityVoltageDropService
    from altium_agent.analysis.engines.thermal_solver import ThermalPlaneSpec, solve_plane_temperature  # noqa: F401

    text = PowerIntegrityVoltageDropService().calculate_voltage_drop(2.0, 0.5, 50.0, 1.0, 10.0, False)
    assert "voltage drop" in str(text).lower()
