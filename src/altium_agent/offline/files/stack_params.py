# SPDX-License-Identifier: Apache-2.0
# Altium Agent (2026): see docs/PROVENANCE.md
"""Build Altium's layer-stack parameter text from a plain layer list.

``Board.MasterLayerStack.Import_FromParameters`` replaces the whole stack in
one call from the same ``|KEY=VALUE`` text Altium keeps in the board record
(the ``V9_*`` keys of ``Board6/Data``). That is the only route to a stack
the layer-by-layer tools cannot make: exact fractional thicknesses, named
layers and any count of inner copper, in one write.

Ported from the Kame FMU sample's ``automation/bridge/stackup.py``, which
drove the same call on AD26 (30352 characters, imported). Everything in the
saved stack outside the layer list is kept as saved; inside it:

* surface layers above Top and below Bottom are kept as saved,
* Top and Bottom copper are kept with their thickness (and name) updated,
* everything between them is replaced by the plan's dielectrics and inner
  copper, inner copper on Altium's mid-layer ids (MidLayer1 = 16777218 ...)
  and dielectrics on 17039361 ... in stack order, each with a fresh GUID.

Inner copper is SIGNAL copper (MidLayerN). Internal plane layers are not
written: the ids and keys for them are unproven, and a GND "plane" as a
signal layer carrying a pour is what every board built here has used.

``Export_ToParameters``, the obvious way to get the current text, crashes
Altium from a script, so the current stack is read from the SAVED file.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import olefile

from ...units import MM_PER_MIL
from .records import decode_text, iter_records

TOP_ID = 16777217
BOTTOM_ID = 16842751
MID1_ID = 16777218
DIEL1_ID = 17039361
MAX_MID_LAYERS = 30

#: Altium's DIELTYPE codes (read off boards Altium saved: Core-009 is 1,
#: PP-006 is 2, Solder Resist is 3).
DIELTYPE = {"core": "1", "prepreg": "2"}

_LAYER_KEY = re.compile(r"^V9_STACK_LAYER(\d+)_(.*)$")


def read_stack_params(pcb_path: str | Path) -> str:
    """The ``V9_*`` items of a saved board record, in file order, '|'-joined."""
    with olefile.OleFileIO(str(pcb_path)) as ole:
        if not ole.exists("Board6/Data"):
            raise ValueError(f"{pcb_path} has no Board6/Data stream")
        raw = ole.openstream("Board6/Data").read()
    seen: set[str] = set()
    items: list[str] = []
    for rec in iter_records(raw):
        if rec.kind != 0:
            continue
        for item in decode_text(rec.payload).split("|"):
            key = item.partition("=")[0]
            if key.startswith("V9_") and key not in seen:
                seen.add(key)
                items.append(item)
    if not items:
        raise ValueError(f"{pcb_path} holds no V9 layer-stack keys")
    return "|".join(items)


def _parse(params: str):
    other: list[str] = []
    layers: dict[int, list[tuple[str, str]]] = {}
    start = None
    for item in params.split("|"):
        if not item:
            continue
        key, _, val = item.partition("=")
        m = _LAYER_KEY.match(key)
        if m:
            if start is None:
                start = len(other)
            layers.setdefault(int(m.group(1)), []).append((m.group(2), val))
        else:
            other.append(item)
    if start is None:
        raise ValueError("no V9_STACK_LAYER keys in the saved stack")
    return other, layers, start


def _get(fields: list[tuple[str, str]], suffix: str) -> str | None:
    return next((v for s, v in fields if s == suffix), None)


def _mil(mm: float) -> str:
    return f"{float(mm) / MM_PER_MIL:.4f}mil"


def _guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def validate_plan(layers: list[dict[str, Any]]) -> list[str]:
    """Problems with a layer list, empty when it can be built.

    Copper and dielectric alternate, copper first and last. Copper needs
    ``thickness_mm``; a dielectric needs ``kind`` (core|prepreg),
    ``height_mm`` and ``er``.
    """
    problems: list[str] = []
    if len(layers) < 3:
        problems.append("a stack needs at least copper, dielectric, copper")
        return problems
    for i, lyr in enumerate(layers):
        want = "copper" if i % 2 == 0 else "dielectric"
        kind = str(lyr.get("type", "")).lower()
        if kind != want:
            problems.append(f"layer {i}: expected {want}, got {kind or 'nothing'} "
                            "(copper and dielectric alternate, copper first and last)")
            continue
        if kind == "copper":
            t = lyr.get("thickness_mm")
            if not isinstance(t, (int, float)) or not 0.005 <= t <= 0.2:
                problems.append(f"layer {i} ({lyr.get('name', 'copper')}): thickness_mm "
                                "must be 0.005..0.2 (0.035 = 1 oz)")
        else:
            if str(lyr.get("kind", "")).lower() not in DIELTYPE:
                problems.append(f"layer {i}: dielectric kind must be core or prepreg")
            h = lyr.get("height_mm")
            if not isinstance(h, (int, float)) or not 0.02 <= h <= 3.0:
                problems.append(f"layer {i}: height_mm must be 0.02..3.0")
            er = lyr.get("er")
            if not isinstance(er, (int, float)) or not 1.0 <= er <= 15.0:
                problems.append(f"layer {i}: er must be 1..15")
    n_copper = (len(layers) + 1) // 2
    if n_copper - 2 > MAX_MID_LAYERS:
        problems.append(f"{n_copper - 2} inner copper layers; Altium allows {MAX_MID_LAYERS}")
    return problems


def build_stack_params(current: str, layers: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The full parameter text for ``layers``, and the layer names in order."""
    problems = validate_plan(layers)
    if problems:
        raise ValueError("; ".join(problems))
    other, saved, start = _parse(current)
    order = [saved[i] for i in sorted(saved)]
    ids = [int(_get(f, "LAYERID") or 0) for f in order]
    try:
        top = ids.index(TOP_ID)
        bot = ids.index(BOTTOM_ID)
    except ValueError as exc:
        raise ValueError(f"saved stack has no Top/Bottom copper (layer ids {ids})") from exc

    # Substack context keys ("{GUID}CONTEXT", "{GUID}USEDBYPRIMS") are copied
    # from Top for every new layer, USEDBYPRIMS cleared.
    ctx = [(s, "FALSE" if s.endswith("USEDBYPRIMS") else v)
           for s, v in order[top] if s.startswith("{")]

    copper = layers[0::2]
    diels = layers[1::2]

    def outer(fields, plan):
        out = []
        for s, v in fields:
            if s == "COPTHICK":
                v = _mil(plan["thickness_mm"])
            elif s == "NAME" and plan.get("name"):
                v = str(plan["name"])
            out.append((s, v))
        return out

    new = order[:top] + [outer(order[top], copper[0])]
    for i, d in enumerate(diels):
        new.append(ctx + [
            ("ID", _guid()),
            ("NAME", str(d.get("name") or f"Dielectric {i + 1}")),
            ("LAYERID", str(DIEL1_ID + i)),
            ("USEDBYPRIMS", "FALSE"),
            ("DIELTYPE", DIELTYPE[str(d["kind"]).lower()]),
            ("DIELCONST", f"{float(d['er']):.3f}"),
            ("DIELHEIGHT", _mil(d["height_mm"])),
            ("DIELMATERIAL", str(d.get("material") or ("Core" if d["kind"] == "core" else "Prepreg"))),
        ])
        if i < len(copper) - 2:
            c = copper[i + 1]
            new.append(ctx + [
                ("ID", _guid()),
                ("NAME", str(c.get("name") or f"Mid-Layer {i + 1}")),
                ("LAYERID", str(MID1_ID + i)),
                ("USEDBYPRIMS", "TRUE"),
                ("COPTHICK", _mil(c["thickness_mm"])),
                ("COMPONENTPLACEMENT", "0"),
            ])
    new += [outer(order[bot], copper[-1])] + order[bot + 1:]

    stack_items = [f"V9_STACK_LAYER{n}_{s}={v}" for n, fields in enumerate(new) for s, v in fields]
    names = [_get(f, "NAME") or "?" for f in new]
    return "|".join(other[:start] + stack_items + other[start:]), names


def removed_layers(current: str, layers: list[dict[str, Any]]) -> list[str]:
    """Copper of the saved stack that the plan does not keep (planes, or mid
    layers past the plan's count). Objects on them would be orphaned."""
    _other, saved, _start = _parse(current)
    keep = {TOP_ID, BOTTOM_ID} | {MID1_ID + i for i in range(max(0, (len(layers) + 1) // 2 - 2))}
    out = []
    for i in sorted(saved):
        lid = int(_get(saved[i], "LAYERID") or 0)
        if lid >> 16 in (0x0100, 0x0101) and lid not in keep:
            out.append(_get(saved[i], "NAME") or str(lid))
    return out


def board_thickness_mm(layers: list[dict[str, Any]]) -> float:
    return round(sum(float(l.get("thickness_mm") or l.get("height_mm") or 0) for l in layers), 4)
