# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tools/routing.py (find_freerouting, _run_freerouting).
# Modified for Altium Agent (2026): see docs/PROVENANCE.md.
"""Run a separately installed Freerouting as an external process.

Freerouting is GPL-3.0. Altium Agent never copies, bundles, imports or links
it: this module only finds an installation the user made and runs it with a
Specctra DSN file in and an SES file out, as any user could from a shell.

Where it looks, in order:

1. ``ALTIUM_AGENT_FREEROUTING``: a native ``freerouting``/``freerouting-cli``
   executable, or a ``.jar``.
2. ``ALTIUM_AGENT_FREEROUTING_JAR``: a ``.jar`` (with ``ALTIUM_AGENT_JAVA``).
3. ``%USERPROFILE%\\Altium Agent\\freerouting`` and the altium-mcp install
   ``%USERPROFILE%\\.altium-mcp\\freerouting`` (read only).
4. ``freerouting`` on PATH.

A jar needs Java; Freerouting 2.x is built for Java 25. The Java used is
``ALTIUM_AGENT_JAVA``, else a runtime beside the jar (``jre``), else
``java`` on PATH; ``find`` reports a jar built for a newer Java than the
one found, instead of failing at run time with a class-version error.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

#: Freerouting 2.x settings for an unattended run (names as in its
#: settings classes). No window; no usage analytics sent from the user's
#: machine; no automatic neck-down (it narrows tracks below a net's minimum
#: width at pads). The optimizer runs only on request (it can take longer
#: than the routing).
RUN_FLAGS = ("--gui.enabled=false", "--usage_and_diagnostic_data.disable_analytics=true",
             "--router.automatic_neckdown=false")
_CLASS_JAVA = {65: 21, 66: 22, 67: 23, 68: 24, 69: 25, 70: 26}


@dataclass
class Freerouting:
    command: list[str]
    kind: str                 # native | jar
    path: str
    java: str | None = None
    java_major: int | None = None
    needs_java: int | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {"command": self.command, "kind": self.kind, "path": self.path, "java": self.java,
                "java_major": self.java_major, "needs_java": self.needs_java, "usable": self.usable,
                "problems": self.problems}


def search_dirs() -> list[Path]:
    home = Path.home()
    return [home / "Altium Agent" / "freerouting", home / ".altium-mcp" / "freerouting"]


def _java_major(java: str) -> int | None:
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30,
                             check=False, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def jar_java_needed(jar: Path) -> int | None:
    """The Java release a jar's main class was compiled for."""
    try:
        with zipfile.ZipFile(jar) as z:
            mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
            main = re.search(r"^Main-Class:\s*(\S+)", mf, re.MULTILINE)
            if not main:
                return None
            data = z.read(main.group(1).replace(".", "/") + ".class")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    if data[:4] != b"\xca\xfe\xba\xbe":
        return None
    return _CLASS_JAVA.get(int.from_bytes(data[6:8], "big"))


def _bundled_java(base: Path) -> str | None:
    for name in ("java.exe", "java"):
        for p in sorted(base.rglob(name)):
            if p.is_file() and p.parent.name == "bin":
                return str(p)
    return None


def _jar(jar: Path, java_hint: str | None) -> Freerouting:
    java = java_hint or _bundled_java(jar.parent) or shutil.which("java")
    fr = Freerouting([], "jar", str(jar), java=java)
    fr.needs_java = jar_java_needed(jar)
    if not java:
        fr.problems.append("no Java found: set ALTIUM_AGENT_JAVA, or put a runtime in a jre folder beside the jar")
        return fr
    fr.java_major = _java_major(java)
    if fr.needs_java and fr.java_major and fr.java_major < fr.needs_java:
        fr.problems.append(f"{jar.name} needs Java {fr.needs_java}; {java} is Java {fr.java_major}")
    fr.command = [java, "-jar", str(jar)]
    return fr


def find() -> Freerouting | None:
    """The Freerouting to run, or None when none is installed."""
    java_env = os.environ.get("ALTIUM_AGENT_JAVA") or None
    for var in ("ALTIUM_AGENT_FREEROUTING", "ALTIUM_AGENT_FREEROUTING_JAR"):
        raw = os.environ.get(var)
        if raw:
            p = Path(raw).expanduser()
            if p.is_file():
                return _jar(p, java_env) if p.suffix.lower() == ".jar" else Freerouting([str(p)], "native", str(p))
    for base in search_dirs():
        if not base.is_dir():
            continue
        # the jar first: Freerouting 2.5's native CLI (a GraalVM image) crashes on boards
        # with keep-out areas (UnsupportedFeatureError for ObstacleArea serialization)
        jars = sorted(base.rglob("freerouting*.jar"))
        if jars:
            return _jar(jars[-1], java_env)
        natives = [p for p in sorted(base.rglob("freerouting*")) if p.is_file()
                   and (p.suffix.lower() == ".exe" or (p.suffix == "" and os.access(p, os.X_OK)))]
        if natives:
            return Freerouting([str(natives[0])], "native", str(natives[0]))
    exe = shutil.which("freerouting") or shutil.which("freerouting-cli")
    return Freerouting([exe], "native", exe) if exe else None


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class FreeroutingError(RuntimeError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def run(dsn: Path, ses: Path, *, max_passes: int = 50, timeout_s: int = 1200, optimize: bool = False,
        extra: tuple[str, ...] = (), fr: Freerouting | None = None, log: Path | None = None) -> dict:
    """Route ``dsn`` into ``ses``. Blocks until Freerouting exits or the timeout."""
    fr = fr or find()
    if fr is None:
        raise FreeroutingError("Freerouting is not installed",
                               hint="Install it yourself (it is GPL-3.0 and not shipped with Altium Agent) and "
                                    "set ALTIUM_AGENT_FREEROUTING, or put it under "
                                    + " or ".join(str(d) for d in search_dirs()))
    if not fr.usable:
        raise FreeroutingError("; ".join(fr.problems))
    ses.unlink(missing_ok=True)
    args = fr.command + ["-de", str(dsn), "-do", str(ses), "-mp", str(int(max_passes))]
    args += list(RUN_FLAGS) + [f"--router.optimizer.enabled={'true' if optimize else 'false'}"]
    args += list(extra)
    t0 = time.time()
    log = log or ses.with_suffix(".log")
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        try:
            proc = subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout_s,
                                  check=False, creationflags=_no_window())
        except subprocess.TimeoutExpired as exc:
            raise FreeroutingError(f"Freerouting did not finish within {timeout_s} s",
                                   hint="Raise timeout_s, lower max_passes, or route in batches.") from exc
    tail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
    if not ses.exists():
        raise FreeroutingError(f"Freerouting wrote no SES (exit {proc.returncode})", hint=tail)
    return {"seconds": round(time.time() - t0, 1), "exit_code": proc.returncode, "ses": str(ses),
            "log": str(log), "log_tail": tail, "command": args[:len(fr.command)]}
