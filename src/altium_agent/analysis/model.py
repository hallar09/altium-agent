# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro analysis/model.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Project-level design model assembled from the offline readers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..offline.errors import FileFormatError
from ..offline.files.pcbdoc import PcbDocument, read_pcbdoc
from ..offline.files.prjpcb import AltiumProject, read_project
from ..offline.files.sch_netlist import Netlist, build_netlist
from ..offline.files.schdoc import SchComponent, SchDocument, read_schdoc


@dataclass
class LogicalComponent:
    """One designator, with its schematic parts merged (U1A + U1B -> U1)."""

    designator: str
    comment: str
    footprint: str
    lib_ref: str
    description: str
    parameters: dict[str, str]
    parts: list[SchComponent]
    sheets: list[str]

    @property
    def pin_count(self) -> int:
        return sum(len(p.pins) for p in self.parts)

    def to_dict(self) -> dict:
        return {
            "designator": self.designator, "comment": self.comment, "footprint": self.footprint,
            "lib_ref": self.lib_ref, "description": self.description, "parameters": self.parameters,
            "parts_placed": len(self.parts), "pins": self.pin_count, "sheets": self.sheets,
        }


@dataclass
class DesignModel:
    project: AltiumProject
    sheets: list[SchDocument]
    sheet_errors: list[str]

    def components(self) -> list[LogicalComponent]:
        merged: dict[str, LogicalComponent] = {}
        order: list[str] = []
        for sheet in self.sheets:
            for c in sheet.components:
                key = c.designator or f"<unnamed@{sheet.path.name}:{c.index}>"
                if key not in merged:
                    merged[key] = LogicalComponent(key, c.comment, c.footprint, c.lib_ref, c.description,
                                                   dict(c.parameters), [], [])
                    order.append(key)
                lc = merged[key]
                lc.parts.append(c)
                if sheet.path.name not in lc.sheets:
                    lc.sheets.append(sheet.path.name)
                for k, v in c.parameters.items():
                    lc.parameters.setdefault(k, v)
        return [merged[k] for k in order]

    def unique_path(self, designator: str) -> str:
        """Altium's hierarchical unique id (\\SHEETSYMBOLID\\...\\COMPONENTID) used by project variants."""
        parent: dict[str, tuple[str, str]] = {}
        for s in self.sheets:
            for sym in s.sheet_symbols:
                child = Path(sym.file_name.replace("\\", "/")).name.lower()
                parent.setdefault(child, (s.path.name.lower(), sym.unique_id))
        for s in self.sheets:
            for c in s.components:
                if c.designator.lower() == designator.lower():
                    chain, sheet, seen = [c.unique_id], s.path.name.lower(), set()
                    while sheet in parent and sheet not in seen:
                        seen.add(sheet)
                        sheet, uid = parent[sheet]
                        chain.append(uid)
                    return "\\" + "\\".join(reversed(chain))
        raise KeyError(designator)

    def netlist(self) -> Netlist:
        return build_netlist([s.path for s in self.sheets], self.project.design.get("HierarchyMode", "0"),
                             search_dirs=[self.project.directory],
                             power_names_first=self.project.design.get("PowerPortNamesTakePriority", "0") == "1")

    def pcb(self, index: int = 0) -> PcbDocument | None:
        docs = self.project.pcb_docs
        if not docs:
            return None
        if index >= len(docs):
            raise FileFormatError(f"project has {len(docs)} PcbDoc(s); index {index} is out of range")
        return read_pcbdoc(docs[index])


def load_model(project_path: Path) -> DesignModel:
    project = read_project(project_path)
    sheets: list[SchDocument] = []
    errors: list[str] = []
    for p in project.schematic_docs:
        try:
            sheets.append(read_schdoc(p))
        except Exception as exc:  # noqa: BLE001 - one bad sheet must not hide the rest
            errors.append(f"{p.name}: {exc}")
    missing = [d.relative for d in project.documents if not d.exists and d.kind in ("SCH", "PCB")]
    errors.extend(f"missing document: {m}" for m in missing)
    return DesignModel(project, sheets, errors)
