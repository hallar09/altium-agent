# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/engineering.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Engineering analyses: power integrity, thermal, signal integrity extras, EMC heuristics.

Physics/heuristics come from the ported kicad-mcp-pro engines (src/altium_mcp/engines); this module
feeds them geometry read from the Altium PcbDoc. All results are first-order estimates, labelled as
such, and never a substitute for a field solver, thermal simulation or a compliance lab.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ...analysis.copper import _point_in_poly
from ...analysis.review import decoupling_review, is_ground_net, is_power_net
from ...analysis.si import board_geometry
from ...analysis.engines.impedance import list_dielectric_materials, recommended_decoupling_distance_mm, \
    via_stub_resonance_ghz, via_stub_risk_level
from ...analysis.engines.models_power_integrity import (
    CopperWeightCheckInput,
    DecouplingRecommendationInput,
    ThermalPlaneSpreadInput,
    ThermalPourInput,
    ThermalViaInput,
)
from ...analysis.engines.models_signal_integrity import DifferentialPairSkewInput
from ...analysis.engines.svc_copper_weight import CopperTrackObservation, PowerIntegrityCopperWeightService
from ...analysis.engines.svc_decoupling import PowerIntegrityDecouplingService
from ...analysis.engines.svc_differential_pair_skew import SignalIntegrityDifferentialPairSkewService
from ...analysis.engines.svc_high_speed_channel import SignalIntegrityHighSpeedChannelService
from ...analysis.engines.svc_length_matching import SignalIntegrityLengthMatchingService
from ...analysis.engines.svc_pdn_mesh_check import PowerIntegrityPdnMeshService
from ...analysis.engines.svc_stackup_generation import SignalIntegrityStackupGenerationService
from ...analysis.engines.svc_stackup_synthesis import SignalIntegrityStackupSynthesisService
from ...analysis.engines.svc_thermal_plane_spreading import PowerIntegrityThermalPlaneService
from ...analysis.engines.svc_thermal_pour import CopperPourObservation, PowerIntegrityThermalPourService
from ...analysis.engines.svc_thermal_via import PowerIntegrityThermalViaService
from ...analysis.engines.svc_voltage_drop import PowerIntegrityVoltageDropService
from ...offline.errors import InvalidArgument
from ...offline.files.pcbdoc import COPPER_LEGACY, NONE16, PcbDocument
from ...offline.context import ToolClass
from .common import PcbArg, ProjectArg, match, pcb_for
from .registry import tool

HIGH_SPEED_NAME_HINTS = ("CLK", "USB", "DDR", "ETH", "PCIE", "HDMI", "LVDS", "MIPI", "SDIO", "QSPI", "_P", "_N", "D+", "D-")


def _out(result) -> dict:
    if isinstance(result, BaseModel):
        d = result.model_dump(exclude_none=True)
        return {k: v for k, v in d.items() if v not in ("", [], {})}
    return {"report": str(result)}


def _net_index(doc: PcbDocument, net: str) -> int:
    for i, n in enumerate(doc.nets):
        if n.lower() == net.lower():
            return i
    close = [n for n in doc.nets if net.lower() in n.lower()][:15]
    raise InvalidArgument(f"net {net} not on the board", hint=f"similar: {close}" if close else "")


def _net_lengths(doc: PcbDocument) -> dict[str, float]:
    return doc.routed_length_by_net()


def _copper_oz(doc: PcbDocument, layer: int) -> float:
    s = doc.copper_for_legacy(layer)
    return round((s.copper_thickness_mm or 0.035) / 0.035, 3) if s else 1.0


def _gnd_cover(doc: PcbDocument) -> dict[int, list[list[tuple[float, float]]]]:
    """Copper layer -> GND reference polygons (pours, fills). Plane layers with a GND net cover everything."""
    cover: dict[int, list] = defaultdict(list)
    for rg in doc.regions:
        if rg.net != NONE16 and is_ground_net(doc.net_name(rg.net)) and len(rg.outline) >= 3:
            cover[rg.layer].append(rg.outline)
    for f in doc.fills:
        if f.net != NONE16 and is_ground_net(doc.net_name(f.net)):
            cover[f.layer].append([(f.x1, f.y1), (f.x2, f.y1), (f.x2, f.y2), (f.x1, f.y2)])
    return cover


def _plane_nets(doc: PcbDocument) -> dict[int, str]:
    return {38 + i: doc.board.get(f"PLANE{i}NETNAME", "").strip() for i in range(1, 17)
            if doc.board.get(f"PLANE{i}NETNAME", "").strip() not in ("", "(No Net)")}


def _copper_order(doc: PcbDocument) -> list[int]:
    """Legacy layer ids of copper layers in physical (stack) order."""
    order = []
    for s in doc.stack:
        if s.kind != "copper":
            continue
        lid = 32 if s.layer_id == 0x0100FFFF else s.layer_id - 0x01000000
        order.append(lid)
    return order


def _adjacent_layers(doc: PcbDocument, layer: int) -> list[int]:
    order = _copper_order(doc)
    if layer not in order:
        return []
    i = order.index(layer)
    return [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order)]


def _segments(doc: PcbDocument, net_idx: int):
    for t in doc.tracks:
        if t.net == net_idx and t.layer in COPPER_LEGACY and t.polygon == NONE16:
            yield t


def _high_speed_nets(doc: PcbDocument) -> list[str]:
    names = set()
    for dp in doc.diff_pairs:
        names.update(n for n in (dp.get("positive"), dp.get("negative")) if n)
    for c in doc.classes:
        if c["kind"] == "net" and not c["superclass"] and any(h in c["name"].upper() for h in ("HS", "HIGH", "USB", "DDR", "ETH", "CLK", "DIFF")):
            names.update(c["members"])
    for n in doc.nets:
        if any(h in n.upper() for h in ("CLK", "USB", "DDR", "ETH", "HDMI", "LVDS", "PCIE", "MIPI")):
            names.add(n)
    return sorted(n for n in names if n in doc.nets)


# =================================================================== power integrity / thermal


@tool(ToolClass.READ, "power-integrity", "Trace voltage drop")
def pdn_calculate_voltage_drop(current_a: Annotated[float, Field(gt=0, le=1000)],
                               trace_width_mm: Annotated[float, Field(gt=0, le=100)],
                               trace_length_mm: Annotated[float, Field(gt=0, le=10000)],
                               copper_oz: Annotated[float, Field(gt=0.1, le=10)] = 1.0,
                               max_temp_rise_c: Annotated[float, Field(gt=0, le=200)] = 10.0,
                               internal_layer: bool = False) -> dict:
    """DC voltage drop, trace resistance, power loss and IPC-2221 temperature rise for one trace."""
    return _out(PowerIntegrityVoltageDropService().calculate_voltage_drop(
        current_a, trace_width_mm, trace_length_mm, copper_oz, max_temp_rise_c, internal_layer))


@tool(ToolClass.READ, "power-integrity", "Copper weight check for a net")
def pdn_check_copper_weight(net: str, expected_current_a: Annotated[float, Field(gt=0, le=1000)],
                            max_temp_rise_c: Annotated[float, Field(gt=0, le=200)] = 10.0,
                            project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Is the routed copper of a power net wide/thick enough for its current? Uses every track of the net with
    the copper thickness of its stack-up layer."""
    doc = pcb_for(project, pcb)
    idx = _net_index(doc, net)
    obs = []
    for t in _segments(doc, idx):
        s = doc.copper_for_legacy(t.layer)
        obs.append(CopperTrackObservation(t.width, t.length, (s.copper_thickness_mm if s and s.copper_thickness_mm else 0.035),
                                          t.layer in (1, 32)))
    payload = CopperWeightCheckInput(net_name=doc.nets[idx], expected_current_a=expected_current_a,
                                     max_temp_rise_c=max_temp_rise_c)
    res = _out(PowerIntegrityCopperWeightService().analyze(payload, obs))
    res["tracks_considered"] = len(obs)
    res["note"] = "Pours/planes on the net also carry current; this checks only routed tracks (the bottlenecks)."
    return res


@tool(ToolClass.READ, "power-integrity", "Recommend decoupling capacitors")
def pdn_recommend_decoupling_caps(ic_refs: Annotated[list[str], Field(min_length=1)], vcc_net: str,
                                  supply_voltage_v: Annotated[float, Field(gt=0, le=1000)],
                                  target_ripple_mv: Annotated[float, Field(gt=0, le=1000)] = 20.0,
                                  project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Local + bulk decoupling recommendation per IC, with the capacitors currently placed nearby."""
    doc = pcb_for(project, pcb, primitives=False)
    comps = {c.designator: c for c in doc.components}
    nearby = {}
    for ref in ic_refs:
        a = comps.get(ref)
        if a is None:
            nearby[ref] = []
            continue
        cands = [(c.designator, math.hypot(c.x_mm - a.x_mm, c.y_mm - a.y_mm), c.comment)
                 for c in doc.components if c.designator.upper().startswith("C") and c.designator != ref]
        nearby[ref] = sorted(cands, key=lambda c: c[1])[:8]
    payload = DecouplingRecommendationInput(ic_refs=ic_refs, vcc_net=vcc_net, supply_voltage_v=supply_voltage_v,
                                            target_ripple_mv=target_ripple_mv)
    return _out(PowerIntegrityDecouplingService().recommend(
        payload=payload, references=ic_refs, recommendation_mm=recommended_decoupling_distance_mm(200.0),
        nearby_by_ref=nearby))


@tool(ToolClass.READ, "power-integrity", "PDN impedance / IR check")
def check_power_integrity(net: str, source_ref: str, load_refs: Annotated[list[str], Field(min_length=1)],
                          load_current_a: Annotated[float, Field(gt=0, le=1000)] = 0.1,
                          nominal_voltage_v: Annotated[float, Field(gt=0, le=1000)] = 3.3,
                          target_impedance_ohm: float | None = None,
                          project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """PDN mesh check for one rail: DC drop from source to loads plus impedance vs frequency with the
    decoupling capacitors found on the net. Trace width/length/copper weight are taken from the board."""
    doc = pcb_for(project, pcb)
    idx = _net_index(doc, net)
    segs = list(_segments(doc, idx))
    width = statistics.median([t.width for t in segs]) if segs else 0.5
    length = doc.routed_length_by_net().get(doc.nets[idx], 100.0) or 100.0
    oz = statistics.median([_copper_oz(doc, t.layer) for t in segs]) if segs else 1.0
    caps = []
    for p in doc.pads:
        if p.net == idx and p.component != NONE16 and doc.designator(p.component).upper().startswith("C"):
            uf = _parse_cap_uf(doc.components[p.component].comment)
            if uf:
                caps.append(uf)
    res = _out(PowerIntegrityPdnMeshService().analyze(
        net_name=doc.nets[idx], source_ref=source_ref, load_refs=load_refs, trace_width_mm=width,
        load_current_a=load_current_a, trace_length_mm=min(length, 10000.0), copper_weight_oz=oz,
        nominal_voltage_v=nominal_voltage_v, decoupling_caps_uf=caps or None, target_impedance_ohm=target_impedance_ohm))
    res["derived_from_board"] = {"median_width_mm": round(width, 4), "routed_length_mm": round(length, 2),
                                 "copper_oz": oz, "decoupling_caps_uf": caps[:40]}
    return res


def _parse_cap_uf(text: str) -> float | None:
    import re

    m = re.search(r"(\d+(?:\.\d+)?)\s*([pnuµμ]|uf|nf|pf)?(\d*)", (text or "").lower().replace("µ", "u").replace("μ", "u"))
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "u")[0]
    if m.group(3) and m.group(2) and len(m.group(2)) == 1:  # 4u7 style
        value = float(f"{m.group(1)}.{m.group(3)}")
    return value * {"p": 1e-6, "n": 1e-3, "u": 1.0}.get(unit, 1.0)


@tool(ToolClass.READ, "power-integrity", "Thermal via count")
def thermal_calculate_via_count(power_w: Annotated[float, Field(gt=0, le=10000)],
                                ambient_c: float = 25.0, max_junction_c: float = 125.0,
                                theta_ja_deg_c_w: Annotated[float, Field(gt=0.1)] = 40.0,
                                via_diameter_mm: Annotated[float, Field(gt=0.05, le=5)] = 0.3,
                                project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """How many thermal vias a package needs, using the real board thickness when a PcbDoc is available."""
    thickness = 1.6
    try:
        thickness = pcb_for(project, pcb, primitives=False).board_thickness_mm
    except Exception:  # noqa: BLE001 - board optional
        pass
    payload = ThermalViaInput(power_w=power_w, ambient_c=ambient_c, max_junction_c=max_junction_c,
                              theta_ja_deg_c_w=theta_ja_deg_c_w, via_diameter_mm=via_diameter_mm)
    res = _out(PowerIntegrityThermalViaService().estimate(payload, board_thickness_mm=thickness))
    res["board_thickness_mm"] = thickness
    return res


@tool(ToolClass.READ, "power-integrity", "Copper pour for heat")
def thermal_check_copper_pour(net: str, expected_power_w: Annotated[float, Field(gt=0, le=10000)],
                              project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Does a net have copper pours (and on which layers) to spread the expected heat?"""
    doc = pcb_for(project, pcb)
    idx = _net_index(doc, net)
    pours = [CopperPourObservation(p["name"] or f"poly{i}", (p["layer"],)) for i, p in enumerate(doc.polygons)
             if p["net"] == doc.nets[idx]]
    res = _out(PowerIntegrityThermalPourService().analyze(
        ThermalPourInput(net_name=doc.nets[idx], expected_power_w=expected_power_w), pours, max_items=50))
    area = sum(r.area_mm2 for r in doc.regions if r.net == idx)
    res["poured_area_mm2"] = round(area, 1)
    return res


@tool(ToolClass.READ, "power-integrity", "Plane heat spreading (2-D FD solver)")
def thermal_simulate_plane_spreading(power_w: Annotated[float, Field(gt=0, le=10000)],
                                     plane_width_mm: Annotated[float, Field(gt=0, le=1000)],
                                     plane_height_mm: Annotated[float, Field(gt=0, le=1000)],
                                     source_width_mm: Annotated[float, Field(gt=0)] = 5.0,
                                     source_height_mm: Annotated[float, Field(gt=0)] = 5.0,
                                     copper_oz: Annotated[float, Field(gt=0.1, le=10)] = 1.0,
                                     ambient_c: float = 25.0,
                                     max_temp_rise_c: Annotated[float, Field(gt=0, le=300)] = 40.0) -> dict:
    """Finite-difference temperature field of a copper plane with a central heat source."""
    return _out(PowerIntegrityThermalPlaneService().analyze(ThermalPlaneSpreadInput(
        power_w=power_w, plane_width_mm=plane_width_mm, plane_height_mm=plane_height_mm,
        source_width_mm=source_width_mm, source_height_mm=source_height_mm, copper_oz=copper_oz,
        ambient_c=ambient_c, max_temp_rise_c=max_temp_rise_c)))


# =================================================================== signal integrity


@tool(ToolClass.READ, "signal-integrity", "High-speed channel loss / eye")
def si_analyze_high_speed_channel(data_rate_gbps: Annotated[float, Field(gt=0, le=200)],
                                  length_mm: Annotated[float | None, Field(gt=0, le=2000)] = None,
                                  net: Annotated[str | None, Field(description="Take the routed length from this net.")] = None,
                                  z0_ohm: float = 50.0, eps_eff: float = 3.8, loss_tangent: float = 0.02,
                                  trace_width_mm: float = 0.2, max_insertion_loss_db: float = 10.0,
                                  project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Insertion loss (conductor + dielectric) at Nyquist and a first-order eye estimate for a lossy line.
    Uses ngspice for a cross-check when it is installed."""
    if net:
        doc = pcb_for(project, pcb)
        length_mm = doc.routed_length_by_net().get(doc.nets[_net_index(doc, net)])
    if not length_mm:
        raise InvalidArgument("pass length_mm, or a routed net")
    return _out(SignalIntegrityHighSpeedChannelService().analyze(
        length_mm, data_rate_gbps, z0_ohm, eps_eff, loss_tangent, trace_width_mm, 1.0, max_insertion_loss_db))


@tool(ToolClass.READ, "signal-integrity", "Via stub resonance")
def si_check_via_stub(frequency_ghz: Annotated[float, Field(gt=0, le=1000)],
                      net: Annotated[str | None, Field(description="Check every via of this net.")] = None,
                      er: Annotated[float, Field(gt=1, le=20)] = 4.0,
                      project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Stub length of through vias from the deepest layer the net actually uses at each via, its quarter-wave
    resonance and the risk at the given frequency."""
    doc = pcb_for(project, pcb)
    nets = [_net_index(doc, net)] if net else [doc.nets.index(n) for n in _high_speed_nets(doc)]
    thickness = doc.board_thickness_mm
    rows = []
    for v in doc.vias:
        if v.net not in nets:
            continue
        used = {t.layer for t in doc.tracks if t.net == v.net and t.layer in COPPER_LEGACY and
                (math.hypot(t.x1 - v.x, t.y1 - v.y) < v.diameter / 2 + t.width or math.hypot(t.x2 - v.x, t.y2 - v.y) < v.diameter / 2 + t.width)}
        depths = [d for d in (doc.layer_depth_mm(lyr) for lyr in used) if d is not None]
        deepest = max(depths) if depths else thickness
        top = min(depths) if depths else 0.0
        stub = max(thickness - deepest, top)  # unused barrel beyond the outermost used layers
        rows.append({"net": doc.net_name(v.net), "x_mm": round(v.x, 3), "y_mm": round(v.y, 3),
                     "layers_used": sorted(doc.layer_label(lyr) for lyr in used), "stub_mm": round(stub, 3),
                     "resonance_ghz": round(via_stub_resonance_ghz(stub, er=er), 2) if stub > 0.01 else None,
                     "risk": via_stub_risk_level(stub, frequency_ghz, er=er) if stub > 0.01 else "none"})
    rows.sort(key=lambda r: -r["stub_mm"])
    return {"frequency_ghz": frequency_ghz, "board_thickness_mm": thickness, "vias": rows[:300], "count": len(rows),
            "high_risk": [r for r in rows if r["risk"] in ("high", "critical")][:50],
            "note": "Back-drilling or blind/buried vias remove stubs on fast nets."}


@tool(ToolClass.READ, "signal-integrity", "Differential pair skew")
def si_check_differential_pair_skew(net_p: str, net_n: str,
                                    skew_budget_ps: Annotated[float, Field(gt=0)] = 5.0,
                                    er: Annotated[float, Field(gt=1, le=20)] = 4.2,
                                    trace_type: Literal["microstrip", "stripline"] = "microstrip",
                                    project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Length and delay mismatch of a differential pair from the routed copper, against a skew budget."""
    doc = pcb_for(project, pcb)
    ip, in_ = _net_index(doc, net_p), _net_index(doc, net_n)
    lengths = doc.routed_length_by_net()

    def width(n: str) -> float | None:
        ws = [t.width for t in _segments(doc, doc.nets.index(n))]
        return statistics.median(ws) if ws else None

    def height() -> float:
        layer = doc.copper_layers[0].name if doc.copper_layers else "Top Layer"
        try:
            return board_geometry(doc, layer)["height_mm"]
        except ValueError:
            return 0.2

    return _out(SignalIntegrityDifferentialPairSkewService().analyze(
        DifferentialPairSkewInput(net_p=doc.nets[ip], net_n=doc.nets[in_], er=er, trace_type=trace_type),
        lengths, skew_budget_ps, width, height, lambda p, n: (skew_budget_ps, "tool argument")))


@tool(ToolClass.READ, "signal-integrity", "Length matching groups")
def si_validate_length_matching(net_groups: Annotated[list[list[str]], Field(min_length=1)],
                                tolerance_mm: Annotated[float, Field(ge=0, le=1000)] = 2.0,
                                project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Check that each group of nets (e.g. a DDR byte lane) is length-matched within tolerance."""
    doc = pcb_for(project, pcb)
    lengths = doc.routed_length_by_net()
    return _out(SignalIntegrityLengthMatchingService().validate(net_groups, tolerance_mm, lengths))


@tool(ToolClass.READ, "signal-integrity", "Dielectric materials")
def si_list_dielectric_materials() -> dict:
    """Built-in laminates (FR-4 grades, Rogers, Megtron...) with Dk, Df and usable frequency."""
    return {"materials": list_dielectric_materials()}


@tool(ToolClass.READ, "signal-integrity", "Generate a stack-up")
def si_generate_stackup(layer_count: Literal[2, 4, 6] = 4, target_impedance_ohm: float = 50.0,
                        manufacturer: str = "JLCPCB", er: float = 4.2, copper_oz: float = 1.0) -> dict:
    """Practical stack-up recommendation with trace geometry for a target impedance."""
    return _out(SignalIntegrityStackupGenerationService().generate(layer_count, target_impedance_ohm, manufacturer, er, copper_oz))


@tool(ToolClass.READ, "signal-integrity", "Synthesize stack-up for interfaces")
def si_synthesize_stackup_for_interfaces(
    interfaces: Annotated[list[dict], Field(min_length=1, description=(
        "Each: {'kind': 'usb2'|'usb3'|'pcie_g3'|'ddr4'|'ethernet_1g'|..., 'impedance_target_ohm': 90, "
        "'differential': true}"))],
    cost_tier: Literal["standard", "midloss", "lowloss", "highspeed", "rf", "ultralow"] = "standard",
    board_thickness_mm: float = 1.6,
) -> dict:
    """Layer count, material, copper weights and net-class geometry that satisfy the listed interfaces."""
    return _out(SignalIntegrityStackupSynthesisService.synthesize(interfaces, cost_tier, board_thickness_mm))


@tool(ToolClass.READ, "signal-integrity", "Decoupling placement for one pin")
def si_calculate_decoupling_placement(ic_ref: str, power_pin: str,
                                      target_freq_mhz: Annotated[float, Field(gt=0)] = 200.0,
                                      project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Recommended max cap distance for a frequency vs the actual nearest capacitor on that pin's net."""
    doc = pcb_for(project, pcb)
    pad = next((p for p in doc.pads if p.component != NONE16 and doc.designator(p.component).lower() == ic_ref.lower()
                and p.name == power_pin), None)
    if pad is None:
        raise InvalidArgument(f"pad {ic_ref}-{power_pin} not found")
    rec = recommended_decoupling_distance_mm(target_freq_mhz)
    caps = [(doc.designator(p.component), math.hypot(p.x - pad.x, p.y - pad.y)) for p in doc.pads
            if p.net == pad.net and p.component != NONE16 and doc.designator(p.component).upper().startswith("C")]
    caps.sort(key=lambda c: c[1])
    nearest = caps[0] if caps else None
    return {"pin": f"{ic_ref}-{power_pin}", "net": doc.net_name(pad.net), "target_freq_mhz": target_freq_mhz,
            "recommended_max_distance_mm": round(rec, 2),
            "nearest_cap": {"ref": nearest[0], "distance_mm": round(nearest[1], 2)} if nearest else None,
            "verdict": "PASS" if nearest and nearest[1] <= rec else ("FAIL" if nearest else "NO_CAP"),
            "other_caps": [{"ref": r, "distance_mm": round(d, 2)} for r, d in caps[1:6]]}


# =================================================================== EMC heuristics


def _sample_points(t, step: float = 1.0):
    n = max(1, int(t.length / step))
    return [(t.x1 + (t.x2 - t.x1) * i / n, t.y1 + (t.y2 - t.y1) * i / n) for i in range(n + 1)]


@tool(ToolClass.READ, "emc", "Return-path continuity")
def emc_check_return_path_continuity(nets: Annotated[list[str] | None, Field(description="Nets to check; default: high-speed nets.")] = None,
                                     project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """For each routed segment, is there a ground reference (GND pour or GND plane) on an adjacent copper
    layer under it? Reports the length routed without a reference per net."""
    doc = pcb_for(project, pcb)
    cover = _gnd_cover(doc)
    planes = {lyr for lyr, n in _plane_nets(doc).items() if is_ground_net(n)}
    targets = nets or _high_speed_nets(doc)
    rows = []
    for name in targets:
        if name not in doc.nets:
            continue
        idx = doc.nets.index(name)
        total = uncovered = 0.0
        for t in _segments(doc, idx):
            adj = _adjacent_layers(doc, t.layer)
            pts = _sample_points(t)
            seg_len = t.length / max(1, len(pts) - 1) if len(pts) > 1 else t.length
            for x, y in pts[:-1] or pts:
                total += seg_len
                ok = any(a in planes for a in adj) or any(_point_in_poly(x, y, poly) for a in adj for poly in cover.get(a, ()))
                if not ok:
                    uncovered += seg_len
        if total:
            pct = 100 * uncovered / total
            rows.append({"net": name, "routed_mm": round(total, 2), "unreferenced_mm": round(uncovered, 2),
                         "unreferenced_pct": round(pct, 1), "verdict": "PASS" if pct < 5 else ("WARN" if pct < 20 else "FAIL")})
    rows.sort(key=lambda r: -r["unreferenced_pct"])
    return {"nets": rows, "failing": [r["net"] for r in rows if r["verdict"] == "FAIL"],
            "method": "sampled every 1 mm; reference = GND pour/fill/plane on an adjacent copper layer"}


@tool(ToolClass.READ, "emc", "Ground plane coverage / voids")
def emc_check_ground_plane_voids(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """GND copper coverage per copper layer relative to the board area; flags missing or heavily voided planes."""
    doc = pcb_for(project, pcb)
    board_area = doc.outline_area_mm2() or 1.0
    per_layer: dict[int, float] = defaultdict(float)
    for rg in doc.regions:
        if rg.net != NONE16 and is_ground_net(doc.net_name(rg.net)):
            per_layer[rg.layer] += rg.area_mm2
    planes = {lyr for lyr, n in _plane_nets(doc).items() if is_ground_net(n)}
    layers = []
    for lid in _copper_order(doc):
        cov = 100.0 if lid in planes else min(100.0, 100 * per_layer.get(lid, 0.0) / board_area)
        layers.append({"layer": doc.layer_label(lid), "gnd_coverage_pct": round(cov, 1)})
    best = max((r["gnd_coverage_pct"] for r in layers), default=0)
    verdict = "PASS" if best >= 70 else ("WARN" if best >= 40 else "FAIL")
    return {"board_area_mm2": round(board_area, 1), "layers": layers, "best_reference_layer_pct": best,
            "verdict": verdict, "advice": "Keep at least one near-solid GND layer adjacent to every signal layer."}


@tool(ToolClass.READ, "emc", "Split-plane crossings")
def emc_check_split_plane_crossing(nets: list[str] | None = None, project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Signals routed over a gap between different power pours on the adjacent layer (return-path breaks)."""
    doc = pcb_for(project, pcb)
    power_polys: dict[int, list[tuple[str, list]]] = defaultdict(list)
    for rg in doc.regions:
        n = doc.net_name(rg.net)
        if rg.net != NONE16 and len(rg.outline) >= 3 and not is_ground_net(n):
            power_polys[rg.layer].append((n, rg.outline))
    crossings = []
    for name in nets or _high_speed_nets(doc):
        if name not in doc.nets:
            continue
        for t in _segments(doc, doc.nets.index(name)):
            for adj in _adjacent_layers(doc, t.layer):
                polys = power_polys.get(adj)
                if not polys:
                    continue
                seen = []
                for x, y in _sample_points(t, 0.5):
                    owner = next((n for n, poly in polys if _point_in_poly(x, y, poly)), None)
                    if not seen or seen[-1] != owner:
                        seen.append(owner)
                if len(seen) > 1:
                    crossings.append({"net": name, "layer": doc.layer_label(t.layer), "reference_layer": doc.layer_label(adj),
                                      "regions_crossed": [s or "<gap>" for s in seen], "at_mm": [round(t.x1, 2), round(t.y1, 2)]})
    return {"crossings": crossings[:200], "count": len(crossings), "verdict": "PASS" if not crossings else "WARN"}


@tool(ToolClass.READ, "emc", "Via stitching density")
def emc_check_via_stitching(max_spacing_mm: Annotated[float, Field(gt=0)] = 10.0, edge_band_mm: Annotated[float, Field(gt=0)] = 3.0,
                            project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """GND via density, largest stitching gap across the board, and edge stitching along the outline."""
    doc = pcb_for(project, pcb)
    gnd = [(v.x, v.y) for v in doc.vias if is_ground_net(doc.net_name(v.net))]
    bbox = doc.outline_bbox()
    if not bbox:
        raise InvalidArgument("board has no outline")
    outline = [(p["x"], p["y"]) for p in doc.outline_mm]
    step = max_spacing_mm / 2
    worst, gaps = 0.0, 0
    y = bbox["min_y"] + step / 2
    while y < bbox["max_y"]:
        x = bbox["min_x"] + step / 2
        while x < bbox["max_x"]:
            if _point_in_poly(x, y, outline):
                d = min((math.hypot(x - gx, y - gy) for gx, gy in gnd), default=1e9)
                worst = max(worst, d)
                gaps += d > max_spacing_mm
            x += step
        y += step
    perim = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(outline, outline[1:] + outline[:1]))
    edge = sum(1 for gx, gy in gnd if min(_seg_d(gx, gy, a, b) for a, b in zip(outline, outline[1:] + outline[:1])) <= edge_band_mm)
    area_cm2 = (doc.outline_area_mm2() or 0) / 100
    return {"gnd_vias": len(gnd), "density_per_cm2": round(len(gnd) / area_cm2, 2) if area_cm2 else None,
            "largest_gap_mm": round(worst, 1), "grid_points_over_spacing": gaps,
            "edge_vias": edge, "edge_spacing_mm": round(perim / edge, 1) if edge else None,
            "verdict": "PASS" if worst <= max_spacing_mm * 1.5 else "WARN"}


def _seg_d(px, py, a, b) -> float:
    from ...analysis.copper import _seg_point_dist

    return _seg_point_dist(px, py, a[0], a[1], b[0], b[1])


@tool(ToolClass.READ, "emc", "Differential pair symmetry")
def emc_check_differential_pair_symmetry(project: ProjectArg = None, pcb: PcbArg = None,
                                         max_mismatch_mm: Annotated[float, Field(ge=0)] = 0.5) -> dict:
    """Every differential pair: length mismatch, width differences and via-count asymmetry."""
    doc = pcb_for(project, pcb)
    lengths = doc.routed_length_by_net()
    vias = doc.via_count_by_net()
    rows = []
    for dp in doc.diff_pairs:
        p, n = dp.get("positive"), dp.get("negative")
        if p not in doc.nets or n not in doc.nets:
            continue
        wp = sorted({round(t.width, 3) for t in _segments(doc, doc.nets.index(p))})
        wn = sorted({round(t.width, 3) for t in _segments(doc, doc.nets.index(n))})
        mism = abs(lengths.get(p, 0) - lengths.get(n, 0))
        rows.append({"pair": dp["name"], "p": p, "n": n, "length_p_mm": lengths.get(p, 0), "length_n_mm": lengths.get(n, 0),
                     "mismatch_mm": round(mism, 3), "widths_p": wp, "widths_n": wn, "vias_p": vias.get(p, 0),
                     "vias_n": vias.get(n, 0),
                     "verdict": "PASS" if mism <= max_mismatch_mm and wp == wn and vias.get(p, 0) == vias.get(n, 0) else "WARN"})
    return {"pairs": rows, "count": len(rows), "note": "No differential pairs defined." if not rows else ""}


@tool(ToolClass.READ, "emc", "High-speed routing rules (stubs)")
def emc_check_high_speed_routing_rules(nets: list[str] | None = None, max_vias: int = 4,
                                       project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Dangling track ends (stubs), via counts and layer changes on high-speed nets."""
    doc = pcb_for(project, pcb)
    rows = []
    for name in nets or _high_speed_nets(doc):
        if name not in doc.nets:
            continue
        idx = doc.nets.index(name)
        segs = list(_segments(doc, idx))
        anchors = [(p.x, p.y, max(p.size_x, p.size_y) / 2) for p in doc.pads if p.net == idx] + \
                  [(v.x, v.y, v.diameter / 2) for v in doc.vias if v.net == idx]
        stubs = 0
        for t in segs:
            for ex, ey in ((t.x1, t.y1), (t.x2, t.y2)):
                touching = sum(1 for o in segs if o is not t and (math.hypot(o.x1 - ex, o.y1 - ey) < 0.01 or math.hypot(o.x2 - ex, o.y2 - ey) < 0.01))
                if not touching and not any(math.hypot(ax - ex, ay - ey) <= r + t.width / 2 for ax, ay, r in anchors):
                    stubs += 1
        nv = sum(1 for v in doc.vias if v.net == idx)
        layers = sorted({doc.layer_label(t.layer) for t in segs})
        rows.append({"net": name, "stubs": stubs, "vias": nv, "layers": layers,
                     "verdict": "PASS" if stubs == 0 and nv <= max_vias else "WARN"})
    return {"nets": rows, "warnings": [r for r in rows if r["verdict"] != "PASS"]}


@tool(ToolClass.READ, "emc", "Decoupling placement (EMC)")
def emc_check_decoupling_placement(max_distance_mm: Annotated[float, Field(gt=0, le=50)] = 3.0,
                                   project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """IC power pins whose nearest decoupling capacitor is too far (same check as decoupling_check)."""
    return decoupling_review(pcb_for(project, pcb), max_distance_mm=max_distance_mm)


@tool(ToolClass.READ, "emc", "Full EMC sweep")
def emc_run_full_compliance(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Run every EMC heuristic (return path, ground coverage, split crossings, stitching, diff-pair symmetry,
    stubs, decoupling) and roll up a verdict. First-order review, not a compliance test."""
    checks = {
        "return_path": emc_check_return_path_continuity(project=project, pcb=pcb),
        "ground_coverage": emc_check_ground_plane_voids(project, pcb),
        "split_plane_crossing": emc_check_split_plane_crossing(project=project, pcb=pcb),
        "via_stitching": emc_check_via_stitching(project=project, pcb=pcb),
        "diff_pair_symmetry": emc_check_differential_pair_symmetry(project, pcb),
        "high_speed_stubs": emc_check_high_speed_routing_rules(project=project, pcb=pcb),
        "decoupling": emc_check_decoupling_placement(project=project, pcb=pcb),
    }
    status = {}
    for k, v in checks.items():
        if "verdict" in v:
            status[k] = v["verdict"]
        elif k == "return_path":
            status[k] = "FAIL" if v["failing"] else "PASS"
        elif k == "high_speed_stubs":
            status[k] = "WARN" if v["warnings"] else "PASS"
        elif k == "decoupling":
            status[k] = "WARN" if v["issue_count"] else "PASS"
        elif k == "diff_pair_symmetry":
            status[k] = "WARN" if any(r["verdict"] != "PASS" for r in v["pairs"]) else "PASS"
    overall = "FAIL" if "FAIL" in status.values() else ("WARN" if "WARN" in status.values() else "PASS")
    return {"verdict": overall, "checks": status, "details": checks,
            "disclaimer": "Heuristic first-pass review; not EM simulation or a compliance lab result."}


# =================================================================== creepage / corner style


@tool(ToolClass.READ, "validation", "Creepage / clearance between nets")
def pcb_check_creepage_clearance(high_voltage_nets: Annotated[list[str], Field(min_length=1)],
                                 min_distance_mm: Annotated[float, Field(gt=0, le=100)] = 2.5,
                                 project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Closest pad-to-pad edge distance between each high-voltage net and every other net (same layer side).
    Surface creepage proxy: slots/coatings are not modelled."""
    doc = pcb_for(project, pcb)
    hv = {doc.nets.index(n) for n in high_voltage_nets if n in doc.nets}
    if not hv:
        raise InvalidArgument("none of the high_voltage_nets are on the board")
    pads = [p for p in doc.pads if p.net != NONE16]
    hv_pads = [p for p in pads if p.net in hv]

    def half(p) -> tuple[float, float]:
        # Axis-aligned half extents; 90/270-degree pads swap their sizes, other angles use the circumscribed square.
        r = round(p.rotation % 180, 1)
        if r in (0.0, 180.0):
            return p.size_x / 2, p.size_y / 2
        if r == 90.0:
            return p.size_y / 2, p.size_x / 2
        m = math.hypot(p.size_x, p.size_y) / 2
        return m, m

    violations = []
    for a in hv_pads:
        ahx, ahy = half(a)
        for b in pads:
            if b.net == a.net or b.net in hv and b.net < a.net:
                continue
            if not (a.layer == b.layer or 74 in (a.layer, b.layer)):  # same side, or a through-hole pad
                continue
            bhx, bhy = half(b)
            gx = abs(a.x - b.x) - ahx - bhx
            gy = abs(a.y - b.y) - ahy - bhy
            d = math.hypot(gx, gy) if gx > 0 and gy > 0 else max(gx, gy)
            if d < min_distance_mm:
                violations.append({"from": f"{doc.designator(a.component)}-{a.name} ({doc.net_name(a.net)})",
                                   "to": f"{doc.designator(b.component)}-{b.name} ({doc.net_name(b.net)})",
                                   "edge_distance_mm": round(max(d, 0.0), 3)})
    violations.sort(key=lambda v: v["edge_distance_mm"])
    return {"min_distance_mm": min_distance_mm, "violations": violations[:200], "count": len(violations),
            "verdict": "PASS" if not violations else "FAIL",
            "note": "Pad edge-to-edge distance (rectangular pads, 90-degree rotations exact). Confirm with an Altium Clearance rule scoped to the HV nets."}


@tool(ToolClass.READ, "validation", "Track corner style gate")
def pcb_route_corner_style_gate(max_right_angles: int = 0, project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Count 90-degree (and acute) corners between joined copper track segments."""
    doc = pcb_for(project, pcb)
    by_end: dict[tuple, list] = defaultdict(list)
    for t in doc.tracks:
        if t.layer in COPPER_LEGACY and t.polygon == NONE16 and t.length > 0:
            for p in ((round(t.x1, 3), round(t.y1, 3)), (round(t.x2, 3), round(t.y2, 3))):
                by_end[(t.layer, t.net, p)].append(t)
    right, acute = [], []
    for (layer, net, p), ts in by_end.items():
        if len(ts) != 2:
            continue
        v = []
        for t in ts:
            ox, oy = ((t.x2, t.y2) if (round(t.x1, 3), round(t.y1, 3)) == p else (t.x1, t.y1))
            v.append((ox - p[0], oy - p[1]))
        dot = v[0][0] * v[1][0] + v[0][1] * v[1][1]
        cos = dot / (math.hypot(*v[0]) * math.hypot(*v[1]) or 1)
        ang = math.degrees(math.acos(max(-1, min(1, cos))))
        item = {"net": doc.net_name(net), "layer": doc.layer_label(layer), "at_mm": list(p), "angle_deg": round(ang, 1)}
        if abs(ang - 90) < 1:
            right.append(item)
        elif ang < 89:
            acute.append(item)
    return {"right_angle_corners": len(right), "acute_corners": len(acute), "examples": (acute + right)[:50],
            "verdict": "PASS" if len(right) <= max_right_angles and not acute else "WARN"}
