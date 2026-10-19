# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""Snapshot review over the live Altium design.

In eda-agent this module held the backend-neutral main flow
(review_design, get_board_info, list_components, list_nets, run_drc,
run_erc). With Altium the only backend, all but the review duplicated an
Altium tool (proj_get_board_info, pcb_get_components, proj_get_nets,
pcb_run_drc, proj_run_erc), so Altium Agent keeps only the review, as
``analyze_review_design``. Failures report ``{"ok": False, "reason": ...}``.
"""

from __future__ import annotations

from typing import Any, Optional

from ..core.backends import BackendUnavailableError, resolve_backend
from ..core.review_engine import review_snapshot


async def _snapshot(backend: Optional[str]):
    """Resolve the backend and pull its snapshot, or return a reason string."""
    be = None
    try:
        # Inside the guard: an unrecognised name is refused here rather
        # than quietly resolving to another EDA, and that refusal has to
        # reach the caller as a reason instead of a traceback.
        be = resolve_backend(backend)
        snap = await be.snapshot()
    except BackendUnavailableError as e:
        return None, str(e)
    except Exception as e:  # keep a raw backend traceback out of the response
        # Named from the request when resolution itself failed, since
        # there is no adapter to ask for a name in that case.
        who = be.name if be is not None else (backend or "the active backend")
        return None, f"{who} snapshot failed: {e}"
    return snap, None


def register_eda_tools(mcp) -> None:

    @mcp.tool()
    async def analyze_review_design() -> dict[str, Any]:
        """Netlist-level review of the open Altium design.

        Reads one snapshot (BOM with every pin's net) and reports board
        stats and net classes, annotation hygiene (unannotated, duplicate
        references, missing values) and connectivity (single-pin nets,
        shorted pins/components, unconnected parts and pads, missing
        decoupling). Geometric DRC is pcb_run_drc; ERC is proj_run_erc.
        """
        snap, reason = await _snapshot(None)
        if snap is None:
            return {"ok": False, "reason": reason}
        return review_snapshot(snap)
