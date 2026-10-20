# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/outjob.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Reader for Altium Output Job files (.OutJob)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ini import IniFile


@dataclass
class OutputMedium:
    index: int
    name: str
    type: str  # Printer | Publish (PDF) | GeneratedFiles (folder structure) | Multimedia ...


@dataclass
class OutputEntry:
    index: int
    type: str
    name: str
    category: str
    enabled: bool
    document: str
    media: list[str] = field(default_factory=list)


@dataclass
class OutJob:
    path: Path
    variant: str
    configuration: str
    media: list[OutputMedium]
    outputs: list[OutputEntry]

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "variant": self.variant,
            "configuration": self.configuration,
            "media": [{"name": m.name, "type": m.type} for m in self.media],
            "outputs": [
                {"name": o.name, "type": o.type, "category": o.category, "enabled": o.enabled,
                 "document": o.document or "[Project Physical Documents]", "media": o.media}
                for o in self.outputs
            ],
        }


def read_outjob(path: Path) -> OutJob:
    ini = IniFile.read(path)
    group = ini.section("OutputGroup1")
    if group is None:
        return OutJob(path, "", "", [], [])
    media: list[OutputMedium] = []
    i = 1
    while f"OutputMedium{i}" in group:
        media.append(OutputMedium(i, group.get(f"OutputMedium{i}"), group.get(f"OutputMedium{i}_Type")))
        i += 1
    outputs: list[OutputEntry] = []
    i = 1
    while f"OutputType{i}" in group:
        entry = OutputEntry(
            index=i,
            type=group.get(f"OutputType{i}"),
            name=group.get(f"OutputName{i}"),
            category=group.get(f"OutputCategory{i}"),
            enabled=group.get(f"OutputEnabled{i}", "1") != "0",
            document=group.get(f"OutputDocumentPath{i}"),
        )
        for m in media:
            if group.get(f"OutputEnabled{i}_OutputMedium{m.index}", "0") not in ("", "0"):
                entry.media.append(m.name)
        outputs.append(entry)
        i += 1
    return OutJob(path, group.get("VariantName"), group.get("CurrentConfigurationName"), media, outputs)
