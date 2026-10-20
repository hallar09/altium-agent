# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/bom.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Bill of materials from the schematic, with project-variant support."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..offline.files.prjpcb import ProjectVariant
from .model import DesignModel, LogicalComponent

NO_BOM_KINDS = {1, 2, 4, 5}  # mechanical, graphical, net tie (no BOM), standard (no BOM)

_FIELD_PATTERNS = {
    "manufacturer": [r"^manufacturer( ?1)?$", r"^mfr( ?1)?$", r"^manufacturer name$"],
    "mpn": [r"^manufacturer part number( ?1)?$", r"^mpn( ?1)?$", r"^mfr part ?(number|#|no)?( ?1)?$",
            r"^part number$", r"^manufacturer_part_number$"],
    "supplier": [r"^supplier( ?1)?$"],
    "supplier_pn": [r"^supplier part number( ?1)?$", r"^supplier code$", r"^supplier pn( ?1)?$"],
    "value": [r"^value$"],
}


def _pick(params: dict[str, str], key: str) -> str:
    for pat in _FIELD_PATTERNS[key]:
        for name, value in params.items():
            if re.match(pat, name.strip(), re.IGNORECASE) and value and not value.startswith("="):
                return value.strip()
    return ""


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _find_variant(model: DesignModel, name: str | None) -> ProjectVariant | None:
    if not name or name.lower() in ("", "none", "[no variations]"):
        return None
    for v in model.project.variants:
        if v.name.lower() == name.lower() or v.description.lower() == name.lower():
            return v
    raise ValueError(f"variant '{name}' not found; available: {[v.name for v in model.project.variants]}")


def build_bom(model: DesignModel, *, variant: str | None = None, group_by: tuple[str, ...] = ("comment", "footprint", "mpn"),
              include_dnp: bool = True) -> dict:
    var = _find_variant(model, variant)
    not_fitted = {c.designator for c in var.changes if c.kind == "not_fitted"} if var else set()
    alternates = {c.designator: c.alternate_part for c in var.changes if c.kind == "alternate"} if var else {}

    rows: dict[tuple, dict] = {}
    excluded: list[str] = []
    for lc in model.components():
        if not lc.designator or "?" in lc.designator or lc.designator.startswith("<"):
            continue
        kind = lc.parts[0].kind if lc.parts else 0
        if kind in NO_BOM_KINDS:
            excluded.append(lc.designator)
            continue
        dnp = lc.designator in not_fitted or _is_dnp(lc)
        if dnp and not include_dnp:
            continue
        item = _bom_fields(lc)
        if lc.designator in alternates:
            item["comment"] = f"{item['comment']} (alt: {alternates[lc.designator]})"
            item["alternate_part"] = alternates[lc.designator]
        item["fitted"] = not dnp
        key = tuple(item.get(g, "") for g in group_by) + (item["fitted"],)
        row = rows.get(key)
        if row is None:
            row = dict(item)
            row["designators"] = []
            rows[key] = row
        row["designators"].append(lc.designator)

    lines = sorted(rows.values(), key=lambda r: (not r["fitted"], _natural(r["designators"][0])))
    for i, r in enumerate(lines, 1):
        r["designators"].sort(key=_natural)
        r["item"] = i
        r["quantity"] = len(r["designators"]) if r["fitted"] else 0
    return {
        "variant": var.name if var else "[No Variations]",
        "line_items": len(lines),
        "total_fitted_parts": sum(r["quantity"] for r in lines),
        "not_fitted": sorted({d for r in lines if not r["fitted"] for d in r["designators"]}, key=_natural),
        "missing_mpn": sorted({d for r in lines if r["fitted"] and not r["mpn"] for d in r["designators"]}, key=_natural),
        "excluded_non_bom": excluded,
        "rows": lines,
    }


def _is_dnp(lc: LogicalComponent) -> bool:
    for k, v in lc.parameters.items():
        if k.strip().lower() in ("dnp", "dnf", "do not populate", "not fitted") and v.strip().lower() in ("1", "t", "true", "yes", "dnp"):
            return True
    return lc.comment.strip().upper() in ("DNP", "DNF", "NF", "NC")


def _bom_fields(lc: LogicalComponent) -> dict:
    return {
        "comment": lc.comment,
        "description": lc.description,
        "footprint": lc.footprint,
        "lib_ref": lc.lib_ref,
        "value": _pick(lc.parameters, "value"),
        "manufacturer": _pick(lc.parameters, "manufacturer"),
        "mpn": _pick(lc.parameters, "mpn"),
        "supplier": _pick(lc.parameters, "supplier"),
        "supplier_pn": _pick(lc.parameters, "supplier_pn"),
    }


BOM_COLUMNS = ["item", "quantity", "designators", "comment", "value", "description", "footprint", "manufacturer",
               "mpn", "supplier", "supplier_pn", "lib_ref", "fitted"]


def bom_to_csv(bom: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c.replace("_", " ").title() for c in BOM_COLUMNS])
    for r in bom["rows"]:
        w.writerow([", ".join(r["designators"]) if c == "designators" else ("Yes" if r[c] else "No") if c == "fitted"
                    else r.get(c, "") for c in BOM_COLUMNS])
    return buf.getvalue()


def write_bom(bom: dict, path: Path) -> Path:
    path.write_text(bom_to_csv(bom), encoding="utf-8-sig", newline="")
    return path
