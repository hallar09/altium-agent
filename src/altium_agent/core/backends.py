# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""Backend adapter: fill a normalized snapshot from the live Altium session.

Altium Agent changes: the KiCad and EasyEDA adapters of eda-agent were
removed; the resolver now knows only Altium (see docs/PROVENANCE.md).

Each adapter turns one EDA's live API into the same :class:`DesignSnapshot`.
The neutral tools call :func:`resolve_backend` to pick the adapter matching the
active backend (or an explicit override) and then ``await backend.snapshot()``.
Adding another EDA is a matter of writing one more adapter here.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .snapshot import DesignSnapshot


class BackendUnavailableError(RuntimeError):
    """The requested backend's tool is not reachable (not running, API off)."""


#: The backend whose tools were actually registered, once something has
#: registered them. Set by ``tools.register_backend``.
_REGISTERED: Optional[str] = None


def set_active_backend(name: str) -> None:
    """Record which backend's tools were registered.

    THE REGISTRY AND THE RESOLVER USED TO DISAGREE. Tool registration
    takes the backend as an argument, while this module read it back out
    of the environment, so the two agreed only as long as one caller set
    both. A harness that registered the EasyEDA surface without also
    exporting the variable got EasyEDA tools and an ALTIUM resolver, and
    ``review_design`` reviewed a completely different design while
    reporting success. With Altium open at the time the answer looked
    entirely plausible: real parts, real nets, wrong document.

    Recording it here removes the second source of truth rather than
    asking every caller to remember the first.
    """
    global _REGISTERED
    _REGISTERED = (name or "").strip().lower() or None


def active_backend_name() -> str:
    """The configured backend, or failing that the registered one.

    THE ENVIRONMENT WINS WHEN IT IS SET, because it is the explicit
    configuration and registration is only a record of what happened.
    Preferring the registration instead made `_REGISTERED` sticky for
    the life of the process: once anything had registered a backend it
    overrode every later `ALTIUM_AGENT_BACKEND`, which is how nine tests
    that set the variable started reading the wrong guide text.

    The bug this still fixes is the opposite case, and it is the common
    one: something registers a backend WITHOUT setting the variable, and
    the resolver silently falls back to the default. A harness that did
    that got EasyEDA tools over an Altium resolver, and review_design
    returned a clean, plausible review of a different EDA's document.
    """
    configured = (os.environ.get("ALTIUM_AGENT_BACKEND") or "").strip().lower()
    if configured:
        return configured
    if _REGISTERED:
        return _REGISTERED
    return "altium"



class AltiumBackend:
    name = "altium"

    async def _bridge(self):
        from ..bridge import get_bridge
        from ..bridge.exceptions import AltiumError
        bridge = get_bridge()
        return bridge, AltiumError

    async def health(self) -> dict[str, Any]:
        bridge, AltiumError = await self._bridge()
        try:
            return await bridge.send_command_async("application.ping", {})
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None

    async def snapshot(self) -> DesignSnapshot:
        bridge, AltiumError = await self._bridge()
        try:
            # One BOM read carries designators, values, footprints, library
            # references and every pin's net -- a complete snapshot.
            bom = await bridge.send_command_async(
                "project.get_bom", {"limit": "5000"})
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None
        parts: list[dict[str, Any]] = []
        pins: list[dict[str, Any]] = []
        unconnected = 0
        for comp in (bom or {}).get("components", []):
            ref = str(comp.get("designator", ""))
            parts.append({
                "refdes": ref,
                "value": comp.get("comment", ""),
                "footprint": comp.get("footprint", ""),
                "lib_ref": comp.get("lib_ref", ""),
            })
            for pin in comp.get("pins", []):
                net = str(pin.get("net", "") or "").strip()
                pins.append({"refdes": ref, "pin": pin.get("pin", ""),
                             "net": net})
                if not net:
                    unconnected += 1
        return DesignSnapshot.build(
            "altium", parts, pins,
            board_name=str((bom or {}).get("project_path", "") or ""),
            unconnected_pad_count=unconnected,
        )

    async def run_drc(self) -> dict[str, Any]:
        bridge, AltiumError = await self._bridge()
        try:
            raw = await bridge.send_command_async("pcb.run_drc", {})
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None
        raw = raw or {}
        violations = raw.get("violations", []) or []
        return {
            "ok": True,
            "source": "altium",
            "violation_count": raw.get("violation_count", len(violations)),
            "violations": violations[:200],
        }

    async def run_erc(self) -> dict[str, Any]:
        bridge, AltiumError = await self._bridge()
        try:
            await bridge.send_command_async("generic.run_erc", {})
            raw = await bridge.send_command_async(
                "generic.get_erc_violations", {})
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None
        raw = raw or {}
        violations = raw.get("violations", raw.get("items", [])) or []
        return {
            "ok": True,
            "source": "altium",
            "violation_count": raw.get("violation_count", len(violations)),
            "violations": violations[:200],
        }



_BACKENDS = {
    "altium": AltiumBackend,
}


def resolve_backend(name: Optional[str] = None):
    """Return the adapter for ``name`` (or the active backend if None).

    Altium Agent drives Altium only, so the one adapter is Altium's; the
    resolver is kept so an unknown name is still refused rather than
    silently reviewing whatever is open.

    A NAME THIS DOES NOT RECOGNISE IS REFUSED. It used to fall through
    to Altium, which turns a misspelled backend into a full review of
    whichever design Altium happens to have open: plausible parts,
    plausible nets, wrong document, and a report that says nothing went
    wrong. Reviewing the wrong design silently is worse than not
    reviewing at all.
    """
    key = (name or active_backend_name()).strip().lower()
    if key in _BACKENDS:
        return _BACKENDS[key]()
    if not name:
        # No explicit request: an unrecognised ambient value, including
        # "both", means take the default.
        return AltiumBackend()
    raise BackendUnavailableError(
        f"unknown backend {name!r}. Valid names are "
        f"{', '.join(sorted(_BACKENDS))}. Refusing rather than falling "
        f"back, because the fallback would review a different design "
        f"and report success")
