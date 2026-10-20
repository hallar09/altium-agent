# SPDX-License-Identifier: Apache-2.0
"""A checkpoint per stage, and undo to any stage (Altium Agent).

Entering a stage snapshots the project folder (the content-addressed
checkpoint store, so unchanged files cost nothing) and records the
checkpoint in the session journal as an ``artifact`` of kind
``checkpoint:<stage>``. Undo to a stage restores that snapshot, after a
safety checkpoint of the current state, and records a ``rewind`` so the
journal's stage map matches the files again.

The snapshot is of the files on disk: Altium's unsaved edits are not in it,
so save before entering a stage.
"""

from __future__ import annotations

from pathlib import Path

from .session import STAGES, SessionJournal

CHECKPOINT_KIND = "checkpoint:"


def _store():
    from ..checkpoint import CheckpointStore
    from ..config import get_config

    return CheckpointStore(get_config().workspace_dir / "checkpoints")


def session_project(journal: SessionJournal) -> Path | None:
    """The project a session works on: its latest ``project`` artifact."""
    for ev in reversed(journal.events()):
        if ev.kind == "artifact" and ev.payload.get("kind") == "project" and ev.payload.get("path"):
            return Path(ev.payload["path"])
    return None


def session_profile(journal: SessionJournal) -> str | None:
    for ev in reversed(journal.events()):
        if ev.kind == "artifact" and ev.payload.get("kind") == "profile" and ev.payload.get("path"):
            return ev.payload["path"]
    return None


def project_for(journal: SessionJournal | None, project: str | None = None) -> Path:
    from ..offline.context import resolve_project

    if project:
        return resolve_project(project)
    if journal is not None:
        p = session_project(journal)
        if p is not None:
            return p
    return resolve_project(None)


def take(journal: SessionJournal, stage: str, project: Path) -> dict:
    """Snapshot ``project``'s folder for the start of ``stage`` and journal it."""
    info = _store().create(project.parent, project_file=project.name,
                           label=f"stage {stage} ({journal.session_id})")
    journal.artifact(info.id, kind=CHECKPOINT_KIND + stage)
    return {"checkpoint": info.summary(),
            "note": "Files on disk only: Altium's unsaved edits are not in it."}


def stage_checkpoints(journal: SessionJournal) -> dict[str, str]:
    """stage -> the latest checkpoint id taken on entering it."""
    out: dict[str, str] = {}
    for ev in journal.events():
        k = ev.payload.get("kind", "") if ev.kind == "artifact" else ""
        if k.startswith(CHECKPOINT_KIND):
            out[k[len(CHECKPOINT_KIND):]] = ev.payload.get("path", "")
    return out


def undo_files(journal: SessionJournal, stage: str, project: Path) -> dict:
    """Restore the files to ``stage``'s checkpoint (after a safety checkpoint) and rewind."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    cps = stage_checkpoints(journal)
    cid = cps.get(stage)
    if not cid:
        return {"ok": False, "error": f"no checkpoint was taken on entering {stage}",
                "stages_with_checkpoints": sorted(cps, key=STAGES.index)}
    store = _store()
    safety = store.create(project.parent, project_file=project.name,
                          label=f"before undo to {stage} ({journal.session_id})")
    restored = store.restore(cid, project.parent, prune_added=True)
    journal.rewind(stage, checkpoint_id=cid, safety_checkpoint_id=safety.id)
    return {"ok": True, "stage": stage, "checkpoint_id": cid, "restored": restored,
            "safety_checkpoint": safety.summary(),
            "note": f"Redo is app_restore_checkpoint {safety.id} (prune_added=true)."}
