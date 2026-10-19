# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""Full-preflight checks that talk to a running Altium.

Runs all the health checks first, then a series of canaries that exercise
the IPC bridge, script alive, version match, basic command round-trips,
the Pascal handlers we lean on most.

Library-path canaries are explicit-input only: the doctor accepts an
optional list of SchLib paths from the user, and reports whether each
one is reachable and parseable. It does not crawl directories or assume
any naming scheme.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

from altium_agent.diag.checks import Check, Severity, Status
from altium_agent.diag.health import run_health_checks


def _altium_process_running() -> Check:
    """Exactly one Altium must be running.

    Altium Agent never starts X2.EXE. With none running there is nothing
    to drive; with two, bridge requests can reach the wrong instance, and
    a second X2.EXE started by a tool is how BoardKit lost its requests.
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        return Check(
            name="altium process running",
            status=Status.SKIP,
            message="psutil not installed, cannot enumerate processes",
        )

    target = os.environ.get("ALTIUM_AGENT_ALTIUM_PROCESS", "X2.exe").lower()
    matches: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name == target or name == target.replace(".exe", ""):
            matches.append(proc.info.get("pid"))

    if not matches:
        return Check(
            name="altium process running",
            status=Status.FAIL,
            message=f"no process named {target} found",
            fix=("Start Altium Designer yourself; Altium Agent never "
                 "starts it."),
        )
    if len(matches) > 1:
        return Check(
            name="altium process running",
            status=Status.FAIL,
            message=(f"{len(matches)} Altium processes are running "
                     f"(pids {matches}); bridge requests can reach the "
                     f"wrong one"),
            fix="Close every Altium instance except the one you design in.",
        )
    return Check(
        name="altium process running",
        status=Status.PASS,
        message=f"pid {matches[0]}",
    )


def _ping_altium() -> Optional[dict]:
    """Send a ping. Returns the response dict, or None on any failure."""
    try:
        from altium_agent.bridge import get_bridge
        bridge = get_bridge()
        return bridge.ping_with_version()
    except Exception:
        return None


def _check_script_responsive(ping: Optional[dict]) -> Check:
    if ping is None:
        return Check(
            name="DelphiScript polling loop responsive",
            status=Status.FAIL,
            message="ping_altium did not return",
            fix=(
                "Open AltiumAgent.PrjScr in Altium and run "
                "Dispatcher.pas > StartMCPServer."
            ),
        )
    return Check(
        name="DelphiScript polling loop responsive",
        status=Status.PASS,
        message=f"version {ping.get('script_version', '?')}",
    )


def _check_script_version_match(ping: Optional[dict]) -> Check:
    if ping is None:
        return Check(
            name="script version match",
            status=Status.SKIP,
            message="cannot check, script not responding",
            severity=Severity.MINOR,
        )

    altium_v = ping.get("script_version", "")
    bundled_v = ""
    try:
        from altium_agent.cli import get_bundled_scripts_path
        main_pas = get_bundled_scripts_path() / "Main.pas"
        if main_pas.exists():
            for line in main_pas.read_text(encoding="utf-8").splitlines():
                if "SCRIPT_VERSION" in line and "=" in line:
                    parts = line.split("=", 1)
                    bundled_v = parts[1].strip().strip(";").strip().strip("'")
                    break
    except Exception:
        pass

    if not altium_v or not bundled_v:
        return Check(
            name="script version match",
            status=Status.SKIP,
            message=f"altium={altium_v!r} bundled={bundled_v!r}, incomplete",
            severity=Severity.MINOR,
        )

    if altium_v != bundled_v:
        return Check(
            name="script version match",
            status=Status.FAIL,
            message=f"altium={altium_v} but bundled={bundled_v}",
            fix=(
                "Re-run `altium-agent install-scripts --force`, then close "
                "Altium fully (the script cache survives AltiumAgent.PrjScr "
                "reload) and reopen the script project."
            ),
        )
    return Check(
        name="script version match",
        status=Status.PASS,
        message=altium_v,
    )


def _check_readonly_canary() -> Check:
    """Round-trip a READ-ONLY command to confirm the bridge handles a real
    command, not just ping.

    eda-agent used application.save_all here, which saves every dirty
    document as a side effect of a health check. A doctor must not edit
    the user's files, so Altium Agent asks for the open documents instead.
    """
    try:
        from altium_agent.bridge import get_bridge
        bridge = get_bridge()
        bridge.send_command("application.get_open_documents", {}, timeout=15.0)
    except Exception as exc:
        return Check(
            name="bridge command canary",
            status=Status.FAIL,
            message=f"get_open_documents round-trip failed: {exc}",
            fix=("Run `altium-agent doctor --json` and read the "
                 "self-heal findings, or call app_diagnose."),
        )
    return Check(name="bridge command canary", status=Status.PASS)


def _check_self_heal() -> list[Check]:
    """What app_diagnose would report, as doctor checks."""
    try:
        from altium_agent.diag.self_heal import BLOCK, LiveEnvironment, diagnose
        result = diagnose(LiveEnvironment())
    except Exception as exc:                          # noqa: BLE001
        return [Check(name="self-heal diagnosis", status=Status.SKIP,
                      message=f"could not run: {exc}", severity=Severity.MINOR)]
    if not result["findings"]:
        return [Check(name="self-heal diagnosis", status=Status.PASS,
                      message="no modal, no active command, one Altium")]
    out = []
    for f in result["findings"]:
        out.append(Check(
            name=f"self-heal: {f['code']}",
            status=Status.FAIL if f["severity"] == BLOCK else Status.WARN,
            message=f["message"],
            fix=f["fix"] + (f" (automatic: app_recover {f['auto']})"
                            if f.get("auto") else ""),
            severity=Severity.CRITICAL if f["severity"] == BLOCK else Severity.MINOR,
        ))
    return out


def _check_freerouting() -> list[Check]:
    """Freerouting is optional and external (GPL, never vendored)."""
    from altium_agent.config import freerouting_paths
    jar, java = freerouting_paths()
    checks = []
    checks.append(Check(
        name="freerouting jar",
        status=Status.PASS if jar and jar.is_file() else Status.WARN,
        message=str(jar) if jar else "not configured",
        fix=("Optional. Set ALTIUM_AGENT_FREEROUTING_JAR to a "
             "freerouting-*.jar if a project's routing policy allows "
             "third-party engines."),
        severity=Severity.MINOR))
    checks.append(Check(
        name="java for freerouting",
        status=Status.PASS if java and java.is_file() else Status.WARN,
        message=str(java) if java else "not found",
        fix=("Optional. Set ALTIUM_AGENT_JAVA to java.exe (it need not be "
             "on PATH)."),
        severity=Severity.MINOR))
    return checks


def _check_lib_paths(library_paths: Sequence[str]) -> list[Check]:
    """Check each user-supplied SchLib path. No hardcoded paths."""
    if not library_paths:
        return [
            Check(
                name="library paths",
                status=Status.SKIP,
                message=(
                    "no --library paths supplied, pass one or more "
                    "--library PATH.SchLib to test lib reachability"
                ),
                severity=Severity.MINOR,
            )
        ]

    checks: list[Check] = []
    for raw in library_paths:
        p = Path(raw).expanduser()
        name = f"library: {p.name}"
        if not p.exists():
            checks.append(
                Check(
                    name=name,
                    status=Status.FAIL,
                    message=f"{p} does not exist",
                    fix="Check the path or correct the spelling.",
                    severity=Severity.MINOR,
                )
            )
            continue
        try:
            from altium_agent.bridge import get_bridge
            bridge = get_bridge()
            raw_resp = bridge.send_command(
                "library.get_components",
                {"library_path": str(p)},
                timeout=30.0,
            )
        except Exception as exc:
            checks.append(
                Check(
                    name=name,
                    status=Status.FAIL,
                    message=f"could not enumerate components: {exc}",
                    fix=(
                        "Confirm Altium is running and the script is "
                        "responsive (run `altium-agent doctor` and look at "
                        "earlier checks)."
                    ),
                    severity=Severity.MINOR,
                )
            )
            continue
        count = 0
        if isinstance(raw_resp, dict):
            count = len(raw_resp.get("components") or raw_resp.get("results") or [])
        elif isinstance(raw_resp, list):
            count = len(raw_resp)
        if count == 0:
            checks.append(
                Check(
                    name=name,
                    status=Status.WARN,
                    message=f"{p}: 0 components found",
                    severity=Severity.MINOR,
                )
            )
        else:
            checks.append(
                Check(
                    name=name,
                    status=Status.PASS,
                    message=f"{count} components",
                )
            )
    return checks


def run_doctor_checks(library_paths: Optional[Sequence[str]] = None) -> list[Check]:
    """Compose health + Altium-side preflight."""
    checks: list[Check] = []

    # Health checks first, early failures often explain later ones.
    checks.extend(run_health_checks())
    if any(c.status == Status.FAIL and c.severity == Severity.CRITICAL for c in checks):
        return checks

    checks.append(_altium_process_running())
    if checks[-1].status == Status.FAIL:
        return checks

    ping = _ping_altium()
    checks.append(_check_script_responsive(ping))
    checks.append(_check_script_version_match(ping))

    if ping is None:
        return checks

    checks.append(_check_readonly_canary())
    checks.extend(_check_self_heal())
    checks.extend(_check_freerouting())
    checks.extend(_check_lib_paths(library_paths or []))
    return checks
