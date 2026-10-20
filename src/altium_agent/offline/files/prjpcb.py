# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/prjpcb.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Reader for Altium project files (.PrjPcb)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from .ini import IniFile, IniSection

SOURCE_KINDS = {".schdoc": "SCH", ".pcbdoc": "PCB", ".schlib": "SCHLIB", ".pcblib": "PCBLIB",
                ".outjob": "OUTJOB", ".intlib": "INTLIB", ".dblib": "DBLIB", ".pcbdwf": "DRAFTSMAN",
                ".harness": "HARNESS", ".annotation": "ANNOTATION", ".bomdoc": "BOM"}


@dataclass
class ProjectDocument:
    path: Path  # resolved absolute path
    relative: str  # as written in the project file
    kind: str
    exists: bool


@dataclass
class VariantChange:
    designator: str
    kind: str  # "not_fitted" | "alternate" | "fitted" | "other"
    alternate_part: str = ""
    unique_id: str = ""


@dataclass
class ProjectVariant:
    name: str
    description: str
    changes: list[VariantChange] = field(default_factory=list)
    parameter_changes: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AltiumProject:
    path: Path
    documents: list[ProjectDocument]
    parameters: dict[str, str]
    variants: list[ProjectVariant]
    design: dict[str, str]
    output_groups: list[dict[str, str]]
    configurations: list[str]
    error_levels: dict[str, str]

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def directory(self) -> Path:
        return self.path.parent

    def docs(self, kind: str) -> list[ProjectDocument]:
        return [d for d in self.documents if d.kind == kind]

    @property
    def schematic_docs(self) -> list[Path]:
        return [d.path for d in self.docs("SCH") if d.exists]

    @property
    def pcb_docs(self) -> list[Path]:
        return [d.path for d in self.docs("PCB") if d.exists]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for d in self.documents:
            counts[d.kind] = counts.get(d.kind, 0) + 1
        return {
            "name": self.name,
            "path": str(self.path),
            "document_counts": counts,
            "documents": [
                {"path": str(d.path), "relative": d.relative, "kind": d.kind, "exists": d.exists}
                for d in self.documents
            ],
            "parameters": self.parameters,
            "variants": [
                {"name": v.name, "description": v.description,
                 "not_fitted": [c.designator for c in v.changes if c.kind == "not_fitted"],
                 "alternate": [{"designator": c.designator, "part": c.alternate_part}
                               for c in v.changes if c.kind == "alternate"],
                 "parameter_changes": len(v.parameter_changes)}
                for v in self.variants
            ],
            "configurations": self.configurations,
            "hierarchy_mode": self.design.get("HierarchyMode", ""),
            "output_path": self.design.get("OutputPath", ""),
        }


def _resolve(project_dir: Path, rel: str) -> Path:
    # Paths in project files use backslashes and may be relative or absolute.
    p = PureWindowsPath(rel)
    if p.is_absolute():
        return Path(str(p))
    return (project_dir / Path(*p.parts)).resolve()


def _variant_change(raw: str) -> VariantChange | None:
    fields: dict[str, str] = {}
    for part in raw.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            fields[k.strip().lower()] = v.strip()
    designator = fields.get("designator", "")
    if not designator:
        return None
    kind_code = fields.get("kind", "")
    kind = {"0": "fitted", "1": "not_fitted", "2": "alternate"}.get(kind_code, "other")
    if fields.get("alternatepart"):
        kind = "alternate" if kind != "not_fitted" else kind
    return VariantChange(designator, kind, fields.get("alternatepart", ""), fields.get("uniqueid", ""))


def _read_variant(section: IniSection) -> ProjectVariant:
    v = ProjectVariant(section.get("Description") or section.name, section.get("Description"))
    for key, value in section.items():
        kl = key.lower()
        if kl.startswith("variation") and kl[9:].isdigit():
            change = _variant_change(value)
            if change:
                v.changes.append(change)
        elif kl.startswith("paramvariation") and kl[14:].isdigit():
            fields = {}
            for part in value.split("|"):
                if "=" in part:
                    k, val = part.split("=", 1)
                    fields[k.strip()] = val.strip()
            v.parameter_changes.append(fields)
    return v


def read_project(path: Path) -> AltiumProject:
    path = path.resolve()
    ini = IniFile.read(path)
    project_dir = path.parent

    documents: list[ProjectDocument] = []
    for sec in ini.numbered("Document"):
        rel = sec.get("DocumentPath")
        if not rel:
            continue
        resolved = _resolve(project_dir, rel)
        kind = SOURCE_KINDS.get(resolved.suffix.lower(), resolved.suffix.lstrip(".").upper() or "OTHER")
        documents.append(ProjectDocument(resolved, rel, kind, resolved.exists()))

    parameters: dict[str, str] = {}
    for sec in ini.numbered("Parameter"):
        name = sec.get("Name")
        if name:
            parameters[name] = sec.get("Value")

    variants = [_read_variant(s) for s in ini.numbered("ProjectVariant")]
    design = ini.section("Design")
    output_groups = [s.as_dict() for s in ini.numbered("OutputGroup")]
    configurations = [s.get("Name") for s in ini.numbered("Configuration") if s.get("Name")]
    erc = ini.section("Electrical Rules Check")

    return AltiumProject(
        path=path,
        documents=documents,
        parameters=parameters,
        variants=variants,
        design=design.as_dict() if design else {},
        output_groups=output_groups,
        configurations=configurations,
        error_levels=erc.as_dict() if erc else {},
    )
