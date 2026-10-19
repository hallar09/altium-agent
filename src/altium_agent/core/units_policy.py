# SPDX-License-Identifier: Apache-2.0
"""Altium Agent's unit policy for tool parameters: millimetres, named.

The rule (docs/TOOL_NAMING.md): every length a tool takes is in
millimetres and its name says so (``x_mm``, ``width_mm``,
``clearance_mm``). Conversion to Altium's internal units happens inside
the tool, never in the caller.

eda-agent, which this project forks, speaks mils, and many of its
parameters carry no unit at all (``x``, ``size``, ``hole_size``). Those
tools are migrated in later phases; until then they are LEGACY and listed
in ``LEGACY_UNIT_TOOLS.json`` beside this module. The list only shrinks:
tests/test_units_policy.py fails if a tool not on it takes a mil or an
unsuffixed length, and fails if a tool on it has been migrated but not
removed.

This module classifies; it converts nothing. Conversion constants live
in ``altium_agent.units`` and only there.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..units import MILS_PER_MM, MM_PER_MIL

#: Suffixes that state a length unit.
MM_SUFFIXES = ("_mm",)
MIL_SUFFIXES = ("_mils", "_mil")

#: Parameter names that are lengths when they carry no suffix. Collected
#: from the forked surface: each one was a mil value in eda-agent.
_BARE_LENGTH_NAMES = frozenset({
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "dx", "dy",
    "width", "height", "length", "size", "radius", "diameter",
    "hole_size", "hole", "drill", "clearance", "gap", "spacing", "pitch",
    "margin", "offset", "grid", "trace_width", "via_size", "via_hole",
    "line_width", "track_width", "expansion", "thickness",
})

#: Names that end like a length but are not one.
_NOT_LENGTHS = frozenset({
    "board_thickness_oz", "copper_oz", "width_px", "height_px",
})


def unit_of(param: str) -> str:
    """``mm``, ``mil``, ``bare`` (an unsuffixed length) or ``none``."""
    name = param.lower()
    if name in _NOT_LENGTHS:
        return "none"
    if name.endswith(MM_SUFFIXES):
        return "mm"
    if name.endswith(MIL_SUFFIXES):
        return "mil"
    if name in _BARE_LENGTH_NAMES:
        return "bare"
    return "none"


@dataclass
class ToolUnits:
    """The length parameters of one tool, grouped by unit."""

    name: str
    mm: list[str] = field(default_factory=list)
    mil: list[str] = field(default_factory=list)
    bare: list[str] = field(default_factory=list)

    @property
    def conforms(self) -> bool:
        """True when every length is a named millimetre value."""
        return not self.mil and not self.bare

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "mm": self.mm, "mil": self.mil,
                "bare": self.bare, "conforms": self.conforms}


def classify(name: str, fn: Callable) -> ToolUnits:
    """Group a tool's parameters by the length unit their names state."""
    out = ToolUnits(name=name)
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return out
    for pname in params:
        kind = unit_of(pname)
        if kind == "mm":
            out.mm.append(pname)
        elif kind == "mil":
            out.mil.append(pname)
        elif kind == "bare":
            out.bare.append(pname)
    return out


def classify_many(tools: Iterable[tuple[str, Callable]]) -> dict[str, ToolUnits]:
    return {name: classify(name, fn) for name, fn in tools}


LEGACY_FILE = Path(__file__).with_name("LEGACY_UNIT_TOOLS.json")


def legacy_tools() -> set[str]:
    """Tools allowed, for now, to take mils or unsuffixed lengths."""
    try:
        data = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(data.get("tools", []))


# --- conversion helpers for tools written to the policy ---------------------

def mm_to_mils(value_mm: float) -> float:
    """A length in millimetres, in mils (for the bridge's mil payloads)."""
    return float(value_mm) * MILS_PER_MM


def mils_to_mm(value_mils: float) -> float:
    """A length in mils, in millimetres (for replies)."""
    return float(value_mils) * MM_PER_MIL


_MIL_KEY = re.compile(r"^(?P<stem>.+?)_mils?$")


def reply_to_mm(data: Any) -> Any:
    """Copy of a reply with every ``*_mil``/``*_mils`` key also in mm.

    Additive: the mil key stays so a caller reading either works, and a
    key that already has an ``_mm`` twin is left alone.
    """
    if isinstance(data, list):
        return [reply_to_mm(v) for v in data]
    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    for k, v in data.items():
        out[k] = reply_to_mm(v)
    for k, v in data.items():
        m = _MIL_KEY.match(k) if isinstance(k, str) else None
        if m and isinstance(v, (int, float)) and not isinstance(v, bool):
            twin = f"{m.group('stem')}_mm"
            if twin not in out:
                out[twin] = round(mils_to_mm(v), 6)
    return out
