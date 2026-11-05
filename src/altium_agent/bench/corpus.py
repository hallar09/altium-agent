# SPDX-License-Identifier: Apache-2.0
"""The benchmark's boards: finished designs laid out by people.

The manifest (benchmarks/boards.json in the repository) names each board's
project and PcbDoc, which parts a brief would pin, and a difficulty tier.
The designs are only ever read; every run works on copies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..offline.files.pcbdoc import PcbDocument, read_pcbdoc
from ..placement.footprints import footprints_of
from ..placement.placer import is_connector, is_mounting_hole

#: The manifest that ships with the repository.
DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "benchmarks" / "boards.json"


@dataclass
class BoardSpec:
    id: str
    project: Path
    pcb: str
    tier: int = 1
    fixed: str | list[str] = "auto"
    extra_fixed: list[str] = field(default_factory=list)
    notes: str = ""
    antennas: dict[str, str] = field(default_factory=dict)   # place_plan's antenna answers
    antennas_source: str = ""

    @property
    def pcb_path(self) -> Path:
        return self.project.parent / self.pcb

    def read(self) -> PcbDocument:
        return read_pcbdoc(self.pcb_path)


def load(manifest: Path | None = None, ids: list[str] | None = None,
         max_tier: int | None = None) -> list[BoardSpec]:
    """The boards in ``manifest``, narrowed to ``ids`` and ``max_tier``.

    An id that matches no board is an error, not a silent skip.
    """
    path = Path(manifest or DEFAULT_MANIFEST)
    data = json.loads(path.read_text(encoding="utf-8"))
    specs = [BoardSpec(id=b["id"], project=Path(b["project"]), pcb=b["pcb"], tier=int(b.get("tier", 1)),
                       fixed=b.get("fixed", "auto"), extra_fixed=list(b.get("extra_fixed") or []),
                       notes=b.get("notes", ""), antennas=dict(b.get("antennas") or {}),
                       antennas_source=b.get("antennas_source", "")) for b in data["boards"]]
    if ids:
        known = {s.id for s in specs}
        missing = [i for i in ids if i not in known]
        if missing:
            raise SystemExit(f"not in {path.name}: {', '.join(missing)} (known: {', '.join(sorted(known))})")
        specs = [s for s in specs if s.id in ids]
    if max_tier is not None:
        specs = [s for s in specs if s.tier <= max_tier]
    return specs


def fixed_parts(doc: PcbDocument, spec: BoardSpec) -> list[str]:
    """The parts a brief would pin: the manifest's list, or (``auto``) the
    parts the designer locked, connectors, mounting holes and parts with no
    connected pin (logos, fiducials); plus ``extra_fixed``."""
    fps = footprints_of(doc)
    if spec.fixed == "auto":
        refs = [f.ref for f in fps if f.locked or is_connector(f) or is_mounting_hole(f)
                or not any(p.net for p in f.pins)]
    else:
        refs = list(spec.fixed)
    on_board = {f.ref.upper(): f.ref for f in fps}
    unknown = [r for r in list(refs) + spec.extra_fixed if r.upper() not in on_board]
    if unknown:
        raise SystemExit(f"{spec.id}: fixed parts not on the board: {', '.join(unknown)}")
    seen: dict[str, str] = {}
    for r in list(refs) + spec.extra_fixed:
        seen.setdefault(r.upper(), on_board[r.upper()])
    return sorted(seen.values())
