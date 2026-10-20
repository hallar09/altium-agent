# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Brass contributors
# Adapted from Brass src/board/antenna.ts (TypeScript, Apache-2.0) for Altium
# Agent (2026): see docs/PROVENANCE.md.
"""RF modules with a printed antenna, and the keep-out their antenna needs.

The copper under a printed antenna and past its end must stay clear on every
layer, or the antenna detunes; the module sits with its antenna end on a board
edge. How big that area is comes from the module's datasheet, so the size is
ASKED for, never guessed: "top 6 5" means the antenna end of the footprint
(as drawn in the library), how far the antenna reaches into the module, and
how far the clear area extends past it, in mm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .footprints import Box, Footprint

_RF = re.compile(
    r"wi-?fi|802\.11|bluetooth|\bble\b|zigbee|\blora|802\.15|wireless|\bgnss\b|\bantenna\b|wroom|wrover|"
    r"\besp-?(?:01|07|12|32)|spwf|cc3\d{3}mod|wl18\d\dmod|\bnina-|\bxbee|\brn4\d{3}|\bbgm\d|\bmgm\d", re.IGNORECASE)


@dataclass
class AntennaKeepout:
    end: str        # top | bottom | left | right (of the footprint as drawn)
    depth_mm: float
    beyond_mm: float = 0.0

    def to_dict(self) -> dict:
        return {"end": self.end, "depth_mm": self.depth_mm, "beyond_mm": self.beyond_mm}


@dataclass
class RfModule:
    ref: str
    label: str
    why: str


def rf_modules(fps: list[Footprint], descriptions: dict[str, str] | None = None) -> list[RfModule]:
    """Parts that look like an RF module (or an antenna) with a printed antenna."""
    from .placer import is_connector

    descriptions = {k.upper(): v for k, v in (descriptions or {}).items()}
    out = []
    for f in fps:
        if is_connector(f) or f.through_hole:
            continue
        text = " ".join(t for t in (descriptions.get(f.ref.upper(), ""), f.comment, f.footprint) if t)
        antenna_ref = re.match(r"^(ANT|AE)\d", f.ref, re.IGNORECASE) is not None
        m = _RF.search(text)
        if not antenna_ref and not (m and (len(f.pins) >= 6 or re.search("module", text, re.IGNORECASE))):
            continue
        out.append(RfModule(f.ref, f.comment or f.footprint, m.group(0) if m else f.ref))
    return out


def antenna_question(m: RfModule) -> str:
    return (f"{m.ref} ({m.label}) looks like an RF module with a printed antenna (\"{m.why}\"). "
            "How big must the copper keep-out at its antenna be? From the module datasheet: the "
            "antenna end of the footprint as drawn in the library (top, bottom, left or right), how "
            "far the antenna reaches into the module, and how far the clear area extends past it, "
            "in mm, e.g. \"top 6 5\". Answer \"none\" for a module with an antenna connector.")


def parse_antenna_answer(text: str) -> AntennaKeepout | str | None:
    """'top 6 5' -> AntennaKeepout, 'none' -> 'none', anything else -> None."""
    t = text.strip().lower()
    if t in ("none", "no"):
        return "none"
    m = re.match(r"^(top|bottom|left|right)[\s,]+(\d+(?:\.\d+)?)(?:[\s,]+(\d+(?:\.\d+)?))?\s*(?:mm)?$", t)
    if not m:
        return None
    depth = float(m.group(2))
    beyond = float(m.group(3) or 0)
    return AntennaKeepout(m.group(1), depth, beyond) if depth > 0 else None


def antenna_zone(box: Box, k: AntennaKeepout) -> tuple[Box, tuple[float, float]]:
    """The keep-out in the footprint's own frame, and the middle of its far edge."""
    cx, cy = (box.min_x + box.max_x) / 2, (box.min_y + box.max_y) / 2
    if k.end == "top":
        return Box(box.min_x, box.max_y - k.depth_mm, box.max_x, box.max_y + k.beyond_mm), (cx, box.max_y + k.beyond_mm)
    if k.end == "bottom":
        return Box(box.min_x, box.min_y - k.beyond_mm, box.max_x, box.min_y + k.depth_mm), (cx, box.min_y - k.beyond_mm)
    if k.end == "right":
        return Box(box.max_x - k.depth_mm, box.min_y, box.max_x + k.beyond_mm, box.max_y), (box.max_x + k.beyond_mm, cy)
    return Box(box.min_x - k.beyond_mm, box.min_y, box.min_x + k.depth_mm, box.max_y), (box.min_x - k.beyond_mm, cy)
