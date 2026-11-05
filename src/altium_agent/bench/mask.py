# SPDX-License-Identifier: Apache-2.0
"""Show a benchmark board to the tools as its task, not as the person drew it.

There is no PcbDoc writer, so a stripped board cannot be saved as a file.
Instead the benchmark copies the project and writes a mask file naming the
copy and what to take away; with ``ALTIUM_AGENT_BENCH_MASK`` pointing at
it, :func:`altium_agent.offline.files.pcbdoc.read_pcbdoc` (the one reader
every offline tool goes through) returns the masked board. Without the
variable nothing changes, and a path the mask does not name is read as is.

Mask file::

    {"boards": {"<absolute path of the copy's .PcbDoc>": {
        "strip_routing": true,
        "stage": ["U1", "C3", ...],          # parts moved beside the board
        "placements": [{"ref": ..., "x_mm": ..., ...}]   # applied last
    }}}
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from ..offline.files.pcbdoc import PcbDocument

ENV = "ALTIUM_AGENT_BENCH_MASK"


def _key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


@lru_cache(maxsize=4)
def _load(path: str, _mtime: int) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {_key(k): v for k, v in (data.get("boards") or {}).items()}


def entry_for(path: str | Path) -> dict | None:
    """The mask entry for a board path, or None (no mask, or not named)."""
    mask = os.environ.get(ENV)
    if not mask:
        return None
    m = Path(mask)
    try:
        boards = _load(str(m.resolve()), m.stat().st_mtime_ns)
    except FileNotFoundError:
        raise FileNotFoundError(f"{ENV} names {mask}, which does not exist") from None
    return boards.get(_key(path))


def apply(doc: PcbDocument, entry: dict) -> PcbDocument:
    """``doc`` as the mask entry describes it."""
    from .transform import apply_placements, stage_parts, strip_routing

    if entry.get("strip_routing"):
        doc = strip_routing(doc)
    if entry.get("stage"):
        doc = stage_parts(doc, list(entry["stage"]))
    if entry.get("placements"):
        doc = apply_placements(doc, list(entry["placements"]))
    doc.warnings = list(doc.warnings) + ["benchmark task: this board is masked (see ALTIUM_AGENT_BENCH_MASK)"]
    return doc


def write(path: Path, boards: dict[str, dict]) -> Path:
    """Write a mask file for ``boards`` ({pcbdoc path: entry})."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"boards": {str(Path(k).resolve()): v for k, v in boards.items()}}, indent=1),
                    encoding="utf-8")
    return path
