# SPDX-License-Identifier: Apache-2.0
"""Session files: where an AI-driven placement or routing keeps its state.

A design session runs to hundreds of actions. Its state lives in a JSON
file, not in the conversation, so every tool call reads the current state
back, an action can be undone, a session survives a new chat, and the
result is a file the user can inspect. Sessions are written into the
outputs folder (never beside the design).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from ..atomicfile import replace_with_retry
from ..offline.context import output_dir_for, safe_path
from ..offline.errors import InvalidArgument

#: Undo steps kept per session.
HISTORY = 200


def now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def new(kind: str, owner: Path, name: str | None, data: dict[str, Any]) -> Path:
    """Create a session file for ``owner`` (a project or board path)."""
    folder = output_dir_for(owner, "sessions")
    stem = name or f"{kind}-{_dt.datetime.now(_dt.UTC):%Y%m%d-%H%M%S}"
    path = folder / f"{stem}.json"
    if path.exists():
        raise InvalidArgument(f"a session called {stem!r} already exists ({path}); pick another name")
    save(path, {"kind": kind, "version": 1, "created": now(), "history": [], **data})
    return path


def load(session: str | Path, kind: str) -> tuple[Path, dict[str, Any]]:
    path = safe_path(session, suffixes=(".json",))
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != kind:
        raise InvalidArgument(f"{path.name} is not a {kind} session (it is {data.get('kind')!r})")
    return path, data


def save(path: Path, data: dict[str, Any]) -> None:
    data["updated"] = now()
    data["history"] = data.get("history", [])[-HISTORY:]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    replace_with_retry(tmp, path)
