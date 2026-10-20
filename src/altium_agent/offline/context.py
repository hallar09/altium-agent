# SPDX-License-Identifier: Apache-2.0 AND MIT
"""What the ported altium-mcp-pro code expects from its host: settings,
path rules, the active project and the DFM profile.

altium-mcp-pro had a config module, a path guard confined to configured
roots, a session module and operating modes. Altium Agent keeps one
policy for all of them, here:

- READING a design file is allowed anywhere: the offline readers never
  change a file, and the projects people analyse live wherever they live
  (including Altium's own examples, which are read, never written).
- WRITING (a BOM CSV, a gate history) only goes under the Altium Agent
  output folder, ``%USERPROFILE%\\Altium Agent\\outputs\\<project>`` or
  ``ALTIUM_AGENT_OUTPUT_DIR``, never next to the design and never into
  Altium's install or example folders. That keeps the rule "only the
  user's design copies are edited" true by construction for these tools.
- Operating modes are gone: every ported tool is read-only, and its
  maturity/interaction badges come from tools/metadata.py like any
  other tool.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .errors import InvalidArgument, PathNotAllowed, ProjectNotFound

DEFAULT_DFM_PROFILE = "generic_standard"


class ToolClass(str, Enum):
    READ = "read"
    WRITE = "write"
    MANUFACTURING = "manufacturing"


class OperatingMode(str, Enum):
    READONLY = "readonly"


def allows(mode, tool_class) -> bool:  # noqa: ARG001 - kept for the ported call sites
    return tool_class == ToolClass.READ


@dataclass(frozen=True)
class Settings:
    project: Optional[Path]
    dfm_profile: str
    output_dir: Optional[Path]
    mode: OperatingMode = OperatingMode.READONLY


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def get_settings() -> Settings:
    """Read every call: cheap, and tests change the environment freely."""
    project = Path(_env("ALTIUM_AGENT_PROJECT")).expanduser() if _env("ALTIUM_AGENT_PROJECT") else None
    out = Path(_env("ALTIUM_AGENT_OUTPUT_DIR")).expanduser() if _env("ALTIUM_AGENT_OUTPUT_DIR") else None
    return Settings(project=project.resolve() if project else None,
                    dfm_profile=dfm_profile() or _env("ALTIUM_AGENT_DFM_PROFILE") or DEFAULT_DFM_PROFILE,
                    output_dir=out)


get_settings.cache_clear = lambda: None  # type: ignore[attr-defined]  # ported call sites clear a cache


# --- session state -----------------------------------------------------------

_state: dict[str, object] = {"project": None, "dfm_profile": None}


def set_active_project(path: Optional[Path]) -> None:
    _state["project"] = path


def active_project() -> Optional[Path]:
    p = _state["project"]
    return p if isinstance(p, Path) else None


def set_dfm_profile(name: Optional[str]) -> None:
    _state["dfm_profile"] = name


def dfm_profile() -> Optional[str]:
    p = _state["dfm_profile"]
    return p if isinstance(p, str) else None


# --- paths -------------------------------------------------------------------

def _forbidden_write_roots() -> list[Path]:
    roots = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    public = os.environ.get("PUBLIC") or str(Path(os.environ.get("SystemDrive", "C:") + "\\") / "Users" / "Public")
    roots.append(Path(public) / "Documents" / "Altium")
    return roots


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def safe_path(raw, *, must_exist: bool = True, suffixes: tuple[str, ...] = ()) -> Path:
    """A path to READ. Any folder is allowed; the reader never writes."""
    if raw is None or str(raw).strip() == "":
        raise InvalidArgument("empty path")
    if "\x00" in str(raw):
        raise InvalidArgument("path contains a NUL byte")
    path = Path(str(raw).strip().strip('"')).expanduser()
    if not path.is_absolute():
        base = get_settings().project.parent if get_settings().project else Path.cwd()
        path = base / path
    path = path.resolve()
    if suffixes and path.suffix.lower() not in suffixes:
        raise InvalidArgument(f"{path.name}: expected one of {', '.join(suffixes)}")
    if must_exist and not path.exists():
        raise InvalidArgument(f"{path} does not exist")
    return path


def resolve_project(project: Optional[str]) -> Path:
    """The .PrjPcb from the argument, the active project, or ALTIUM_AGENT_PROJECT.

    A folder holding exactly one .PrjPcb is accepted.
    """
    if project:
        p = safe_path(project)
    elif active_project():
        p = safe_path(active_project())
    elif get_settings().project:
        p = safe_path(get_settings().project)
    else:
        raise ProjectNotFound(
            "no project given",
            hint="Pass project='C:/path/Board.PrjPcb' (or its folder), or set ALTIUM_AGENT_PROJECT.")
    if p.is_dir():
        found = list(dict.fromkeys(sorted(p.glob("*.PrjPcb")) + sorted(p.glob("*.prjpcb"))))
        if len(found) != 1:
            raise ProjectNotFound(f"{p} contains {len(found)} .PrjPcb files",
                                  hint="Pass the .PrjPcb file itself.")
        p = found[0]
    if p.suffix.lower() != ".prjpcb":
        raise InvalidArgument(f"{p.name} is not a .PrjPcb project")
    return p


def project_path_or_none(project: Optional[str] = None) -> Optional[Path]:
    try:
        return resolve_project(project)
    except Exception:  # noqa: BLE001 - optional context only
        return None


def outputs_home() -> Path:
    return Path.home() / "Altium Agent" / "outputs"


def output_dir_for(project_path: Optional[Path], sub: str) -> Path:
    """The folder a tool may WRITE into. Never beside the design."""
    base = get_settings().output_dir or outputs_home()
    name = project_path.stem if project_path is not None else "_no_project"
    out = (base / name / sub).resolve()
    if any(_within(out, r) for r in _forbidden_write_roots()):
        raise PathNotAllowed(f"refusing to write into {out}",
                             hint="Set ALTIUM_AGENT_OUTPUT_DIR to a folder of your own.")
    out.mkdir(parents=True, exist_ok=True)
    return out


# --- project discovery (from altium-mcp-pro discovery.py, MIT) -------------------
# Only the folder walk is kept: Altium Agent finds running Altium and its
# installs in diag/ and bridge/, so those helpers are not ported.

import time  # noqa: E402


def list_altium_installs() -> list[dict]:
    return []


def running_altium() -> list[dict]:
    return []


def find_projects(roots: list[Path], *, max_results: int = 200, time_budget_s: float = 8.0,
                  max_depth: int = 6) -> list[dict]:
    """Walk roots for *.PrjPcb, skipping heavy/irrelevant folders. Bounded by time and depth."""
    skip = {"node_modules", ".git", "history", "project outputs", "__pycache__", ".venv", "appdata",
            "$recycle.bin", "windows", "program files", "program files (x86)"}
    start = time.monotonic()
    results: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        stack = [(root, 0)]
        while stack:
            if time.monotonic() - start > time_budget_s or len(results) >= max_results:
                return results
            d, depth = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if depth < max_depth and e.name.lower() not in skip and not e.name.startswith("."):
                            stack.append((Path(e.path), depth + 1))
                    elif e.name.lower().endswith(".prjpcb"):
                        key = os.path.normcase(e.path)
                        if key not in seen:
                            seen.add(key)
                            st = e.stat()
                            results.append({"path": e.path, "name": Path(e.name).stem,
                                            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
                except OSError:
                    continue
    results.sort(key=lambda r: r["modified"], reverse=True)
    return results


__all__ = [
    "DEFAULT_DFM_PROFILE", "OperatingMode", "Settings", "ToolClass", "active_project", "allows",
    "dfm_profile", "find_projects", "get_settings", "list_altium_installs", "output_dir_for", "outputs_home", "project_path_or_none",
    "resolve_project", "running_altium", "safe_path", "set_active_project", "set_dfm_profile",
]
