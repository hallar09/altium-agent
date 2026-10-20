# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/erc.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Offline electrical/schematic checks (a fast pre-check; Altium's compiler remains authoritative)."""

from __future__ import annotations

from collections import Counter, defaultdict

from ..offline.files.sch_netlist import Netlist
from .model import DesignModel

DRIVERS = {"output", "io", "power", "passive", "open_collector", "open_emitter", "hiz"}


def _finding(severity: str, check: str, message: str, **where) -> dict:
    d = {"severity": severity, "check": check, "message": message}
    d.update({k: v for k, v in where.items() if v not in (None, "", [])})
    return d


def run_erc(model: DesignModel, netlist: Netlist | None = None, *, max_per_check: int = 50) -> dict:
    netlist = netlist or model.netlist()
    findings: list[dict] = []
    comps = model.components()

    for err in model.sheet_errors:
        findings.append(_finding("error", "document", err))
    for w in netlist.warnings:
        findings.append(_finding("info", "netlist_scope", w))

    # Annotation
    for c in comps:
        if not c.designator or "?" in c.designator or c.designator.startswith("<unnamed"):
            findings.append(_finding("error", "unannotated", f"component {c.designator or '?'} is not annotated",
                                     designator=c.designator, sheets=c.sheets, lib_ref=c.lib_ref))
    # Duplicate part placements (same designator AND same part id placed twice)
    for c in comps:
        part_ids = Counter(p.part_id for p in c.parts)
        dups = [pid for pid, n in part_ids.items() if n > 1]
        if dups and "?" not in c.designator:
            findings.append(_finding("error", "duplicate_designator",
                                     f"{c.designator} is placed {max(part_ids.values())} times (part {dups})",
                                     designator=c.designator, sheets=c.sheets))
        if len({p.lib_ref for p in c.parts}) > 1:
            findings.append(_finding("error", "duplicate_designator",
                                     f"{c.designator} is used by different library parts "
                                     f"({', '.join(sorted({p.lib_ref for p in c.parts}))})",
                                     designator=c.designator, sheets=c.sheets))
    # Footprint / value
    for c in comps:
        if c.pin_count == 0:
            continue  # graphical / mechanical symbols
        if not c.footprint:
            findings.append(_finding("warning", "missing_footprint", f"{c.designator} has no PCB footprint",
                                     designator=c.designator, lib_ref=c.lib_ref))
        if not c.comment or c.comment.strip() in ("*", "Comment"):
            findings.append(_finding("info", "missing_value", f"{c.designator} has no comment/value",
                                     designator=c.designator))

    # Connectivity
    no_erc_pins = 0
    no_erc_points = {(round(x, 1), round(y, 1)) for s in model.sheets for (x, y) in s.no_erc}
    by_comp: dict[str, list[str]] = defaultdict(list)
    for m in netlist.unconnected_pins:
        by_comp[m.designator].append(m.pin)
    hotspots = {}
    for s in model.sheets:
        for comp in s.components:
            for pin in comp.pins:
                hotspots[(comp.designator, pin.designator)] = (round(pin.hotspot[0], 1), round(pin.hotspot[1], 1))
    for des, pins in sorted(by_comp.items()):
        open_pins = [p for p in pins if hotspots.get((des, p)) not in no_erc_points]
        no_erc_pins += len(pins) - len(open_pins)
        if open_pins:
            findings.append(_finding("warning", "unconnected_pin",
                                     f"{des}: {len(open_pins)} unconnected pin(s) without No-ERC: "
                                     f"{', '.join(open_pins[:20])}{' ...' if len(open_pins) > 20 else ''}",
                                     designator=des))
    for lbl in netlist.floating_labels:
        findings.append(_finding("warning", "floating_net_label",
                                 f"'{lbl['name']}' is not connected to any pin", sheets=lbl["sheets"]))
    for net in netlist.nets:
        if len(net.members) == 1:
            m = net.members[0]
            findings.append(_finding("warning", "single_pin_net",
                                     f"net {net.name} only connects {m.designator}-{m.pin}", net=net.name))
        types = Counter(m.pin_type for m in net.members)
        if types.get("output", 0) > 1:
            outs = [f"{m.designator}-{m.pin}" for m in net.members if m.pin_type == "output"]
            findings.append(_finding("error", "output_conflict",
                                     f"net {net.name} has {len(outs)} output pins: {', '.join(outs[:10])}",
                                     net=net.name))
        if types and set(types) <= {"input"} and not net.has_power_port:
            findings.append(_finding("warning", "undriven_net",
                                     f"net {net.name} has only input pins ({sum(types.values())})", net=net.name))

    # Cap list size per check.
    counts = Counter(f["check"] for f in findings)
    kept: list[dict] = []
    seen: Counter = Counter()
    for f in findings:
        seen[f["check"]] += 1
        if seen[f["check"]] <= max_per_check:
            kept.append(f)
    sev = Counter(f["severity"] for f in findings)
    return {
        "summary": {"errors": sev.get("error", 0), "warnings": sev.get("warning", 0), "info": sev.get("info", 0),
                    "by_check": dict(counts), "pins_suppressed_by_no_erc": no_erc_pins,
                    "components": len(comps), "nets": len(netlist.nets)},
        "findings": kept,
        "truncated": len(kept) < len(findings),
        "note": "Offline pre-check. Run project_compile (live bridge) for Altium's authoritative ERC.",
    }
