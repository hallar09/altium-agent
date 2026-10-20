# SPDX-License-Identifier: Apache-2.0
"""The dialog driver answers DRC's dialogs only as far as it was authorised.

BoardKit lost DRC runs to two modals: the Design Rule Checker itself,
and, on a board with shelved or modified polygons, a prompt offering
"Repour Polygons and Run DRC" or "Run DRC". The first is harmless to
answer when DRC was asked for. The second is a real choice: repouring
rebuilds copper. These tests hold the decisions; the Win32 side is
covered by the live run (docs/RELEASE_VERIFICATION.md, Part A).
"""

from __future__ import annotations

import asyncio

from altium_agent.tools.registry import ToolRegistry
from altium_agent.ui.dialog_driver import decide, role_of
from altium_agent.ui.dialog_report import classify

POUR_BUTTONS = ["Repour Polygons and Run DRC", "Run DRC", "Cancel"]
CHECKER_BUTTONS = ["Run Design Rule Check...", "Close"]


def _dialog(kind, buttons):
    return {"kind": kind, "title": "", "buttons": [{"caption": b, "enabled": True} for b in buttons]}


def test_the_two_dialogs_are_recognised():
    assert classify("Confirm", ["Some polygons are shelved or modified.",
                                "Repour Polygons and Run DRC?"]) == "drc_pour_prompt"
    assert classify("Design Rule Checker [mm]", []) == "drc_setup"


def test_the_repour_button_is_never_read_as_plain_run():
    assert role_of("Repour Polygons and Run DRC") == "repour"
    assert role_of("Run DRC") == "run_check"
    assert role_of("Run Design Rule Check...") == "run_check"


def test_the_polygon_prompt_is_left_for_a_person_by_default():
    caption, why = decide(_dialog("drc_pour_prompt", POUR_BUTTONS), "proceed", True, set(), drc_run=True)
    assert caption is None and "repour" in why


def test_run_as_is_presses_plain_run():
    caption, _ = decide(_dialog("drc_pour_prompt", POUR_BUTTONS), "proceed", False, set(),
                        drc_run=True, drc_pours="run_as_is")
    assert caption == "Run DRC"


def test_repour_needs_commit_authority():
    d = _dialog("drc_pour_prompt", POUR_BUTTONS)
    assert decide(d, "proceed", False, set(), drc_run=True, drc_pours="repour")[0] is None
    assert decide(d, "proceed", True, set(), drc_run=True, drc_pours="repour")[0] == \
        "Repour Polygons and Run DRC"


def test_the_checker_runs_only_when_asked_then_closes():
    d = _dialog("drc_setup", CHECKER_BUTTONS)
    assert decide(d, "proceed", False, set())[0] is None
    assert decide(d, "proceed", False, set(), drc_run=True)[0] == "Run Design Rule Check..."
    assert decide(d, "proceed", False, {"run_check"}, drc_run=True)[0] == "Close"


def test_cancel_backs_out_of_either():
    assert decide(_dialog("drc_pour_prompt", POUR_BUTTONS), "cancel", False, set())[0] == "Cancel"
    assert decide(_dialog("drc_setup", CHECKER_BUTTONS), "cancel", False, set())[0] == "Close"


def _run_drc(**kwargs):
    from altium_agent.tools.pcb import register_pcb_tools

    reg = ToolRegistry()
    register_pcb_tools(reg)
    spec = next(t for t in asyncio.run(reg.list_tools()) if t.name == "pcb_run_drc")
    return asyncio.run(spec.fn(**kwargs))


def test_the_tool_refuses_a_repour_it_was_not_allowed():
    out = _run_drc(allow_modal=True, answer_dialogs=True, drc_pours="repour")
    assert out["ok"] is False and "allow_repour" in out["reason"] and out["drc_triggered"] is False


def test_the_tool_refuses_an_unknown_pour_choice():
    out = _run_drc(allow_modal=True, answer_dialogs=True, drc_pours="maybe")
    assert out["ok"] is False and out["drc_triggered"] is False


def test_the_tool_still_refuses_without_allow_modal():
    out = _run_drc(answer_dialogs=True, drc_pours="run_as_is")
    assert out["ok"] is False and out["drc_triggered"] is False


# --- the loop, on a scripted screen (as in tests/test_dialog_driver.py) ----------

import pytest  # noqa: E402

from altium_agent.ui import dialog_driver as dd  # noqa: E402


def _shown(kind, title, buttons, hwnd):
    return {"kind": kind, "title": title, "hwnd": hwnd, "needs_a_human": False, "message": [],
            "buttons": [{"caption": b, "enabled": True} for b in buttons]}


POUR = _shown("drc_pour_prompt", "Confirm", POUR_BUTTONS, 11)
CHECKER = _shown("drc_setup", "Design Rule Checker [mm]", CHECKER_BUTTONS, 12)


@pytest.fixture
def screen(monkeypatch):
    def install(frames):
        state = {"frames": list(frames), "clicked": []}

        def report(pid):
            fr = state["frames"]
            frame = fr[0] if len(fr) == 1 else fr.pop(0)
            return {"ok": True, "dialogs": frame}

        class _Btn:
            def __init__(self, caption):
                self.text = caption

        class _Win:
            def find_button(self, caption):
                return _Btn(caption)

        monkeypatch.setattr(dd.win, "available", lambda: True)
        monkeypatch.setattr(dd.report, "report", report)
        monkeypatch.setattr(dd.win, "capture", lambda hwnd: _Win())
        monkeypatch.setattr(dd.win, "click", lambda b: state["clicked"].append(b.text))
        monkeypatch.setattr(dd.win, "wait_for_close", lambda hwnd, timeout: True)
        return state
    return install


def test_prompt_then_checker_are_both_answered_with_linger(screen):
    """The checker comes up a moment after the prompt closes: linger waits for it."""
    scr = screen([[POUR], [], [], [CHECKER], [CHECKER], []])
    out = dd.drive(1, dry_run=False, settle=0, drc_run=True, drc_pours="run_as_is", linger=0.5)
    assert scr["clicked"] == ["Run DRC", "Run Design Rule Check...", "Close"]
    assert out["ok"] and out["committed"] is False


def test_an_unanswered_prompt_flags_a_human(screen):
    scr = screen([[POUR]])
    out = dd.drive(1, dry_run=False, settle=0, drc_run=True)
    assert scr["clicked"] == [] and out["stopped_for_a_human"] is True and out["ok"] is False


def test_a_repour_counts_as_a_design_change(screen):
    scr = screen([[POUR], [CHECKER], [CHECKER], []])
    out = dd.drive(1, dry_run=False, settle=0, allow_commit=True, drc_run=True,
                   drc_pours="repour", linger=0.5)
    assert scr["clicked"][0] == "Repour Polygons and Run DRC" and out["committed"] is True
