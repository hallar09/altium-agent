# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/schematic.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Schematic inspection tools (offline, read straight from .SchDoc files)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ...offline.errors import InvalidArgument
from ...offline.files.schdoc import read_schdoc
from ...offline.context import ToolClass
from ...offline.context import safe_path
from .common import LimitArg, OffsetArg, ProjectArg, match, model_for
from .registry import paginate, tool


@tool(ToolClass.READ, "schematic", "List schematic components")
def sch_list_components(
    project: ProjectArg = None,
    filter: Annotated[str | None, Field(description="Substring or glob matched against designator, comment, lib ref, footprint.")] = None,
    sheet: Annotated[str | None, Field(description="Only components on this sheet (file name).")] = None,
    limit: LimitArg = 200,
    offset: OffsetArg = 0,
) -> dict:
    """Components of the project's schematics (multi-part symbols merged per designator)."""
    model = model_for(project)
    items = []
    for c in model.components():
        if sheet and not any(sheet.lower() == s.lower() for s in c.sheets):
            continue
        if filter and not any(match(v, filter) for v in (c.designator, c.comment, c.lib_ref, c.footprint)):
            continue
        items.append(c.to_dict())
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "schematic", "Get schematic component")
def sch_get_component(designator: str, project: ProjectArg = None) -> dict:
    """Full detail for one designator: parameters, footprint options, every part and pin with its net."""
    model = model_for(project)
    comp = next((c for c in model.components() if c.designator.lower() == designator.lower()), None)
    if comp is None:
        raise InvalidArgument(f"{designator} not found in the schematics")
    netlist = model.netlist()
    pin_net = {}
    for net in netlist.nets:
        for m in net.members:
            if m.designator == comp.designator:
                pin_net[m.pin] = net.name
    out = comp.to_dict()
    out["parts"] = []
    for p in comp.parts:
        d = p.to_dict(include_pins=True)
        for pin in d["pins"]:
            pin["net"] = pin_net.get(pin["number"], "")
        out["parts"].append({"part": p.part_id, "footprint_options": p.footprints, "location_mil": d["location_mil"],
                             "pins": d["pins"]})
    return out


@tool(ToolClass.READ, "schematic", "Schematic netlist (offline)")
def sch_netlist(
    project: ProjectArg = None,
    filter: Annotated[str | None, Field(description="Only nets whose name matches (substring or glob).")] = None,
    include_pin_details: bool = False,
    limit: LimitArg = 300,
    offset: OffsetArg = 0,
) -> dict:
    """Nets derived from schematic geometry, honouring the project's net-identifier scope and buses.

    Approximate (no multi-channel expansion); use live_project_netlist for the compiled netlist."""
    model = model_for(project)
    nl = model.netlist().to_dict(include_details=include_pin_details)
    nets = [n for n in nl.pop("nets") if match(n["name"], filter)]
    page = paginate(nets, offset, limit)
    nl.pop("unconnected_pins")
    return {**nl, **page}


@tool(ToolClass.READ, "schematic", "Schematic net detail")
def sch_net_detail(net: str, project: ProjectArg = None) -> dict:
    """Pins (with names and electrical types) on one schematic net, plus aliases and sheets it spans."""
    model = model_for(project)
    nl = model.netlist()
    hit = next((n for n in nl.nets if n.name.lower() == net.lower() or net in n.aliases), None)
    if hit is None:
        close = [n.name for n in nl.nets if net.lower() in n.name.lower()][:20]
        raise InvalidArgument(f"net {net} not found", hint=f"similar: {close}" if close else "")
    return hit.to_dict()


@tool(ToolClass.READ, "schematic", "Read one schematic sheet")
def sch_sheet(path: Annotated[str, Field(description="Path to a .SchDoc")],
              include_objects: Annotated[bool, Field(description="Include net labels, ports and power ports.")] = False) -> dict:
    """Summary of a single .SchDoc (works without a project): components, wires, labels, sheet symbols."""
    doc = read_schdoc(safe_path(path, suffixes=(".schdoc",)))
    out = doc.summary()
    out["components"] = [c.to_dict() for c in doc.components]
    if include_objects:
        out["labels"] = [{"kind": lbl.kind, "text": lbl.text, "at_mil": list(lbl.points[0]), **lbl.extra} for lbl in doc.labels]
    return out
