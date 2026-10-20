# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/registry.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Capture the ported tools; tools/offline/__init__.py publishes them under
Altium Agent names (or leaves them out, deliberately)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable
    tool_class: object
    category: str
    title: str
    destructive: bool
    idempotent: bool
    live: bool
    open_world: bool = False

    @property
    def name(self) -> str:
        return self.fn.__name__


TOOLS: list[ToolSpec] = []


def tool(tool_class, category: str, title: str, *, destructive: bool = False,
         idempotent: bool = True, live: bool = False, open_world: bool = False) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        if not any(t.fn is fn for t in TOOLS):
            TOOLS.append(ToolSpec(fn, tool_class, category, title, destructive, idempotent, live, open_world))
        return fn

    return deco


def paginate(items: list, offset: int, limit: int) -> dict:
    offset = max(0, offset)
    limit = max(1, min(limit, 5000))
    page = items[offset: offset + limit]
    return {"total": len(items), "offset": offset, "returned": len(page),
            "next_offset": offset + len(page) if offset + len(page) < len(items) else None, "items": page}
