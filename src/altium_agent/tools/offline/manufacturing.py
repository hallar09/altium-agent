# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/manufacturing.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""BOM, OutJob execution and release packaging."""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ...analysis.bom import build_bom, write_bom
client = None  # live bridge: not used by the ported offline tools
from ...offline.errors import InvalidArgument
from ...offline.context import ToolClass
from ...offline.context import output_dir_for, resolve_project, safe_path
from .analysis import design_review
from .common import ProjectArg, model_for
from .registry import tool


@tool(ToolClass.READ, "manufacturing", "Generate BOM")
def bom_generate(
    project: ProjectArg = None,
    variant: Annotated[str | None, Field(description="Project variant name; omit for [No Variations].")] = None,
    include_dnp: Annotated[bool, Field(description="Keep not-fitted parts as zero-quantity lines.")] = True,
    write_csv: Annotated[bool, Field(description="Also write a CSV into the output folder.")] = False,
) -> dict:
    """Bill of materials grouped by comment + footprint + MPN, with variant DNP/alternate handling and a list of
    fitted parts that lack a manufacturer part number."""
    model = model_for(project)
    try:
        bom = build_bom(model, variant=variant, include_dnp=include_dnp)
    except ValueError as exc:
        raise InvalidArgument(str(exc)) from None
    if write_csv:
        out = output_dir_for(model.project.path, "bom")
        name = f"{model.project.name}_BOM{'_' + variant if variant else ''}.csv"
        bom["csv_path"] = str(write_bom(bom, out / name))
    return bom


def _project_output_dir(project_path: Path, design: dict) -> Path:
    configured = design.get("OutputPath", "")
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else (project_path.parent / p).resolve()
    return project_path.parent / f"Project Outputs for {project_path.stem}"


@tool(ToolClass.MANUFACTURING, "manufacturing", "Run an OutJob in Altium", idempotent=False, live=True)
def outjob_run(
    outjob: Annotated[str, Field(description="Path to the .OutJob file.")],
    medium: Annotated[str | None, Field(description="Output container name, e.g. 'Folder Structure' or 'PDF'. "
                                                    "Omit to run the job's default.")] = None,
    action: Annotated[str, Field(description="'Run' for generated files, 'PublishToPDF' for PDF containers.")] = "Run",
    project: ProjectArg = None,
) -> dict:
    """Generate outputs (Gerber, NC drill, pick-and-place, PDFs...) by running an OutJob in the live Altium.
    Lists the files that appeared in the project's output folder."""
    path = safe_path(outjob, suffixes=(".outjob",))
    if action not in ("Run", "PublishToPDF"):
        raise InvalidArgument("action must be 'Run' or 'PublishToPDF'")
    start = time.time()
    result = client.call("outjob.run", {"path": str(path), "medium": medium, "action": action}, timeout=900)
    produced = []
    try:
        model = model_for(project)
    except Exception:  # noqa: BLE001 - output listing is best effort without a project
        model = None
    out_dir = _project_output_dir(model.project.path, model.project.design) if model else None
    if out_dir and out_dir.exists():
        produced = [str(p) for p in out_dir.rglob("*") if p.is_file() and p.stat().st_mtime >= start - 2]
    return {"bridge": result, "output_dir": str(out_dir) if out_dir else None, "new_files": produced[:500],
            "new_file_count": len(produced)}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@tool(ToolClass.MANUFACTURING, "manufacturing", "Build a release package", idempotent=False)
def release_package(
    confirm: Annotated[bool, Field(description="Must be true: a human has reviewed the design and approves packaging.")],
    project: ProjectArg = None,
    outputs_dir: Annotated[str | None, Field(description="Folder with generated outputs. Defaults to the project's "
                                                         "Altium output folder.")] = None,
    variant: str | None = None,
    allow_warnings: Annotated[bool, Field(description="Package even when the review verdict is WARN.")] = True,
    revision: Annotated[str, Field(max_length=40, pattern=r"^[A-Za-z0-9._-]+$")] = "A",
) -> dict:
    """Zip the generated outputs with a BOM, the design-review report and a SHA-256 manifest. Refuses when the
    design review FAILs. Never sends anything to a fabricator."""
    if not confirm:
        raise InvalidArgument("release_package needs confirm=true after a human review of design_review results")
    project_path = resolve_project(project)
    model = model_for(str(project_path))
    review = design_review(str(project_path))
    if review["verdict"] == "FAIL" or (review["verdict"] == "WARN" and not allow_warnings):
        return {"packaged": False, "verdict": review["verdict"], "blockers": review["blockers"],
                "warnings": review["warnings"]}
    src = safe_path(outputs_dir) if outputs_dir else _project_output_dir(project_path, model.project.design)
    files = [p for p in src.rglob("*") if p.is_file()] if src.exists() else []
    if not files:
        raise InvalidArgument(f"no generated outputs in {src}", hint="Run outjob_run first (Fabrication/Assembly OutJobs).")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = output_dir_for(project_path, "release")
    bom = build_bom(model, variant=variant)
    bom_path = write_bom(bom, out / f"{model.project.name}_BOM.csv")
    review_path = out / f"{model.project.name}_design_review.json"
    review_path.write_text(json.dumps(review, indent=2), encoding="utf-8")
    zip_path = out / f"{model.project.name}_rev{revision}_{stamp}.zip"
    manifest = {"project": str(project_path), "revision": revision, "created": stamp, "verdict": review["verdict"],
                "variant": bom["variant"], "files": []}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            arc = f"outputs/{f.relative_to(src).as_posix()}"
            z.write(f, arc)
            manifest["files"].append({"path": arc, "sha256": _sha256(f), "bytes": f.stat().st_size})
        for extra, arc in ((bom_path, "bom/" + bom_path.name), (review_path, "review/" + review_path.name)):
            z.write(extra, arc)
            manifest["files"].append({"path": arc, "sha256": _sha256(extra), "bytes": extra.stat().st_size})
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    return {"packaged": True, "zip": str(zip_path), "verdict": review["verdict"], "warnings": review["warnings"],
            "file_count": len(manifest["files"]), "zip_sha256": _sha256(zip_path)}
