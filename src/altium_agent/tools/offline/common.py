# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/common.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Shared argument resolution for tools."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from ...analysis.model import DesignModel, load_model
from ...offline.errors import InvalidArgument
from ...offline.files.pcbdoc import PcbDocument, read_pcbdoc
from ...offline.context import project_path_or_none, resolve_project, safe_path  # noqa: F401 - re-exported

ProjectArg = Annotated[str | None, Field(
    description="Path to the .PrjPcb (or its folder). Optional when ALTIUM_AGENT_PROJECT is set.")]
PcbArg = Annotated[str | None, Field(
    description="Path to a .PcbDoc. Optional: defaults to the project's (first) PcbDoc.")]
LimitArg = Annotated[int, Field(ge=1, le=5000, description="Maximum items to return.")]
OffsetArg = Annotated[int, Field(ge=0, description="Items to skip (pagination).")]


def model_for(project: str | None) -> DesignModel:
    return load_model(resolve_project(project))


def pcb_for(project: str | None, pcb: str | None, *, primitives: bool = True) -> PcbDocument:
    if pcb:
        return read_pcbdoc(safe_path(pcb, suffixes=(".pcbdoc",)), primitives=primitives)
    model = model_for(project)
    docs = model.project.pcb_docs
    if not docs:
        raise InvalidArgument(f"project {model.project.name} has no PcbDoc", hint="Pass pcb='path/to/Board.PcbDoc'.")
    return read_pcbdoc(docs[0], primitives=primitives)


def match(text: str, pattern: str | None) -> bool:
    """Case-insensitive substring or glob ('*', '?') match."""
    if not pattern:
        return True
    import fnmatch

    t, p = text.lower(), pattern.lower()
    if any(ch in p for ch in "*?["):
        return fnmatch.fnmatchcase(t, p)
    return p in t
