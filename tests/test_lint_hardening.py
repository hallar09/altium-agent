# SPDX-License-Identifier: Apache-2.0
"""The DelphiScript hardening rules catch what they claim, and the shipped
scripts carry no violation that is not on the shrinking baseline.

Each rule is proven on a minimal bad snippet AND on its correct twin, so a
rule that fires on everything (or nothing) fails here, not in Altium.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
sys.path.insert(0, str(SCRIPTS))
import lint as lint_mod  # noqa: E402
import lint_hardening as h  # noqa: E402


def rules_hit(src: str) -> list[str]:
    return [x.rule for x in h.scan(src) if x.severity == "error"]


# -- AA101 PreProcess / PostProcess in Try/Finally ---------------------------

BAD_PRE = """
Function Bad(Board : IPCB_Board) : String;
Begin
    PCBServer.PreProcess;
    Board.AddPCBObject(Nil);
    PCBServer.PostProcess;
End;
"""
BAD_PRE_EXCEPT = """
Function Bad2 : String;
Begin
    PCBServer.PreProcess;
    Try
        DoIt(0);
    Except
    End;
    PCBServer.PostProcess;
End;
"""
GOOD_PRE = """
Function Good(Board : IPCB_Board) : String;
Begin
    PCBServer.PreProcess;
    Try
        Board.AddPCBObject(Nil);
    Finally
        PCBServer.PostProcess;
    End;
End;
"""
GOOD_SCH = """
Function GoodSch(Doc : ISch_Document) : String;
Begin
    SchServer.ProcessControl.PreProcess(Doc, '');
    Try
        Doc.GraphicallyInvalidate;
    Finally
        SchServer.ProcessControl.PostProcess(Doc, '');
    End;
End;
"""


def test_preprocess_without_try_is_an_error():
    assert "preprocess-needs-try-finally" in rules_hit(BAD_PRE)
    assert "preprocess-needs-try-finally" in rules_hit(BAD_PRE_EXCEPT)


def test_preprocess_in_try_finally_is_clean():
    assert rules_hit(GOOD_PRE) == []
    assert rules_hit(GOOD_SCH) == []


def test_a_finally_that_posts_the_wrong_server_is_an_error():
    wrong = GOOD_SCH.replace("SchServer.ProcessControl.PostProcess(Doc, '')",
                             "PCBServer.PostProcess")
    assert "preprocess-needs-try-finally" in rules_hit(wrong)


# -- AA102 tenting needs manual mask mode -----------------------------------

BAD_TENT = """
Procedure Tent(Via : IPCB_Via);
Begin
    Via.IsTenting_Top := True;
    Via.IsTenting_Bottom := True;
End;
"""
GOOD_TENT = """
Procedure Tent(Via : IPCB_Via);
Begin
    Via.SolderMaskExpansionMode := eMaskExpansionMode_Manual;
    Via.IsTenting_Top := True;
    Via.IsTenting_Bottom := True;
End;
"""
LATE_MANUAL = """
Procedure Tent(Via : IPCB_Via);
Begin
    Via.IsTenting_Top := True;
    Via.SolderMaskExpansionMode := eMaskExpansionMode_Manual;
End;
"""


def test_tenting_needs_manual_mask_first():
    assert rules_hit(BAD_TENT).count("tenting-needs-manual-mask") == 2
    assert rules_hit(GOOD_TENT) == []
    assert "tenting-needs-manual-mask" in rules_hit(LATE_MANUAL)


# -- AA103 rule enable writes ----------------------------------------------

BAD_ENABLE = """
Procedure Off(Rule : IPCB_Rule);
Begin
    Rule.Enabled := False;
End;
"""
BAD_DRC = """
Procedure Off(Rule : IPCB_Rule);
Begin
    Rule.DRCEnabled := False;
End;
"""
GOOD_READBACK = """
Procedure Off(Board : IPCB_Board; Rule : IPCB_Rule);
Begin
    Rule.DRCEnabled := False;
    If ReadBackRuleEnabled(Board, Rule.Name, True, False) <> 1 Then Exit;
End;
"""
GOOD_NEW_RULE = """
Procedure NewRule(Board : IPCB_Board);
Var Rule : IPCB_Rule;
Begin
    Rule := PCBServer.PCBRuleFactory(eRule_Clearance);
    Rule.Name := 'X';
    Rule.Enabled := True;
    Board.AddPCBObject(Rule);
End;
"""


def test_rule_enable_writes_must_be_read_back():
    assert "rule-enabled-write" in rules_hit(BAD_ENABLE)
    assert "rule-enabled-write" in rules_hit(BAD_DRC)
    assert rules_hit(GOOD_READBACK) == []
    assert rules_hit(GOOD_NEW_RULE) == []


# -- AA104 record field writes ----------------------------------------------

BAD_RECORD = """
Procedure R;
Var
    BR : TCoordRect;
Begin
    BR.Left := 0;
End;
"""
GOOD_RECORD = """
Procedure R(Rule : IPCB_Rule);
Var
    BR : TCoordRect;
Begin
    BR := Rule.BoundingRect;
    BR.Left := 0;
    Rule.BoundingRect := BR;
End;
"""


def test_record_field_write_needs_a_materialized_record():
    assert "record-field-assignment" in rules_hit(BAD_RECORD)
    assert rules_hit(GOOD_RECORD) == []


# -- AA105 collect first, then modify ---------------------------------------

BAD_WALK = """
Procedure Walk(Board : IPCB_Board);
Var
    Iter : IPCB_BoardIterator;
    Track : IPCB_Track;
Begin
    Iter := Board.BoardIterator_Create;
    Track := Iter.FirstPCBObject;
    While Track <> Nil Do
    Begin
        Track.Width := 100;
        Board.RemovePCBObject(Track);
        Track := Iter.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iter);
End;
"""
GOOD_WALK = """
Procedure Walk(Board : IPCB_Board);
Var
    Iter : IPCB_BoardIterator;
    Track : IPCB_Track;
    All : TInterfaceList;
    I : Integer;
Begin
    All := TInterfaceList.Create;
    Iter := Board.BoardIterator_Create;
    Track := Iter.FirstPCBObject;
    While Track <> Nil Do
    Begin
        All.Add(Track);
        Track := Iter.NextPCBObject;
    End;
    Board.BoardIterator_Destroy(Iter);
    For I := 0 To All.Count - 1 Do
    Begin
        Track := All[I];
        Track.Width := 100;
    End;
End;
"""


def test_editing_the_walked_board_is_an_error():
    hits = rules_hit(BAD_WALK)
    assert hits.count("mutate-while-iterating") == 2


def test_collect_then_modify_is_clean():
    assert rules_hit(GOOD_WALK) == []


# -- AA106 jumps inside a guarded section ------------------------------------

def test_continue_inside_a_guarded_section_is_a_warning():
    src = GOOD_PRE.replace("Board.AddPCBObject(Nil);",
                           "While True Do Begin Continue; End;")
    warn = [x.rule for x in h.scan(src) if x.severity == "warn"]
    assert "continue-break-in-try" in warn
    assert rules_hit(src) == []


# -- comments and strings never trigger a rule ------------------------------

def test_comments_and_strings_are_not_code():
    src = """
Procedure Q;
Begin
    { PCBServer.PreProcess; Rule.Enabled := False; }
    // Via.IsTenting_Top := True;
    ShowMessage('PCBServer.PreProcess; Rule.Enabled := False;');
End;
"""
    assert rules_hit(src) == []


# -- the shipped scripts ----------------------------------------------------

def _shipped_errors() -> Counter:
    found: Counter = Counter()
    for name in lint_mod.PAS_FILES:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        clean = h.clean_source(text)
        spans = h.routines(clean)
        for x in h.scan(text):
            if x.severity != "error":
                continue
            routine = next((r.name for r in spans if r.start <= x.offset < r.end), "")
            found[(name, routine, x.rule)] += 1
    return found


def test_shipped_scripts_add_no_violation_beyond_the_baseline():
    baseline = {(e["file"], e["routine"], e["rule"]): e["count"]
                for e in json.loads((SCRIPTS / "lint_baseline.json")
                                    .read_text(encoding="utf-8"))["entries"]}
    found = _shipped_errors()
    new = {k: v for k, v in found.items() if k not in baseline}
    grown = {k: (baseline[k], v) for k, v in found.items()
             if k in baseline and v > baseline[k]}
    assert not new, f"new hardening violations: {new}"
    assert not grown, f"baseline entries that grew (recorded, now): {grown}"


def test_the_baseline_only_lists_what_is_still_there():
    entries = json.loads((SCRIPTS / "lint_baseline.json")
                         .read_text(encoding="utf-8"))["entries"]
    found = _shipped_errors()
    stale = [e for e in entries
             if found.get((e["file"], e["routine"], e["rule"]), 0) == 0]
    shrunk = [e for e in entries
              if 0 < found.get((e["file"], e["routine"], e["rule"]), 0) < e["count"]]
    assert not stale, f"fixed, so remove from lint_baseline.json: {stale}"
    assert not shrunk, f"lower the recorded count in lint_baseline.json: {shrunk}"


def test_the_build_gate_passes():
    assert lint_mod.run_lint(verbose=False) == 0


def test_no_pcb_server_pair_is_left_unguarded():
    """The baseline may hold schematic PreProcess pairs, never PCB ones:
    the twelve inherited PCB-server pairs were wrapped in Try/Finally when
    this rule landed, because an unguarded one is what leaves "a command
    is currently active" and times out every later call."""
    for name in lint_mod.PAS_FILES:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        clean = h.clean_source(text)
        for x in h.scan(text):
            if x.rule != "preprocess-needs-try-finally":
                continue
            assert not clean[x.offset:x.offset + 9].lower().startswith("pcbserver"), (
                f"{name}:{h.line_of(text, x.offset)} leaves the PCB server open")
