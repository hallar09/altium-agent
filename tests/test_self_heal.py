# SPDX-License-Identifier: Apache-2.0
"""The self-healing bridge names the real cause and fixes only what is safe.

Driven through a fake desktop so every branch runs without Windows: no
Altium, two Altiums, a halted script, a script never started, the
"command is currently active" box, a question dialog, an information box,
a dialog off every monitor, one on the second monitor, and the
TextInputHost overlay.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from altium_agent.diag import self_heal as sh

MON1 = (0, 0, 1920, 1080)
MON2 = (1920, 0, 3840, 1080)


@dataclass
class FakeDesktop:
    pids: list = field(default_factory=lambda: [100])
    scripted: set = field(default_factory=lambda: {100})
    answers: bool = True
    dialogs: list = field(default_factory=list)
    blocked: bool = False
    mons: list = field(default_factory=lambda: [MON1])
    windows: list = field(default_factory=list)
    pressed: list = field(default_factory=list)
    end_commands: int = 0
    moved: list = field(default_factory=list)

    def altium_pids(self):
        return list(self.pids)

    def status_form_pids(self):
        return set(self.scripted)

    def ping(self):
        return {"pong": True, "script_version": "t"} if self.answers else None

    def dialog_report(self, pid):
        return {"ok": True, "blocked": self.blocked, "dialogs": list(self.dialogs)}

    def monitors(self):
        return list(self.mons)

    def top_windows(self):
        return list(self.windows)

    def press_dialog_button(self, pid, hwnd, caption):
        self.pressed.append((hwnd, caption))
        self.dialogs = [d for d in self.dialogs if d["hwnd"] != hwnd]
        self.blocked = bool(self.dialogs)
        return True

    def send_end_command(self):
        self.end_commands += 1
        self.answers = True
        return True

    def move_window(self, hwnd, x, y):
        self.moved.append((hwnd, x, y))
        for d in self.dialogs:
            if d["hwnd"] == hwnd:
                w = d["rect"][2] - d["rect"][0]
                h = d["rect"][3] - d["rect"][1]
                d["rect"] = [x, y, x + w, y + h]
        return True


def dialog(hwnd, title, buttons, kind="confirm", message=(), rect=(100, 100, 500, 300)):
    return {"hwnd": hwnd, "title": title, "kind": kind,
            "message": list(message), "rect": list(rect),
            "buttons": [{"caption": b} for b in buttons]}


def codes(result):
    return [f["code"] for f in result["findings"]]


def test_healthy():
    out = sh.diagnose(FakeDesktop())
    assert out["ok"] and out["findings"] == []
    assert out["summary"] == "Altium and the bridge are healthy."


def test_no_altium_is_never_started_for_you():
    out = sh.diagnose(FakeDesktop(pids=[]))
    assert codes(out) == ["ALTIUM_NOT_RUNNING"]
    assert "never starts X2.EXE" in out["findings"][0]["fix"]
    assert out["auto_recoveries"] == []


def test_two_altiums_is_blocking_and_names_the_bridge_owner():
    out = sh.diagnose(FakeDesktop(pids=[100, 200], scripted={200}))
    f = out["findings"][0]
    assert f["code"] == "MULTIPLE_ALTIUM" and f["severity"] == sh.BLOCK
    assert "pid 200 runs the bridge" in f["fix"]
    assert out["target_pid"] == 200


def test_bridge_never_started():
    out = sh.diagnose(FakeDesktop(scripted=set(), answers=False))
    assert codes(out) == ["BRIDGE_NOT_STARTED"]
    assert "StartMCPServer" in out["findings"][0]["fix"]


def test_halted_script():
    out = sh.diagnose(FakeDesktop(answers=False))
    assert codes(out) == ["SCRIPT_HALTED"]
    assert "Stop" in out["findings"][0]["fix"]


def test_command_active_is_recovered_automatically():
    desk = FakeDesktop(answers=False, blocked=True, dialogs=[dialog(
        7, "Altium Designer", ["OK"], kind="error",
        message=["A command is currently active and save cannot be completed"])])
    out = sh.diagnose(desk)
    assert codes(out) == ["COMMAND_ACTIVE"]
    assert out["auto_recoveries"] == [sh.AUTO_END_COMMAND]
    fixed = sh.recover(desk, [sh.AUTO_END_COMMAND], out)
    assert desk.pressed == [(7, "OK")] and desk.end_commands == 1
    assert fixed["ok"] and fixed["after"]["findings"] == []


def test_a_question_is_never_answered_for_you():
    desk = FakeDesktop(blocked=True, dialogs=[dialog(
        9, "Confirm", ["Yes", "No"], message=["Repour polygons and run DRC?"])])
    out = sh.diagnose(desk)
    f = out["findings"][0]
    assert f["code"] == "MODAL_DIALOG" and f["auto"] is None
    refused = sh.recover(desk, [sh.AUTO_DISMISS_INFORMATION], out)
    assert desk.pressed == [] and refused["refused"]


def test_an_information_box_can_be_dismissed():
    desk = FakeDesktop(blocked=True, dialogs=[dialog(
        3, "Information", ["OK"], kind="information",
        message=["No Designator changes are required"])])
    out = sh.diagnose(desk)
    assert out["auto_recoveries"] == [sh.AUTO_DISMISS_INFORMATION]
    sh.recover(desk, [sh.AUTO_DISMISS_INFORMATION], out)
    assert desk.pressed == [(3, "OK")]


def test_unknown_recoveries_are_refused_outright():
    out = sh.recover(FakeDesktop(), ["kill_altium"])
    assert not out["ok"] and "not a safe recovery" in out["reason"]


def test_a_dialog_off_every_monitor_is_brought_back():
    desk = FakeDesktop(dialogs=[dialog(
        5, "Engineering Change Order", ["Validate Changes", "Execute Changes",
                                        "Close"],
        kind="engineering_change_order", rect=(5000, 5000, 5400, 5300))])
    out = sh.diagnose(desk)
    assert "DIALOG_OFF_SCREEN" in codes(out)
    sh.recover(desk, [sh.AUTO_BRING_ON_SCREEN], out)
    assert desk.moved and desk.moved[0][0] == 5
    assert "DIALOG_OFF_SCREEN" not in codes(sh.diagnose(desk))


def test_a_dialog_on_the_second_monitor_is_pointed_out():
    desk = FakeDesktop(mons=[MON1, MON2], dialogs=[dialog(
        6, "Engineering Change Order", ["Close"],
        kind="engineering_change_order", rect=(2100, 100, 2500, 400))])
    out = sh.diagnose(desk)
    f = next(f for f in out["findings"] if f["code"] == "DIALOG_ON_OTHER_MONITOR")
    assert f["evidence"]["monitor"] == 2


def test_the_text_input_overlay_is_reported():
    desk = FakeDesktop(dialogs=[dialog(
        8, "Confirm", ["Repour Polygons and Run DRC", "Run DRC"],
        rect=(300, 300, 700, 500))],
        windows=[sh.WindowInfo(42, 999, "", (350, 350, 900, 700),
                               "TextInputHost.exe")])
    out = sh.diagnose(desk)
    assert "TEXTINPUTHOST_OVERLAY" in codes(out)


def test_the_live_environment_never_raises_off_windows():
    out = sh.diagnose(sh.LiveEnvironment())
    assert isinstance(out, dict) and "findings" in out
