# SPDX-License-Identifier: Apache-2.0
"""Self-healing bridge: say exactly what is holding Altium, fix what is safe.

A silent bridge looks the same whatever the cause: a modal dialog, a
script that died between PreProcess and PostProcess ("a command is
currently active"), a halted or never-started polling loop, a second
X2.EXE that took the requests, a touch-keyboard overlay over the dialog
an automation needed, or a dialog that opened on another monitor. Each
has a different fix, and only some of them are safe to apply without a
human.

``diagnose()`` gathers the evidence once and returns findings, each with
the exact words a user needs (``fix``) and, where it exists, the name of
a safe automatic recovery (``auto``). ``recover()`` applies only the
automatic recoveries it is asked for, and never one that would start
Altium, press a button that commits a change, or kill a process.

Every input comes through :class:`Environment`, so the logic is tested
without Windows; :class:`LiveEnvironment` is the real thing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol

#: Severity: ``block`` stops every bridge call; ``warn`` can make an
#: automation fail; ``info`` explains something without stopping work.
BLOCK, WARN, INFO = "block", "warn", "info"

#: Automatic recoveries recover() may perform, all reversible or inert.
AUTO_DISMISS_INFORMATION = "dismiss_information_dialog"
AUTO_END_COMMAND = "end_active_command"
AUTO_BRING_ON_SCREEN = "bring_dialog_on_screen"
SAFE_RECOVERIES = (AUTO_DISMISS_INFORMATION, AUTO_END_COMMAND,
                   AUTO_BRING_ON_SCREEN)

_COMMAND_ACTIVE = re.compile(r"command is currently active", re.IGNORECASE)
#: Dialog kinds (ui.dialog_report) whose only button acknowledges.
_ACK_KINDS = ("information", "nothing_to_do")

START_STEP = ("In Altium: File > Run Script... > AltiumAgent.PrjScr > "
              "Dispatcher.pas > StartMCPServer > Run.")
STOP_STEP = ("In Altium's script editor press Stop (Run > Stop, or Ctrl+F3), "
             "and close the paused script's tab.")


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    fix: str = ""
    auto: Optional[str] = None
    evidence: dict = field(default_factory=dict)


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    rect: tuple            # (left, top, right, bottom), virtual-desktop px
    process_name: str = ""


class Environment(Protocol):
    def altium_pids(self) -> list[int]: ...
    def status_form_pids(self) -> Optional[set[int]]: ...
    def ping(self) -> Optional[dict]: ...
    def dialog_report(self, pid: int) -> dict: ...
    def monitors(self) -> list[tuple]: ...
    def top_windows(self) -> list[WindowInfo]: ...
    # actions
    def press_dialog_button(self, pid: int, hwnd: int, caption: str) -> bool: ...
    def send_end_command(self) -> bool: ...
    def move_window(self, hwnd: int, x: int, y: int) -> bool: ...


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _intersects(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def monitor_of(rect: tuple, monitors: list[tuple]) -> Optional[int]:
    """Index of the monitor holding the window's centre, else None."""
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    for i, m in enumerate(monitors):
        if m[0] <= cx < m[2] and m[1] <= cy < m[3]:
            return i
    return None


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

def diagnose(env: Environment) -> dict[str, Any]:
    findings: list[Finding] = []
    pids = env.altium_pids()

    if not pids:
        findings.append(Finding(
            "ALTIUM_NOT_RUNNING", BLOCK,
            "Altium Designer is not running.",
            fix=("Start Altium Designer yourself, then " + START_STEP +
                 " Altium Agent never starts X2.EXE: a second instance "
                 "would take the bridge's requests.")))
        return _result(findings, pids, None, None)

    scripted = env.status_form_pids()
    if len(pids) > 1:
        owner = sorted(scripted & set(pids)) if scripted else []
        findings.append(Finding(
            "MULTIPLE_ALTIUM", BLOCK,
            f"{len(pids)} Altium processes are running (pids "
            f"{', '.join(map(str, pids))}); requests may reach the wrong one.",
            fix=("Close every Altium instance except the one you are "
                 "designing in" + (f" (pid {owner[0]} runs the bridge)"
                                   if len(owner) == 1 else "") + "."),
            evidence={"pids": pids, "bridge_pids": owner}))

    target = None
    if scripted:
        hits = [p for p in pids if p in scripted]
        target = hits[0] if len(hits) == 1 else None
    if target is None and len(pids) == 1:
        target = pids[0]

    ping = env.ping()
    report = env.dialog_report(target) if target is not None else {}
    dialogs = report.get("dialogs", []) if report.get("ok") else []
    blocked = bool(report.get("blocked"))
    monitors = env.monitors()

    for d in dialogs:
        text = " ".join([d.get("title", "")] + list(d.get("message") or [])
                        + list(d.get("ocr_text") or []))
        buttons = [b.get("caption", "") for b in d.get("buttons", [])]
        evidence = {"title": d.get("title", ""), "kind": d.get("kind"),
                    "buttons": buttons, "hwnd": d.get("hwnd")}
        if _COMMAND_ACTIVE.search(text):
            findings.append(Finding(
                "COMMAND_ACTIVE", BLOCK,
                "Altium says a command is currently active: a script called "
                "PreProcess and stopped before PostProcess, so saves and "
                "bridge calls time out.",
                fix=("Click OK on the message, then call "
                     "app_exit_active_command (or app_recover with "
                     "end_active_command). If a script is paused, " + STOP_STEP),
                auto=AUTO_END_COMMAND, evidence=evidence))
            continue
        kind = d.get("kind")
        if kind in _ACK_KINDS and len(buttons) == 1:
            findings.append(Finding(
                "MODAL_DIALOG", BLOCK if blocked else WARN,
                f"An information dialog is open: {d.get('title', '')!r}.",
                fix=f"Click {buttons[0]!r} in {d.get('title', '')!r}.",
                auto=AUTO_DISMISS_INFORMATION, evidence=evidence))
        else:
            findings.append(Finding(
                "MODAL_DIALOG", BLOCK if blocked else WARN,
                f"A {kind or 'dialog'} is open and needs a decision: "
                f"{d.get('title', '')!r} offering {buttons}.",
                fix=(f"Read {d.get('title', '')!r} in Altium and choose; "
                     "Altium Agent will not answer a question it was not "
                     "asked to."),
                evidence=evidence))
        rect = d.get("rect")
        if rect and monitors:
            where = monitor_of(tuple(rect), monitors)
            if where is None:
                findings.append(Finding(
                    "DIALOG_OFF_SCREEN", WARN,
                    f"{d.get('title', '')!r} is outside every monitor.",
                    fix="Press Alt+Space, M and an arrow key to bring it back.",
                    auto=AUTO_BRING_ON_SCREEN,
                    evidence={**evidence, "rect": list(rect)}))
            elif where != 0 and len(monitors) > 1:
                findings.append(Finding(
                    "DIALOG_ON_OTHER_MONITOR", INFO,
                    f"{d.get('title', '')!r} is on monitor {where + 1} of "
                    f"{len(monitors)}.",
                    fix=f"Look on monitor {where + 1}.",
                    evidence={**evidence, "monitor": where + 1}))

    # A touch-keyboard / text-input overlay over an Altium dialog blocks
    # coordinate clicks on it (BoardKit saw it over the DRC prompt).
    altium_rects = [tuple(d["rect"]) for d in dialogs if d.get("rect")]
    for w in env.top_windows():
        if "textinputhost" not in w.process_name.lower():
            continue
        covered = [r for r in altium_rects if _intersects(w.rect, r)]
        if covered:
            findings.append(Finding(
                "TEXTINPUTHOST_OVERLAY", WARN,
                "The Windows text-input overlay (TextInputHost) covers an "
                "Altium dialog, so a click there lands on the overlay.",
                fix=("Close the touch keyboard / emoji panel (Esc, or click "
                     "an empty part of the taskbar). Altium Agent answers "
                     "dialogs through their controls, not by clicking "
                     "coordinates, so its own dialog driver is not affected."),
                evidence={"overlay_hwnd": w.hwnd, "rect": list(w.rect)}))
            break

    if ping is None and not any(f.code in ("COMMAND_ACTIVE", "MODAL_DIALOG")
                                and f.severity == BLOCK for f in findings):
        if scripted and target in scripted:
            findings.append(Finding(
                "SCRIPT_HALTED", BLOCK,
                "The bridge's status window is open but the polling loop "
                "does not answer: the script halted or is paused.",
                fix=STOP_STEP + " Then " + START_STEP))
        else:
            findings.append(Finding(
                "BRIDGE_NOT_STARTED", BLOCK,
                "Altium is running but the bridge script is not.",
                fix=START_STEP + " (or `altium-agent relaunch`, which asks "
                    "the running Altium to start it and refuses when "
                    "Altium is not running)."))

    return _result(findings, pids, target, ping)


def _result(findings, pids, target, ping) -> dict[str, Any]:
    blocking = [f for f in findings if f.severity == BLOCK]
    return {
        "ok": not blocking,
        "altium_pids": pids,
        "target_pid": target,
        "bridge_answers": ping is not None,
        "script_version": (ping or {}).get("script_version"),
        "findings": [asdict(f) for f in findings],
        "auto_recoveries": sorted({f.auto for f in findings if f.auto}),
        "summary": ("Altium and the bridge are healthy." if not findings
                    else "; ".join(f.message for f in findings)),
    }


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def recover(env: Environment, actions: list[str],
            diagnosis: Optional[dict] = None) -> dict[str, Any]:
    """Apply the named safe recoveries the diagnosis offers, then re-check.

    An action the diagnosis does not offer is refused rather than tried:
    pressing OK on a dialog that is not there, or posting a PCB command
    that is not open, is how a recovery makes things worse.
    """
    unknown = [a for a in actions if a not in SAFE_RECOVERIES]
    if unknown:
        return {"ok": False, "reason": f"not a safe recovery: {unknown}",
                "safe_recoveries": list(SAFE_RECOVERIES)}
    before = diagnosis or diagnose(env)
    done, refused = [], []
    for action in actions:
        offered = [f for f in before["findings"] if f.get("auto") == action]
        if not offered:
            refused.append({"action": action,
                            "why": "the diagnosis does not call for it"})
            continue
        for f in offered:
            ev = f.get("evidence", {})
            ok = False
            if action == AUTO_DISMISS_INFORMATION:
                ok = env.press_dialog_button(before["target_pid"], ev.get("hwnd"),
                                             (ev.get("buttons") or [""])[0])
            elif action == AUTO_END_COMMAND:
                pressed = True
                if ev.get("hwnd") and ev.get("buttons"):
                    pressed = env.press_dialog_button(
                        before["target_pid"], ev["hwnd"],
                        next((b for b in ev["buttons"] if b.lower() == "ok"),
                             ev["buttons"][0]))
                ok = pressed and env.send_end_command()
            elif action == AUTO_BRING_ON_SCREEN:
                mons = env.monitors()
                if mons:
                    ok = env.move_window(ev.get("hwnd"), mons[0][0] + 40,
                                         mons[0][1] + 40)
            done.append({"action": action, "finding": f["code"], "ok": ok})
    after = diagnose(env)
    return {"ok": after["ok"], "applied": done, "refused": refused,
            "before": before["summary"], "after": after}


# ---------------------------------------------------------------------------
# The live environment (Windows)
# ---------------------------------------------------------------------------

class LiveEnvironment:
    """Reads the real desktop. Every method degrades to 'unknown' off
    Windows rather than raising, so diagnose() always returns."""

    def altium_pids(self) -> list[int]:
        try:
            from ..bridge.process_manager import AltiumProcessManager
            return [c.pid for c in AltiumProcessManager()._candidates()]
        except Exception:
            return []

    def status_form_pids(self) -> Optional[set[int]]:
        try:
            from ..bridge.process_manager import AltiumProcessManager
            pm = AltiumProcessManager
            return pm._window_owner_pids(pm.STATUS_FORM_TITLE)
        except Exception:
            return None

    def ping(self) -> Optional[dict]:
        try:
            from ..bridge import get_bridge
            return get_bridge().ping_with_version()
        except Exception:
            return None

    def dialog_report(self, pid: int) -> dict:
        try:
            from ..ui import dialog_report, windows
            report = dialog_report.report(pid)
            if report.get("ok"):
                try:
                    import win32gui  # type: ignore
                    for d in report.get("dialogs", []):
                        if d.get("hwnd"):
                            d.setdefault("rect", list(win32gui.GetWindowRect(d["hwnd"])))
                except Exception:
                    pass
            return report
        except Exception as exc:                      # noqa: BLE001
            return {"ok": False, "reason": str(exc)}

    def monitors(self) -> list[tuple]:
        try:
            import win32api  # type: ignore
            return [tuple(m[2]) for m in win32api.EnumDisplayMonitors()]
        except Exception:
            return []

    def top_windows(self) -> list[WindowInfo]:
        try:
            import psutil  # type: ignore
            import win32gui  # type: ignore
            import win32process  # type: ignore
        except Exception:
            return []
        out: list[WindowInfo] = []

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                name = psutil.Process(pid).name()
                out.append(WindowInfo(hwnd, pid, win32gui.GetWindowText(hwnd),
                                      tuple(win32gui.GetWindowRect(hwnd)), name))
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            return []
        return out

    def press_dialog_button(self, pid: int, hwnd: int, caption: str) -> bool:
        try:
            from ..ui import windows
            win = windows.capture(hwnd)
            if win is None or win.pid != pid:
                return False
            button = win.find_button(caption)
            if button is None:
                return False
            windows.click(button)
            return windows.wait_for_close(hwnd, 3.0)
        except Exception:
            return False
        return False

    def send_end_command(self) -> bool:
        try:
            from ..bridge import get_bridge
            # eda-agent's App_ExitActiveCommand: unwinds PCB and schematic
            # PreProcess levels left by a handler that died between them.
            reply = get_bridge().send_command("application.exit_active_command",
                                              {}, timeout=30.0)
            return bool(isinstance(reply, dict) and reply.get("pcb_post_process"))
        except Exception:
            return False

    def move_window(self, hwnd: int, x: int, y: int) -> bool:
        try:
            import win32con  # type: ignore
            import win32gui  # type: ignore
            win32gui.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0,
                                  win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)
            return True
        except Exception:
            return False
