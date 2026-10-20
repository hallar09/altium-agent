# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/consistency.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Schematic <-> PCB consistency (an offline 'Design > Update PCB' preview)."""

from __future__ import annotations

import re
from collections import defaultdict

from ..offline.files.pcbdoc import PcbDocument
from ..offline.files.sch_netlist import Netlist
from .model import DesignModel

NO_PCB_KINDS = {2}  # graphical


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def compare(model: DesignModel, pcb: PcbDocument, netlist: Netlist | None = None, *, limit: int = 100) -> dict:
    netlist = netlist or model.netlist()
    sch, bom_only = {}, []
    for c in model.components():
        if not c.designator or "?" in c.designator:
            continue
        if c.parts and c.parts[0].kind in NO_PCB_KINDS:
            continue
        if not c.footprint and c.pin_count == 0:
            bom_only.append(c.designator)  # e.g. mechanical shorting links, screws: BOM items with no footprint
            continue
        sch[c.designator] = c
    pcb_comps = {c.designator: c for c in pcb.components if not c.designator.startswith("#")}

    only_sch = sorted(set(sch) - set(pcb_comps), key=_natural)
    only_pcb_all = sorted(set(pcb_comps) - set(sch) - set(bom_only), key=_natural)
    # Parts placed directly on the PCB (logos, fiducials, mounting holes) have no schematic link.
    only_pcb = [d for d in only_pcb_all if pcb_comps[d].unique_id]
    pcb_only_unlinked = [d for d in only_pcb_all if not pcb_comps[d].unique_id]
    footprint_diff = []
    comment_diff = []
    for d in sorted(set(sch) & set(pcb_comps), key=_natural):
        s, p = sch[d], pcb_comps[d]
        if s.footprint and p.footprint and s.footprint.lower() != p.footprint.lower():
            # Altium Agent: a PCB footprint that is one of the part's other
            # footprint models (an IPC density variant, say) is still a change
            # Update PCB would make, but a different kind of finding from a
            # footprint the schematic part does not offer at all.
            alternates = {f.lower() for part in s.parts for f in getattr(part, "footprints", [])}
            footprint_diff.append({"designator": d, "schematic": s.footprint, "pcb": p.footprint,
                                   "pcb_is_an_alternate_model": p.footprint.lower() in alternates})
        if s.comment and p.comment and s.comment.strip() != p.comment.strip():
            comment_diff.append({"designator": d, "schematic": s.comment, "pcb": p.comment})

    # Pin-level connectivity.
    pcb_pin_net: dict[str, str] = {}
    for net, pins in pcb.pad_netlist().items():
        for pin in pins:
            pcb_pin_net[pin] = net
    sch_pin_net: dict[str, str] = {}
    for net in netlist.nets:
        for m in net.members:
            sch_pin_net[f"{m.designator}-{m.pin}"] = net.name

    renamed: dict[tuple[str, str], int] = defaultdict(int)
    split_merge = []
    for net in netlist.nets:
        pins = [f"{m.designator}-{m.pin}" for m in net.members if m.designator in pcb_comps]
        pcb_nets = {pcb_pin_net.get(p, "<no net>") for p in pins}
        if len(pcb_nets) > 1:
            split_merge.append({"schematic_net": net.name,
                                "pcb_nets": sorted(pcb_nets, key=_natural),
                                "pins": {p: pcb_pin_net.get(p, "<no net>") for p in pins[:40]}})
        elif len(pcb_nets) == 1:
            (pn,) = pcb_nets
            if pn != net.name and pn != "<no net>":
                renamed[(net.name, pn)] += 1
    extra_pcb_connections = []
    for net, pins in pcb.pad_netlist().items():
        sch_nets = {sch_pin_net[p] for p in pins if p in sch_pin_net}
        if len(sch_nets) > 1:
            extra_pcb_connections.append({"pcb_net": net, "schematic_nets": sorted(sch_nets, key=_natural)})

    in_sync = not (only_sch or only_pcb or footprint_diff or split_merge or extra_pcb_connections)
    return {
        "in_sync": in_sync,
        "components_only_in_schematic": only_sch[:limit],
        "components_only_in_pcb": only_pcb[:limit],
        "pcb_only_unlinked_parts": pcb_only_unlinked[:limit],
        "schematic_bom_only_parts": bom_only[:limit],
        "footprint_mismatches": footprint_diff[:limit],
        "comment_mismatches": comment_diff[:limit],
        "nets_split_or_unassigned_in_pcb": split_merge[:limit],
        "pcb_nets_merging_schematic_nets": extra_pcb_connections[:limit],
        "net_name_differences": [{"schematic": a, "pcb": b} for (a, b) in sorted(renamed)][:limit],
        "counts": {"schematic_components": len(sch), "pcb_components": len(pcb_comps),
                   "schematic_nets": len(netlist.nets), "pcb_nets": len(pcb.nets)},
        "netlist_warnings": netlist.warnings,
        "note": "Uses the offline schematic netlist; verify with Design > Update PCB (ECO) in Altium before acting.",
    }
