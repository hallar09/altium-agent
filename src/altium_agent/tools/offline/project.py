# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/project.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Project discovery and overview tools (offline)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from ...offline.context import get_settings
from ...offline.context import find_projects
from ...offline.files.outjob import read_outjob
from ...offline.context import ToolClass
from ...offline.context import safe_path
from .common import ProjectArg, model_for
from .registry import tool


def _default_roots() -> list[Path]:
    """Where to look when no root is given: the user's Documents, the
    configured project's folder, and Altium's public examples (read only).

    The ported tool read ``Settings.roots``, which this project's settings
    never had, so every call without ``root`` raised AttributeError.
    """
    import os
    out: list[Path] = []
    project = get_settings().project
    if project:
        out.append(project.parent)
    home = os.environ.get("USERPROFILE")
    base = Path(home) if home else Path.home()
    out.append(base / "Documents")
    public = os.environ.get("PUBLIC")
    if public:
        out.append(Path(public) / "Documents" / "Altium")
    seen: list[Path] = []
    for r in out:
        if r.exists() and r not in seen:
            seen.append(r)
    return seen


@tool(ToolClass.READ, "project", "Find Altium projects")
def project_find(
    root: Annotated[str | None, Field(description="Folder to search. Defaults to all allowed roots.")] = None,
    name_contains: Annotated[str | None, Field(description="Only projects whose name contains this text.")] = None,
    max_results: Annotated[int, Field(ge=1, le=500)] = 50,
) -> dict:
    """List .PrjPcb projects under the allowed roots (newest first). Start here when no project is configured."""
    roots = [safe_path(root)] if root else _default_roots()
    found = find_projects(roots, max_results=max_results * 4)
    if name_contains:
        found = [p for p in found if name_contains.lower() in p["name"].lower()]
    return {"searched": [str(r) for r in roots], "count": len(found[:max_results]), "projects": found[:max_results],
            "configured_project": str(get_settings().project) if get_settings().project else None}


@tool(ToolClass.READ, "project", "Project summary")
def project_summary(project: ProjectArg = None) -> dict:
    """Overview of a project: documents, parameters, variants, per-sheet object counts and a PCB summary."""
    model = model_for(project)
    out = model.project.summary()
    out["sheets"] = [s.summary() | {"sheet_symbols": len(s.sheet_symbols)} for s in model.sheets]
    out["component_count"] = len(model.components())
    out["load_errors"] = model.sheet_errors
    pcb = model.pcb()
    if pcb:
        s = pcb.summary()
        out["pcb"] = {k: s[k] for k in ("path", "board_outline", "copper_layer_count", "components", "nets", "vias",
                                        "tracks", "net_classes", "differential_pairs")}
    out["outjobs"] = [str(d.path) for d in model.project.docs("OUTJOB") if d.exists]
    return out


@tool(ToolClass.READ, "project", "Project variants")
def project_variants(project: ProjectArg = None) -> dict:
    """Assembly variants defined in the project, with not-fitted and alternate parts per variant."""
    model = model_for(project)
    return {"variants": [
        {"name": v.name, "description": v.description,
         "not_fitted": [c.designator for c in v.changes if c.kind == "not_fitted"],
         "alternate": [{"designator": c.designator, "part": c.alternate_part} for c in v.changes if c.kind == "alternate"],
         "parameter_changes": v.parameter_changes}
        for v in model.project.variants]}


@tool(ToolClass.READ, "project", "List OutJobs")
def outjob_list(project: ProjectArg = None,
                outjob: Annotated[str | None, Field(description="A specific .OutJob file instead of the project's.")] = None) -> dict:
    """Output Job files of the project and the outputs/media each one defines (Gerber, NC Drill, BOM, PDF...)."""
    if outjob:
        paths = [safe_path(outjob, suffixes=(".outjob",))]
    else:
        model = model_for(project)
        paths = [d.path for d in model.project.docs("OUTJOB") if d.exists]
        paths += [p for p in model.project.directory.glob("*.OutJob") if p not in paths]
    return {"outjobs": [read_outjob(Path(p)).to_dict() for p in paths]}
