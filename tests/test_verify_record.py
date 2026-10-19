# SPDX-License-Identifier: Apache-2.0
"""live_verified is written only while a live Altium answers, and only
with evidence.
"""

from __future__ import annotations

import json

import pytest

from altium_agent.diag import verify


def _ledger(tmp_path):
    p = tmp_path / "live_verified.json"
    p.write_text(json.dumps({"records": []}), encoding="utf-8")
    return p


def test_a_record_carries_the_live_identity(tmp_path):
    ev = tmp_path / "run.txt"
    ev.write_text("transcript", encoding="utf-8")
    ledger = _ledger(tmp_path)
    out = verify.record(["pcb_set_rules_enabled"], str(ev), "AD26 sticks",
                        ledger=ledger,
                        ping=lambda: {"pong": True, "script_version": "abc123"},
                        version=lambda: {"version": "26.10.1.6"})
    rec = json.loads(ledger.read_text())["records"][0]
    assert out["ok"] and rec["tool"] == "pcb_set_rules_enabled"
    assert rec["script_stamp"] == "abc123" and rec["altium_version"] == "26.10.1.6"
    assert rec["evidence"].endswith("run.txt") and rec["date"]


def test_no_live_altium_no_record(tmp_path):
    ev = tmp_path / "run.txt"
    ev.write_text("x", encoding="utf-8")
    ledger = _ledger(tmp_path)
    with pytest.raises(RuntimeError, match="did not answer"):
        verify.record(["pcb_set_rules_enabled"], str(ev), ledger=ledger,
                      ping=lambda: None, version=lambda: None)
    assert json.loads(ledger.read_text())["records"] == []


def test_evidence_is_required(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify.record(["pcb_set_rules_enabled"], str(tmp_path / "none.txt"),
                      ledger=_ledger(tmp_path), ping=lambda: {"pong": True},
                      version=lambda: {})


def test_offline_tools_are_refused(tmp_path):
    ev = tmp_path / "run.txt"
    ev.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="offline"):
        verify.record(["tool_catalog"], str(ev), ledger=_ledger(tmp_path),
                      ping=lambda: {"pong": True}, version=lambda: {})


def test_unknown_tools_are_refused(tmp_path):
    ev = tmp_path / "run.txt"
    ev.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not registered"):
        verify.record(["pcb_nope"], str(ev), ledger=_ledger(tmp_path),
                      ping=lambda: {"pong": True}, version=lambda: {},
                      known_tools={"pcb_set_rules_enabled"})
