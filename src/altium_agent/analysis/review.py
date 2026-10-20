# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/review.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Layout review heuristics that combine schematic intent with PCB geometry."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from ..offline.files.pcbdoc import NONE16, PcbDocument

POWER_NET_RE = re.compile(r"^(\+?\d+V\d*|\d+V\d+|V(CC|DD|BAT|BUS|IN|SYS|REF|IO|CORE|DDQ?|USB|A|D)\w*|AVDD\w*|DVDD\w*|"
                          r"PVDD\w*|VDDIO\w*|\+?\d+(\.\d+)?V\w*|P\d+V\d+\w*)$", re.IGNORECASE)
GROUND_NET_RE = re.compile(r"^(A|D|P|E|S|CHASSIS_|SIG)?GND\w*$|^VSS\w*$|^0V$|^EARTH$", re.IGNORECASE)


def is_power_net(name: str) -> bool:
    return bool(POWER_NET_RE.match(name)) and not GROUND_NET_RE.match(name)


def is_ground_net(name: str) -> bool:
    return bool(GROUND_NET_RE.match(name))


def _prefix(designator: str) -> str:
    m = re.match(r"^[A-Za-z_]+", designator)
    return m.group(0).upper() if m else ""


def decoupling_review(pcb: PcbDocument, *, max_distance_mm: float = 3.0, min_ic_pins: int = 6,
                      ic_prefixes: tuple[str, ...] = ("U", "IC")) -> dict:
    """For every IC power pad, find the nearest capacitor pad on the same net (same side preferred).

    Flags IC power pins whose nearest decoupling capacitor is farther than ``max_distance_mm``,
    and power pins with no capacitor on the net at all."""
    pads_by_comp: dict[int, list] = defaultdict(list)
    for p in pcb.pads:
        if p.component != NONE16:
            pads_by_comp[p.component].append(p)
    cap_pads_by_net: dict[int, list] = defaultdict(list)
    for ci, pads in pads_by_comp.items():
        if _prefix(pcb.designator(ci)) == "C":
            for p in pads:
                if p.net != NONE16:
                    cap_pads_by_net[p.net].append((ci, p))

    issues, ok = [], 0
    for ci, pads in pads_by_comp.items():
        ref = pcb.designator(ci)
        if _prefix(ref) not in ic_prefixes or len(pads) < min_ic_pins:
            continue
        for p in pads:
            if p.net == NONE16:
                continue
            net = pcb.net_name(p.net)
            if not is_power_net(net):
                continue
            caps = cap_pads_by_net.get(p.net, [])
            if not caps:
                issues.append({"ic": ref, "pin": p.name, "net": net, "problem": "no capacitor on this net"})
                continue
            best = min(caps, key=lambda cp: math.hypot(cp[1].x - p.x, cp[1].y - p.y))
            dist = math.hypot(best[1].x - p.x, best[1].y - p.y)
            if dist > max_distance_mm:
                issues.append({"ic": ref, "pin": p.name, "net": net, "nearest_cap": pcb.designator(best[0]),
                               "distance_mm": round(dist, 2), "problem": f"nearest capacitor > {max_distance_mm} mm"})
            else:
                ok += 1
    issues.sort(key=lambda i: (i["ic"], -(i.get("distance_mm") or 1e9)))
    return {"max_distance_mm": max_distance_mm, "power_pins_ok": ok, "issues": issues[:200],
            "issue_count": len(issues),
            "note": "Heuristic: power nets recognised by name (VCC/VDD/3V3/...); capacitor = designator prefix C."}


def placement_overview(pcb: PcbDocument) -> dict:
    by_prefix: dict[str, dict] = defaultdict(lambda: {"top": 0, "bottom": 0})
    for c in pcb.components:
        by_prefix[_prefix(c.designator) or "?"][c.side] += 1
    bbox = pcb.outline_bbox()
    outside = []
    if bbox:
        for c in pcb.components:
            if not (bbox["min_x"] - 0.01 <= c.x_mm <= bbox["max_x"] + 0.01 and bbox["min_y"] - 0.01 <= c.y_mm <= bbox["max_y"] + 0.01):
                outside.append({"designator": c.designator, "x_mm": round(c.x_mm, 3), "y_mm": round(c.y_mm, 3)})
    rot = defaultdict(int)
    for c in pcb.components:
        r = round(c.rotation % 360, 3)
        rot["orthogonal" if r % 90 == 0 else "non-orthogonal"] += 1
    return {"by_prefix": dict(sorted(by_prefix.items())), "outside_board_outline": outside,
            "rotation": dict(rot), "locked": sum(1 for c in pcb.components if c.locked)}
