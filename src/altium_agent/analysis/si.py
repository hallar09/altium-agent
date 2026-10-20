# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/si.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Signal-integrity calculators: controlled impedance and current capacity.

Closed-form approximations (Hammerstad-Jensen microstrip, Cohn/IPC-2141 stripline, IPC-2221 current).
Typical accuracy is a few percent against a field solver for common geometries; use Altium's
Impedance Profile (Layer Stack Manager) for sign-off.
"""

from __future__ import annotations

from ..units import MM_PER_MIL

import math

from ..offline.files.pcbdoc import PcbDocument


def microstrip_z0(w: float, h: float, t: float, er: float) -> tuple[float, float]:
    """Surface microstrip, Hammerstad-Jensen with thickness correction. Units: any consistent length.
    Returns (Z0 ohm, effective permittivity)."""
    if w <= 0 or h <= 0:
        raise ValueError("width and height must be positive")
    if t > 0:
        dw = (t / math.pi) * (1 + math.log(4 * math.e / math.sqrt((t / h) ** 2 + (1 / math.pi / (w / t + 1.1)) ** 2)))
        w = w + dw * (1 + 1 / er) / 2
    u = w / h
    a = 1 + (1 / 49) * math.log((u**4 + (u / 52) ** 2) / (u**4 + 0.432)) + (1 / 18.7) * math.log(1 + (u / 18.1) ** 3)
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    e_eff = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)
    f = 6 + (2 * math.pi - 6) * math.exp(-((30.666 / u) ** 0.7528))
    z0_air = 60 * math.log(f / u + math.sqrt(1 + (2 / u) ** 2))
    return z0_air / math.sqrt(e_eff), e_eff


def stripline_z0(w: float, b: float, t: float, er: float) -> float:
    """Symmetric stripline (trace centred between planes separated by b). IPC-2141/Cohn form."""
    if w <= 0 or b <= 0:
        raise ValueError("width and plane spacing must be positive")
    return (60 / math.sqrt(er)) * math.log(4 * b / (0.67 * math.pi * (0.8 * w + t)))


def asymmetric_stripline_z0(w: float, h1: float, h2: float, t: float, er: float) -> float:
    """Offset stripline approximated as the parallel combination of two symmetric striplines."""
    z1 = stripline_z0(w, 2 * h1 + t, t, er)
    z2 = stripline_z0(w, 2 * h2 + t, t, er)
    return 2 * z1 * z2 / (z1 + z2)


def differential(z0: float, s: float, h: float, kind: str) -> float:
    """Edge-coupled differential impedance from single-ended Z0 (IPC-2141 coupling approximations)."""
    if kind == "microstrip":
        return 2 * z0 * (1 - 0.48 * math.exp(-0.96 * s / h))
    return 2 * z0 * (1 - 0.347 * math.exp(-2.9 * s / h))


def impedance(geometry: str, width_mm: float, height_mm: float, er: float, thickness_mm: float = 0.035,
              height2_mm: float | None = None, spacing_mm: float | None = None) -> dict:
    g = geometry.lower()
    if g == "microstrip":
        z0, e_eff = microstrip_z0(width_mm, height_mm, thickness_mm, er)
        result = {"z0_ohm": round(z0, 2), "e_eff": round(e_eff, 3)}
        ref_h = height_mm
    elif g == "stripline":
        if height2_mm is None or abs(height2_mm - height_mm) < 1e-9:
            z0 = stripline_z0(width_mm, 2 * height_mm + thickness_mm, thickness_mm, er)
        else:
            z0 = asymmetric_stripline_z0(width_mm, height_mm, height2_mm, thickness_mm, er)
        e_eff = er
        result = {"z0_ohm": round(z0, 2), "e_eff": er}
        ref_h = 2 * height_mm + thickness_mm if height2_mm is None else height_mm + height2_mm + thickness_mm
    else:
        raise ValueError("geometry must be 'microstrip' or 'stripline'")
    result["delay_ps_per_mm"] = round(math.sqrt(e_eff) / 299.792458 * 1000, 3)
    if spacing_mm:
        result["zdiff_ohm"] = round(differential(z0, spacing_mm, ref_h, "microstrip" if g == "microstrip" else "stripline"), 2)
    result.update({"geometry": g, "width_mm": width_mm, "height_mm": height_mm, "er": er, "thickness_mm": thickness_mm})
    if height2_mm is not None:
        result["height2_mm"] = height2_mm
    if spacing_mm:
        result["spacing_mm"] = spacing_mm
    return result


def solve_width(target_ohm: float, geometry: str, height_mm: float, er: float, thickness_mm: float = 0.035,
                height2_mm: float | None = None, spacing_mm: float | None = None, differential_target: bool = False) -> dict:
    """Bisection on width for a target single-ended (or differential) impedance."""
    key = "zdiff_ohm" if differential_target else "z0_ohm"
    lo, hi = 0.01, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        z = impedance(geometry, mid, height_mm, er, thickness_mm, height2_mm, spacing_mm)[key]
        if z > target_ohm:
            lo = mid
        else:
            hi = mid
    w = round((lo + hi) / 2, 4)
    res = impedance(geometry, w, height_mm, er, thickness_mm, height2_mm, spacing_mm)
    res["target_ohm"] = target_ohm
    res["solved_width_mm"] = w
    return res


def ipc2221_current(width_mm: float, copper_um: float = 35.0, temp_rise_c: float = 10.0, external: bool = True) -> dict:
    area_mil2 = (width_mm / MM_PER_MIL) * (copper_um / (MM_PER_MIL * 1000.0))
    k = 0.048 if external else 0.024
    amps = k * temp_rise_c**0.44 * area_mil2**0.725
    return {"current_a": round(amps, 3), "width_mm": width_mm, "copper_um": copper_um, "temp_rise_c": temp_rise_c,
            "layer": "external" if external else "internal", "standard": "IPC-2221 (conservative for wide traces)"}


def ipc2221_width_for_current(current_a: float, copper_um: float = 35.0, temp_rise_c: float = 10.0,
                              external: bool = True) -> dict:
    k = 0.048 if external else 0.024
    area_mil2 = (current_a / (k * temp_rise_c**0.44)) ** (1 / 0.725)
    width_mm = area_mil2 / (copper_um / (MM_PER_MIL * 1000.0)) * MM_PER_MIL
    return {"width_mm": round(width_mm, 4), "current_a": current_a, "copper_um": copper_um,
            "temp_rise_c": temp_rise_c, "layer": "external" if external else "internal"}


def board_geometry(pcb: PcbDocument, layer: str) -> dict:
    """Derive microstrip/stripline geometry for a copper layer from the board stack-up.

    Reference planes are taken as the nearest copper layers above/below (assumed solid planes)."""
    stack = pcb.stack
    def key(name: str) -> str:  # Altium Agent: 'Top Layer' == 'TopLayer' == 'toplayer'
        return "".join(name.split()).lower()

    idx = next((i for i, lyr in enumerate(stack) if lyr.kind == "copper" and key(lyr.name) == key(layer)), None)
    if idx is None:
        raise ValueError(f"copper layer '{layer}' not found; copper layers: {[lyr.name for lyr in pcb.copper_layers]}")
    lyr = stack[idx]

    def walk(direction: int) -> tuple[float, list[float], bool]:
        h, ers, i = 0.0, [], idx + direction
        while 0 <= i < len(stack):
            s = stack[i]
            if s.kind == "copper":
                return h, ers, True
            if s.kind == "dielectric" and s.dielectric_height_mm:
                h += s.dielectric_height_mm
                if s.dielectric_constant:
                    ers.append(s.dielectric_constant)
            i += direction
        return h, ers, False

    h_up, er_up, plane_up = walk(-1)
    h_dn, er_dn, plane_dn = walk(+1)
    t = lyr.copper_thickness_mm or 0.035
    if plane_up and plane_dn:
        ers = er_up + er_dn
        return {"geometry": "stripline", "height_mm": round(h_up, 4), "height2_mm": round(h_dn, 4),
                "er": round(sum(ers) / len(ers), 3) if ers else 4.2, "thickness_mm": round(t, 4)}
    h, ers = (h_dn, er_dn) if plane_dn else (h_up, er_up)
    return {"geometry": "microstrip", "height_mm": round(h, 4), "er": round(sum(ers) / len(ers), 3) if ers else 4.2,
            "thickness_mm": round(t, 4)}
