# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/inspection.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Detailed inspection tools (offline): PCB primitives, schematic objects, project session.

Names follow kicad-mcp-pro where the concept matches, so agent workflows carry over.
"""

from __future__ import annotations

from ...units import MM_PER_MIL

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from ... import __version__
from ...analysis.copper import ratsnest
from ...offline.context import get_settings
from ...offline.context import find_projects, list_altium_installs, running_altium
from ...offline.errors import InvalidArgument
from ...offline.files.pcbdoc import COPPER_LEGACY, NONE16
from ...offline.files.prjpcb import read_project
from ...offline.files.sch_netlist import expand_bus
from ...offline.context import ToolClass, allows
from ...offline.context import output_dir_for, resolve_project, safe_path
from ...offline.context import active_project, set_active_project
from .common import LimitArg, OffsetArg, PcbArg, ProjectArg, match, model_for, pcb_for
from .registry import TOOLS, paginate, tool


def _r(v: float) -> float:
    return round(v, 4)


# =================================================================== PCB


@tool(ToolClass.READ, "pcb", "List tracks")
def pcb_get_tracks(project: ProjectArg = None, pcb: PcbArg = None,
                   net: Annotated[str | None, Field(description="Substring/glob on net name.")] = None,
                   layer: Annotated[str | None, Field(description="Layer name, e.g. 'Top Layer'.")] = None,
                   include_arcs: bool = True, include_pour: Annotated[bool, Field(description="Include polygon pour fragments.")] = False,
                   limit: LimitArg = 500, offset: OffsetArg = 0) -> dict:
    """Copper tracks (and arcs) with endpoints in mm, width, layer and net."""
    doc = pcb_for(project, pcb)
    items = []
    for t in doc.tracks:
        if t.layer not in COPPER_LEGACY or (t.polygon != NONE16 and not include_pour):
            continue
        ln, nn = doc.layer_label(t.layer), doc.net_name(t.net)
        if (net and not match(nn, net)) or (layer and layer.lower() != ln.lower()):
            continue
        items.append({"kind": "track", "layer": ln, "net": nn, "x1": _r(t.x1), "y1": _r(t.y1), "x2": _r(t.x2),
                      "y2": _r(t.y2), "width_mm": _r(t.width), "length_mm": _r(t.length)})
    if include_arcs:
        for a in doc.arcs:
            if a.layer not in COPPER_LEGACY or (a.polygon != NONE16 and not include_pour):
                continue
            ln, nn = doc.layer_label(a.layer), doc.net_name(a.net)
            if (net and not match(nn, net)) or (layer and layer.lower() != ln.lower()):
                continue
            items.append({"kind": "arc", "layer": ln, "net": nn, "cx": _r(a.cx), "cy": _r(a.cy), "radius_mm": _r(a.radius),
                          "start_deg": round(a.start_angle, 3), "end_deg": round(a.end_angle, 3),
                          "width_mm": _r(a.width), "length_mm": _r(a.length)})
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "pcb", "List vias")
def pcb_get_vias(project: ProjectArg = None, pcb: PcbArg = None, net: str | None = None,
                 limit: LimitArg = 500, offset: OffsetArg = 0) -> dict:
    """Vias with position (mm), diameter, hole, span (start/end layer) and net."""
    doc = pcb_for(project, pcb)
    items = [{"net": doc.net_name(v.net), "x_mm": _r(v.x), "y_mm": _r(v.y), "diameter_mm": _r(v.diameter),
              "hole_mm": _r(v.hole), "from": doc.layer_label(v.layer_start), "to": doc.layer_label(v.layer_end),
              "type": "through" if (v.layer_start, v.layer_end) in ((1, 32), (32, 1)) else "blind/buried"}
             for v in doc.vias if not net or match(doc.net_name(v.net), net)]
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "pcb", "List pads")
def pcb_get_pads(project: ProjectArg = None, pcb: PcbArg = None, designator: str | None = None, net: str | None = None,
                 limit: LimitArg = 500, offset: OffsetArg = 0) -> dict:
    """Pads with owner, number, net, position, size, hole, plating, rotation and layer."""
    doc = pcb_for(project, pcb)
    shapes = {1: "round", 2: "rect", 3: "octagonal", 9: "rounded_rect"}
    items = []
    for p in doc.pads:
        ref = doc.designator(p.component) if p.component != NONE16 else ""
        if designator and ref.lower() != designator.lower():
            continue
        if net and not match(doc.net_name(p.net), net):
            continue
        items.append({"designator": ref, "pad": p.name, "net": doc.net_name(p.net), "x_mm": _r(p.x), "y_mm": _r(p.y),
                      "size_mm": [_r(p.size_x), _r(p.size_y)], "hole_mm": _r(p.hole), "plated": p.plated,
                      "shape": shapes.get(p.shape, str(p.shape)), "rotation": round(p.rotation % 360, 3),
                      "layer": doc.layer_label(p.layer)})
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "pcb", "List graphic shapes and texts")
def pcb_get_shapes(project: ProjectArg = None, pcb: PcbArg = None,
                   layer: Annotated[str | None, Field(description="Layer name filter, e.g. 'Top Overlay', 'Mechanical 1'.")] = None,
                   include_component_graphics: bool = False, limit: LimitArg = 500, offset: OffsetArg = 0) -> dict:
    """Non-copper lines, arcs, fills and free texts (silkscreen, mechanical, keep-out layers)."""
    doc = pcb_for(project, pcb)
    items = []

    def keep(lyr: int, comp: int) -> bool:
        if lyr in COPPER_LEGACY:
            return False
        if comp != NONE16 and not include_component_graphics:
            return False
        return not layer or doc.layer_label(lyr).lower() == layer.lower()

    for t in doc.tracks:
        if keep(t.layer, t.component):
            items.append({"kind": "line", "layer": doc.layer_label(t.layer), "x1": _r(t.x1), "y1": _r(t.y1),
                          "x2": _r(t.x2), "y2": _r(t.y2), "width_mm": _r(t.width)})
    for a in doc.arcs:
        if keep(a.layer, a.component):
            items.append({"kind": "arc", "layer": doc.layer_label(a.layer), "cx": _r(a.cx), "cy": _r(a.cy),
                          "radius_mm": _r(a.radius), "start_deg": a.start_angle, "end_deg": a.end_angle})
    for f in doc.fills:
        if keep(f.layer, f.component) or (f.layer in COPPER_LEGACY and f.component == NONE16 and not layer):
            items.append({"kind": "fill", "layer": doc.layer_label(f.layer), "net": doc.net_name(f.net),
                          "x1": _r(f.x1), "y1": _r(f.y1), "x2": _r(f.x2), "y2": _r(f.y2), "rotation": f.rotation})
    for t in doc.texts:
        if (t.component == NONE16 or include_component_graphics) and (not layer or doc.layer_label(t.layer).lower() == layer.lower()):
            items.append({"kind": "text", "layer": doc.layer_label(t.layer), "text": t.text, "x_mm": _r(t.x),
                          "y_mm": _r(t.y), "height_mm": _r(t.height), "rotation": t.rotation, "mirrored": t.mirrored,
                          "owner": doc.designator(t.component) if t.component != NONE16 else ""})
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "pcb", "List copper zones (polygons)")
def pcb_get_zones(project: ProjectArg = None, pcb: PcbArg = None, include_outline: bool = False) -> dict:
    """Polygon pours with net, layer, pour settings and poured area; plus keep-out and cut-out regions."""
    doc = pcb_for(project, pcb)
    poured = defaultdict(float)
    for rg in doc.regions:
        if rg.polygon != NONE16:
            poured[rg.polygon] += rg.area_mm2
    zones = []
    for i, p in enumerate(doc.polygons):
        z = {k: v for k, v in p.items() if k != "outline_mm"}
        z["poured_area_mm2"] = round(poured.get(i, 0.0), 2)
        z["outline_vertices"] = len(p["outline_mm"])
        if include_outline:
            z["outline_mm"] = p["outline_mm"]
        zones.append(z)
    special = [{"layer": doc.layer_label(r.layer), "keepout": r.is_keepout, "board_cutout": r.is_board_cutout,
                "kind": r.kind, "name": r.name, "area_mm2": round(r.area_mm2, 3), "net": doc.net_name(r.net)}
               for r in doc.regions if r.polygon == NONE16 and (r.is_keepout or r.is_board_cutout or r.kind != 0)]
    return {"zones": zones, "keepouts_and_cutouts": special,
            "unpoured": [z["name"] for z in zones if z["poured_area_mm2"] == 0 and not z["shelved"]]}


@tool(ToolClass.READ, "pcb", "List board layers")
def pcb_get_layers(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Stack-up layers plus every layer that holds objects, with object counts and user names."""
    doc = pcb_for(project, pcb)
    used: Counter = Counter()
    for coll in (doc.tracks, doc.arcs, doc.fills, doc.texts, doc.regions):
        for o in coll:
            used[o.layer] += 1
    for p in doc.pads:
        used[p.layer] += 1
    return {"stack": [lyr.to_dict() for lyr in doc.stack],
            "used_layers": [{"id": lid, "name": doc.layer_label(lid), "objects": n} for lid, n in sorted(used.items())]}


@tool(ToolClass.READ, "pcb", "Board origin")
def pcb_get_origin(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Board (drill) origin in absolute Altium coordinates. All other PCB tools report mm relative to it."""
    doc = pcb_for(project, pcb, primitives=False)
    ox, oy = doc.origin_mil
    return {"origin_mil": [round(ox, 4), round(oy, 4)], "origin_mm": [round(ox * MM_PER_MIL, 4), round(oy * MM_PER_MIL, 4)]}


@tool(ToolClass.READ, "pcb", "Net statistics")
def pcb_get_net_statistics(project: ProjectArg = None, pcb: PcbArg = None, top: Annotated[int, Field(ge=1, le=500)] = 20) -> dict:
    """Totals and rankings: longest nets, most vias, nets per class, width usage, unrouted nets."""
    doc = pcb_for(project, pcb)
    lengths = doc.routed_length_by_net()
    vias = doc.via_count_by_net()
    pads = {n: len(v) for n, v in doc.pad_netlist().items()}
    widths: Counter = Counter(round(t.width, 3) for t in doc.tracks if t.layer in COPPER_LEGACY and t.polygon == NONE16)
    rn = ratsnest(doc)
    return {
        "net_count": len([n for n in doc.nets if n]),
        "total_routed_length_mm": round(sum(lengths.values()), 2),
        "total_vias": len(doc.vias),
        "longest_nets": sorted(({"net": n, "length_mm": v} for n, v in lengths.items()), key=lambda x: -x["length_mm"])[:top],
        "most_vias": sorted(({"net": n, "vias": v} for n, v in vias.items()), key=lambda x: -x["vias"])[:top],
        "highest_fanout": sorted(({"net": n, "pads": v} for n, v in pads.items()), key=lambda x: -x["pads"])[:top],
        "track_width_usage_mm": dict(sorted(widths.items())),
        "single_pad_nets": sorted(n for n, v in pads.items() if v == 1),
        "routing_completion_pct": rn["routing_completion_pct"],
        "nets_with_unrouted_connections": rn["nets_with_unrouted_connections"],
    }


@tool(ToolClass.READ, "pcb", "Ratsnest / unrouted connections")
def pcb_get_ratsnest(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Unrouted connections per net from offline copper connectivity (tracks, arcs, vias, pours, fills, planes)."""
    return ratsnest(pcb_for(project, pcb))


@tool(ToolClass.READ, "pcb", "Unconnected nets")
def get_unconnected_nets(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Just the nets that still have unrouted connections, with the disconnected pad groups."""
    r = ratsnest(pcb_for(project, pcb))
    return {"count": r["nets_with_unrouted_connections"], "unrouted_connections": r["unrouted_connections"],
            "nets": r["open_nets"]}


@tool(ToolClass.READ, "pcb", "Layers used by a footprint")
def pcb_get_footprint_layers(designator: str, project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Every layer a placed component's primitives use (pads, silk, assembly, courtyard/mechanical, bodies)."""
    doc = pcb_for(project, pcb)
    idx = next((c.index for c in doc.components if c.designator.lower() == designator.lower()), None)
    if idx is None:
        raise InvalidArgument(f"{designator} is not on the board")
    used: Counter = Counter()
    for coll in (doc.tracks, doc.arcs, doc.fills, doc.texts, doc.regions, doc.pads):
        for o in coll:
            if o.component == idx:
                used[doc.layer_label(o.layer)] += 1
    return {"designator": doc.components[idx].designator, "footprint": doc.components[idx].footprint,
            "layers": dict(used)}


@tool(ToolClass.READ, "pcb", "Export board statistics")
def pcb_export_stats(project: ProjectArg = None, pcb: PcbArg = None) -> dict:
    """Write board statistics (summary, stack-up, net stats) to JSON in the output folder and return them."""
    doc = pcb_for(project, pcb)
    stats = {"summary": doc.summary(), "stackup": [lyr.to_dict() for lyr in doc.stack],
             "net_statistics": pcb_get_net_statistics(project, pcb)}
    out = output_dir_for(doc.path.parent / "x.PrjPcb", "stats") / f"{doc.path.stem}_stats.json"
    out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return {"path": str(out), **stats}


# =================================================================== schematic


def _sheet_filter(model, sheet: str | None):
    return [s for s in model.sheets if not sheet or s.path.name.lower() == sheet.lower()]


@tool(ToolClass.READ, "schematic", "List wires")
def sch_get_wires(project: ProjectArg = None, sheet: str | None = None, limit: LimitArg = 500, offset: OffsetArg = 0) -> dict:
    """Wires (vertex lists in mils) and buses per sheet, plus junctions."""
    model = model_for(project)
    items = []
    for s in _sheet_filter(model, sheet):
        items += [{"sheet": s.path.name, "kind": "wire", "points_mil": [list(p) for p in w]} for w in s.wires]
        items += [{"sheet": s.path.name, "kind": "bus", "points_mil": [list(p) for p in w]} for w in s.buses]
        items += [{"sheet": s.path.name, "kind": "junction", "at_mil": list(j)} for j in s.junctions]
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "schematic", "List labels")
def sch_get_labels(project: ProjectArg = None, sheet: str | None = None,
                   kind: Literal["any", "net_label", "power_port", "port", "sheet_entry"] = "any",
                   limit: LimitArg = 500, offset: OffsetArg = 0) -> dict:
    """Net labels, power ports, ports and sheet entries with positions (mils) and attributes."""
    model = model_for(project)
    items = []
    for s in _sheet_filter(model, sheet):
        for lbl in s.labels:
            if kind in ("any", lbl.kind):
                items.append({"sheet": s.path.name, "kind": lbl.kind, "text": lbl.text, "at_mil": list(lbl.points[0]),
                              "bus": bool(expand_bus(lbl.text)), **lbl.extra})
        if kind in ("any", "sheet_entry"):
            for sym in s.sheet_symbols:
                for e in sym.entries:
                    items.append({"sheet": s.path.name, "kind": "sheet_entry", "text": e.text, "sheet_symbol": sym.name,
                                  "at_mil": list(e.points[0]), **e.extra})
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "schematic", "List net names")
def sch_get_net_names(project: ProjectArg = None) -> dict:
    """Unique net names from labels/ports/power ports, and the resolved net names of the offline netlist."""
    model = model_for(project)
    named = sorted({lbl.text for s in model.sheets for lbl in s.labels if lbl.text})
    nl = model.netlist()
    return {"label_names": named, "resolved_nets": [n.name for n in nl.nets],
            "power_nets": sorted({n.name for n in nl.nets if n.has_power_port})}


@tool(ToolClass.READ, "schematic", "List sheets (hierarchy)")
def sch_list_sheets(project: ProjectArg = None) -> dict:
    """Schematic sheets with their sheet symbols (child sheets, entries), component and net-label counts."""
    model = model_for(project)
    children = {Path(sym.file_name.replace("\\", "/")).name.lower() for s in model.sheets for sym in s.sheet_symbols}
    return {"sheets": [{"file": s.path.name, "path": str(s.path), "top_level": s.path.name.lower() not in children,
                        "components": len(s.components), "net_labels": len(s.labels_of("net_label")),
                        "ports": [p.text for p in s.labels_of("port")],
                        "sheet_symbols": [{"name": sym.name, "file": sym.file_name, "entries": [e.text for e in sym.entries]}
                                          for sym in s.sheet_symbols],
                        "parameters": s.sheet_parameters} for s in model.sheets]}


@tool(ToolClass.READ, "schematic", "Sheet symbol pins")
def sch_list_sheet_pins(sheet_symbol: Annotated[str, Field(description="Sheet symbol name (designator) or child file name.")],
                        project: ProjectArg = None) -> dict:
    """Entries of one sheet symbol and whether the child sheet has a matching port."""
    model = model_for(project)
    for s in model.sheets:
        for sym in s.sheet_symbols:
            if sheet_symbol.lower() in (sym.name.lower(), sym.file_name.lower()):
                child = next((c for c in model.sheets if c.path.name.lower() == Path(sym.file_name.replace("\\", "/")).name.lower()), None)
                ports = {p.text for p in child.labels_of("port")} if child else set()
                return {"sheet_symbol": sym.name, "file": sym.file_name, "on_sheet": s.path.name,
                        "entries": [{"name": e.text, "io": e.extra.get("io"), "at_mil": list(e.points[0]),
                                     "matching_port": e.text in ports} for e in sym.entries],
                        "child_ports_without_entry": sorted(ports - {e.text for e in sym.entries})}
    raise InvalidArgument(f"sheet symbol {sheet_symbol} not found")


@tool(ToolClass.READ, "schematic", "Trace a net")
def sch_trace_net(net: str, project: ProjectArg = None) -> dict:
    """Follow a net across sheets: every pin, label, port and power port on it, per sheet."""
    model = model_for(project)
    nl = model.netlist()
    hit = next((n for n in nl.nets if n.name.lower() == net.lower() or net in n.aliases), None)
    if hit is None:
        raise InvalidArgument(f"net {net} not found")
    names = hit.aliases | {hit.name}
    per_sheet: dict[str, dict] = defaultdict(lambda: {"pins": [], "labels": []})
    for m in hit.members:
        per_sheet[m.sheet]["pins"].append(f"{m.designator}-{m.pin} ({m.pin_name}, {m.pin_type})")
    for s in model.sheets:
        for lbl in s.labels:
            if lbl.text in names:
                per_sheet[s.path.name]["labels"].append({"kind": lbl.kind, "text": lbl.text, "at_mil": list(lbl.points[0])})
    return {"net": hit.name, "aliases": sorted(names - {hit.name}), "sheets": dict(per_sheet)}


@tool(ToolClass.READ, "schematic", "Pin positions of a symbol")
def sch_get_pin_positions(designator: str, project: ProjectArg = None) -> dict:
    """Absolute electrical hot-spot of every pin (mils), for wiring with live_sch_add_wire."""
    model = model_for(project)
    comp = next((c for c in model.components() if c.designator.lower() == designator.lower()), None)
    if comp is None:
        raise InvalidArgument(f"{designator} not found")
    return {"designator": comp.designator, "pins": [
        {"part": p.part_id, "sheet": comp.sheets[0] if comp.sheets else "", "number": pin.designator, "name": pin.name,
         "type": pin.electrical, "hotspot_mil": list(pin.hotspot), "body_mil": list(pin.base)}
        for p in comp.parts for pin in p.pins]}


@tool(ToolClass.READ, "schematic", "Symbol bounding boxes")
def sch_get_bounding_boxes(project: ProjectArg = None, sheet: str | None = None) -> dict:
    """Approximate symbol extents (mils) from pin geometry and origin; useful for free-space placement."""
    model = model_for(project)
    out = []
    for s in _sheet_filter(model, sheet):
        for c in s.components:
            xs = [c.location[0]] + [p.hotspot[0] for p in c.pins] + [p.base[0] for p in c.pins]
            ys = [c.location[1]] + [p.hotspot[1] for p in c.pins] + [p.base[1] for p in c.pins]
            out.append({"sheet": s.path.name, "designator": c.designator, "min_mil": [min(xs), min(ys)],
                        "max_mil": [max(xs), max(ys)]})
    return {"boxes": out, "note": "Derived from pins; symbol body graphics without pins are not included."}


@tool(ToolClass.READ, "schematic", "Connectivity graph")
def sch_get_connectivity_graph(project: ProjectArg = None, max_nets: Annotated[int, Field(ge=1, le=5000)] = 400) -> dict:
    """Text graph of component -> net adjacency (one line per net) for quick reasoning about topology."""
    model = model_for(project)
    nl = model.netlist()
    lines = [f"{n.name}: " + ", ".join(f"{m.designator}.{m.pin}" for m in n.members) for n in nl.nets[:max_nets]]
    comp_nets: dict[str, set[str]] = defaultdict(set)
    for n in nl.nets:
        for m in n.members:
            comp_nets[m.designator].add(n.name)
    return {"graph": "\n".join(lines), "component_degree": {k: len(v) for k, v in sorted(comp_nets.items())},
            "truncated": len(nl.nets) > max_nets}


@tool(ToolClass.READ, "schematic", "Circuit IR")
def sch_get_circuit_ir(project: ProjectArg = None) -> dict:
    """Semantic circuit description: components (value, footprint, key parameters), nets with typed pins, power nets."""
    model = model_for(project)
    nl = model.netlist()
    return {"components": [{"ref": c.designator, "value": c.comment, "footprint": c.footprint, "lib_ref": c.lib_ref,
                            "description": c.description} for c in model.components() if c.pin_count],
            "nets": [{"name": n.name, "power": n.has_power_port,
                      "pins": [{"ref": m.designator, "pin": m.pin, "name": m.pin_name, "type": m.pin_type} for m in n.members]}
                     for n in nl.nets],
            "power_nets": [n.name for n in nl.nets if n.has_power_port], "scope_mode": nl.mode}


@tool(ToolClass.READ, "schematic", "Population (DNP) status")
def sch_get_population_status(project: ProjectArg = None, variant: str | None = None) -> dict:
    """Fitted / not-fitted / alternate status of every component, optionally for one project variant."""
    model = model_for(project)
    var = None
    if variant:
        var = next((v for v in model.project.variants if v.name.lower() == variant.lower()), None)
        if var is None:
            raise InvalidArgument(f"variant {variant} not found", hint=str([v.name for v in model.project.variants]))
    changes = {c.designator: c for c in (var.changes if var else [])}
    rows = []
    for c in model.components():
        ch = changes.get(c.designator)
        rows.append({"designator": c.designator, "status": ch.kind if ch else "fitted",
                     "alternate_part": ch.alternate_part if ch else ""})
    return {"variant": var.name if var else "[No Variations]", "components": rows,
            "counts": dict(Counter(r["status"] for r in rows))}


@tool(ToolClass.READ, "schematic", "Power flag check")
def sch_check_power_flags(project: ProjectArg = None) -> dict:
    """Nets that feed power-type pins but have no power port (Altium's equivalent of a missing PWR_FLAG),
    and supply-looking nets driven only by labels."""
    from ...analysis.review import is_ground_net, is_power_net

    model = model_for(project)
    nl = model.netlist()
    issues = []
    for n in nl.nets:
        types = Counter(m.pin_type for m in n.members)
        if types.get("power") and not n.has_power_port:
            issues.append({"net": n.name, "problem": "power pins without a power port", "power_pins": types["power"]})
        elif (is_power_net(n.name) or is_ground_net(n.name)) and not n.has_power_port:
            issues.append({"net": n.name, "problem": "supply-named net without a power port"})
    return {"issues": issues, "count": len(issues)}


# =================================================================== project / server


@tool(ToolClass.READ, "project", "Set the active project")
def altium_set_project(project: Annotated[str, Field(description="Path to a .PrjPcb or its folder.")]) -> dict:
    """Make a project the default for every tool in this session (overrides ALTIUM_AGENT_PROJECT)."""
    p = resolve_project(project)
    set_active_project(p)
    return {"active_project": str(p), "summary": read_project(p).summary()["document_counts"]}


@tool(ToolClass.READ, "project", "Active project info")
def altium_get_project_info() -> dict:
    """Which project tools use by default, and its documents."""
    p = active_project() or get_settings().project
    if not p:
        return {"active_project": None, "hint": "Call altium_set_project or project_find."}
    return {"active_project": str(p), **read_project(Path(p)).summary()}


@tool(ToolClass.READ, "project", "Scan a directory for projects")
def altium_scan_directory(path: str, max_results: Annotated[int, Field(ge=1, le=500)] = 100) -> dict:
    """Altium projects and loose design files (SchDoc/PcbDoc/libraries/OutJobs) under one folder."""
    root = safe_path(path)
    files: Counter = Counter()
    loose = []
    for ext in ("SchDoc", "PcbDoc", "SchLib", "PcbLib", "IntLib", "OutJob"):
        for f in root.rglob(f"*.{ext}"):
            if "history" in {x.lower() for x in f.parts}:
                continue
            files[ext] += 1
            if len(loose) < max_results:
                loose.append(str(f))
    return {"projects": find_projects([root], max_results=max_results), "file_counts": dict(files), "files": loose}


@tool(ToolClass.READ, "project", "Recent Altium projects")
def altium_list_recent_projects(limit: Annotated[int, Field(ge=1, le=100)] = 20) -> dict:
    """Recently opened documents/projects from Altium's own history (Windows registry)."""
    if sys.platform != "win32":
        return {"recent": [], "note": "Windows only"}
    import winreg

    recent = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Altium") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                sub = winreg.EnumKey(root, i)
                try:
                    with winreg.OpenKey(root, sub + r"\DesignExplorer\Recent Documents") as k:
                        for j in range(winreg.QueryInfoKey(k)[1]):
                            name, value, _ = winreg.EnumValue(k, j)
                            if isinstance(value, str) and value.lower().endswith(".prjpcb"):
                                recent.append({"order": int("".join(ch for ch in name if ch.isdigit()) or 0),
                                               "path": value, "exists": Path(value).exists(), "install": sub})
                except OSError:
                    continue
    except OSError:
        return {"recent": []}
    recent.sort(key=lambda r: r["order"])
    return {"recent": recent[:limit]}


@tool(ToolClass.READ, "server", "Altium and server version")
def altium_get_version() -> dict:
    """Installed Altium versions, whether one is running, and this server's version."""
    return {"server_version": __version__, "installs": list_altium_installs(), "running": running_altium()}


@tool(ToolClass.READ, "server", "Tool categories")
def altium_list_tool_categories() -> dict:
    """Tool categories with counts (only tools enabled in the current mode)."""
    mode = get_settings().mode
    c = Counter(t.category for t in TOOLS if allows(mode, t.tool_class))
    return {"mode": mode.value, "categories": dict(sorted(c.items()))}


@tool(ToolClass.READ, "server", "Tools in a category")
def altium_get_tools_in_category(category: str) -> dict:
    """Names and one-line descriptions of the tools in one category."""
    mode = get_settings().mode
    import inspect

    return {"category": category, "tools": [
        {"name": t.name, "title": t.title, "live": t.live, "summary": (inspect.getdoc(t.fn) or "").split("\n")[0]}
        for t in TOOLS if t.category == category and allows(mode, t.tool_class)]}


@tool(ToolClass.READ, "server", "Getting-started guide")
def altium_help() -> dict:
    """Concise startup guide: first calls, units, modes and where approximations apply."""
    return {
        "start": ["server_status", "project_find / altium_list_recent_projects", "altium_set_project",
                  "project_summary", "design_review"],
        "units": "PCB: mm from the board origin, Y up. Schematic: mils.",
        "modes": "readonly (default) / write (live edits) / manufacturing (OutJobs, release) / full",
        "authoritative_checks": "live_compile (ERC), live_pcb_drc (DRC), live_project_netlist (compiled nets)",
        "categories": altium_list_tool_categories()["categories"],
    }
