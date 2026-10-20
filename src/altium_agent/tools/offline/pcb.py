# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/pcb.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""PCB inspection tools (offline, read straight from .PcbDoc files)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ...offline.errors import InvalidArgument
from ...offline.files.pcbdoc import NONE16, layer_name
from ...offline.context import ToolClass
from .common import LimitArg, OffsetArg, PcbArg, ProjectArg, match, pcb_for
from .registry import paginate, tool


@tool(ToolClass.READ, "pcb", "PCB summary")
def pcb_summary(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Board size/area, copper layers, counts of components, nets, tracks, vias, pads, polygons, classes and rules."""
    return pcb_for(project, pcb).summary()


@tool(ToolClass.READ, "pcb", "Layer stack-up")
def pcb_stackup(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Layer stack (copper thickness, dielectric heights, Dk, materials) read from the saved PcbDoc."""
    doc = pcb_for(project, pcb, primitives=False)
    total = sum((lyr.copper_thickness_mm or 0) + (lyr.dielectric_height_mm or 0) for lyr in doc.stack)
    return {"layers": [lyr.to_dict() for lyr in doc.stack], "copper_layers": len(doc.copper_layers),
            "total_thickness_mm": round(total, 4), "outline": doc.outline_bbox()}


@tool(ToolClass.READ, "pcb", "List PCB components")
def pcb_components(
    project: ProjectArg = None,
    pcb: PcbArg = None,
    filter: Annotated[str | None, Field(description="Substring/glob on designator, footprint or comment.")] = None,
    side: Literal["any", "top", "bottom"] = "any",
    limit: LimitArg = 300,
    offset: OffsetArg = 0,
) -> dict:
    """Placed components: designator, footprint, comment, position (mm from board origin), rotation, side, lock."""
    doc = pcb_for(project, pcb, primitives=False)
    items = [c.to_dict() for c in doc.components
             if (side == "any" or c.side == side)
             and (not filter or any(match(v, filter) for v in (c.designator, c.footprint, c.comment)))]
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "pcb", "List PCB nets")
def pcb_nets(
    project: ProjectArg = None,
    pcb: PcbArg = None,
    filter: Annotated[str | None, Field(description="Substring/glob on net name.")] = None,
    limit: LimitArg = 300,
    offset: OffsetArg = 0,
) -> dict:
    """Nets with pad count, routed copper length (tracks+arcs, mm) and via count."""
    doc = pcb_for(project, pcb)
    pads = doc.pad_netlist()
    lengths = doc.routed_length_by_net()
    vias = doc.via_count_by_net()
    items = [{"name": n, "pads": len(pads.get(n, [])), "routed_length_mm": lengths.get(n, 0.0), "vias": vias.get(n, 0)}
             for n in doc.nets if n and match(n, filter)]
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "pcb", "PCB net detail")
def pcb_net_detail(net: str, project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Pads, per-layer routed length, track widths, vias and polygons of one PCB net."""
    doc = pcb_for(project, pcb)
    try:
        idx = next(i for i, n in enumerate(doc.nets) if n.lower() == net.lower())
    except StopIteration:
        close = [n for n in doc.nets if net.lower() in n.lower()][:20]
        raise InvalidArgument(f"net {net} not on the board", hint=f"similar: {close}" if close else "") from None
    per_layer: dict[str, float] = {}
    widths: set[float] = set()
    for t in doc.tracks:
        if t.net == idx and t.polygon == NONE16:
            per_layer[layer_name(t.layer)] = per_layer.get(layer_name(t.layer), 0.0) + t.length
            widths.add(round(t.width, 4))
    for a in doc.arcs:
        if a.net == idx and a.polygon == NONE16:
            per_layer[layer_name(a.layer)] = per_layer.get(layer_name(a.layer), 0.0) + a.length
            widths.add(round(a.width, 4))
    return {
        "net": doc.nets[idx],
        "pads": [{"pad": f"{doc.designator(p.component)}-{p.name}" if p.component != NONE16 else p.name,
                  "x_mm": round(p.x, 4), "y_mm": round(p.y, 4), "layer": layer_name(p.layer)}
                 for p in doc.pads if p.net == idx],
        "routed_length_mm": round(sum(per_layer.values()), 4),
        "length_by_layer_mm": {k: round(v, 4) for k, v in per_layer.items()},
        "track_widths_mm": sorted(widths),
        "vias": [{"x_mm": round(v.x, 4), "y_mm": round(v.y, 4), "diameter_mm": round(v.diameter, 4),
                  "hole_mm": round(v.hole, 4)} for v in doc.vias if v.net == idx],
        "polygons": [p for p in doc.polygons if p["net"] == doc.nets[idx]],
        "classes": [c["name"] for c in doc.classes if c["kind"] == "net" and doc.nets[idx] in c["members"]],
    }


@tool(ToolClass.READ, "pcb", "PCB design rules")
def pcb_rules(project: ProjectArg = None, pcb: PcbArg = None,
              kind: Annotated[str | None, Field(description="Filter by rule kind, e.g. Clearance, Width, RoutingVias.")] = None,
              enabled_only: bool = True) -> dict:
    """Design rules stored in the PcbDoc with scopes, priority and key values (mm)."""
    doc = pcb_for(project, pcb, primitives=False)
    rules = [r for r in doc.rules if (not enabled_only or r["enabled"]) and (not kind or r["kind"].lower() == kind.lower())]
    rules.sort(key=lambda r: (r["kind"], r["priority"]))
    return {"count": len(rules), "rules": rules}


@tool(ToolClass.READ, "pcb", "Net classes and differential pairs")
def pcb_classes(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Object classes (net/component/layer/diff-pair classes with members) and differential pairs."""
    doc = pcb_for(project, pcb, primitives=False)
    return {"classes": doc.classes, "differential_pairs": doc.diff_pairs, "polygons": doc.polygons}


@tool(ToolClass.READ, "pcb", "Pad netlist from the board")
def pcb_pad_netlist(project: ProjectArg = None, pcb: PcbArg = None,
                    filter: Annotated[str | None, Field(description="Substring/glob on net name.")] = None,
                    limit: LimitArg = 300, offset: OffsetArg = 0) -> dict:
    """Net -> pads ('U1-3') exactly as assigned on the board."""
    doc = pcb_for(project, pcb)
    items = [{"net": n, "pads": pads} for n, pads in doc.pad_netlist().items() if match(n, filter)]
    return paginate(items, offset, limit)
