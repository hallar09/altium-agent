# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/library.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Library tools: browse .SchLib/.PcbLib files and search across project libraries."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from ...offline.files.libraries import read_pcblib, read_schlib
from ...offline.context import ToolClass
from ...offline.context import safe_path
from .common import LimitArg, OffsetArg, ProjectArg, match, model_for
from .registry import paginate, tool


@tool(ToolClass.READ, "library", "List symbols in a SchLib")
def lib_symbols(path: Annotated[str, Field(description="Path to a .SchLib")],
                filter: Annotated[str | None, Field(description="Substring/glob on lib ref or description.")] = None,
                limit: LimitArg = 200, offset: OffsetArg = 0) -> dict:
    """Symbols in a schematic library with description, part count, pin count, linked footprints and parameters."""
    syms = read_schlib(safe_path(path, suffixes=(".schlib",)))
    items = [s.to_dict() for s in syms if match(s.lib_ref, filter) or match(s.description, filter)]
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "library", "List footprints in a PcbLib")
def lib_footprints(path: Annotated[str, Field(description="Path to a .PcbLib")],
                   filter: Annotated[str | None, Field(description="Substring/glob on footprint name.")] = None,
                   limit: LimitArg = 200, offset: OffsetArg = 0) -> dict:
    """Footprints in a PCB library with description, height and pad count."""
    fps = read_pcblib(safe_path(path, suffixes=(".pcblib",)))
    items = [f.to_dict() for f in fps if match(f.name, filter) or match(f.description, filter)]
    return paginate(items, offset, limit)


@tool(ToolClass.READ, "library", "Search project libraries")
def lib_search(query: Annotated[str, Field(description="Substring/glob matched against symbol/footprint names and descriptions.")],
               project: ProjectArg = None,
               extra_folders: Annotated[list[str] | None, Field(description="More folders to scan for .SchLib/.PcbLib.")] = None,
               limit: LimitArg = 100) -> dict:
    """Search the project's .SchLib/.PcbLib (and any extra folders) for symbols and footprints."""
    model = model_for(project)
    libs: list[Path] = [d.path for d in model.project.documents if d.kind in ("SCHLIB", "PCBLIB") and d.exists]
    folders = [model.project.directory] + [safe_path(f) for f in (extra_folders or [])]
    for folder in folders:
        for pattern in ("*.SchLib", "*.PcbLib", "*.schlib", "*.pcblib"):
            libs.extend(p for p in folder.rglob(pattern) if "history" not in {x.lower() for x in p.parts})
    seen, symbols, footprints, errors = set(), [], [], []
    for lib in libs:
        key = str(lib).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if lib.suffix.lower() == ".schlib":
                symbols += [{"library": str(lib), **s.to_dict()} for s in read_schlib(lib)
                            if match(s.lib_ref, query) or match(s.description, query)]
            else:
                footprints += [{"library": str(lib), **f.to_dict()} for f in read_pcblib(lib)
                               if match(f.name, query) or match(f.description, query)]
        except Exception as exc:  # noqa: BLE001 - keep searching other libraries
            errors.append(f"{lib.name}: {exc}")
    return {"libraries_scanned": len(seen), "symbols": symbols[:limit], "footprints": footprints[:limit],
            "errors": errors}
