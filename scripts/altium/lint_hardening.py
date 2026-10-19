# SPDX-License-Identifier: Apache-2.0
"""Altium Agent hardening rules for DelphiScript, run by lint.py.

Each rule encodes a lesson that cost a session on AD26 (BoardKit's
"Known issues", docs/KNOWN_ISSUES.md). They run over whole routines, not
single lines, because every one of them is about ORDER inside a routine:
what has to come before what.

    AA101 preprocess-needs-try-finally   error
        PCBServer.PreProcess (or SchServer.ProcessControl.PreProcess) must
        be followed directly by Try, and that Try must close with a
        Finally that calls the matching PostProcess. A script that dies
        between the two leaves "a command is currently active": every
        save and every bridge call then times out.

    AA102 tenting-needs-manual-mask      error
        AD26 ignores IsTenting_Top/Bottom while the via's
        SolderMaskExpansionMode follows the rule. Set
        eMaskExpansionMode_Manual first, in the same routine.

    AA103 rule-enabled-write             error
        Scripted Rule.Enabled / DRCEnabled does not stick in AD26. Delete
        and recreate the rule instead; a write is accepted only when the
        routine reads it back (calls ReadBackRuleEnabled) or when it
        enables a NEW rule right before Board.AddPCBObject.

    AA104 record-field-assignment        error
        Writing a field of a record local that was never assigned (R.Left
        := ... on a fresh TCoordRect) stops the script with "Undeclared
        identifier: Left", and Try/Except cannot catch it. Read the whole
        record from its property first (R := Rule.BoundingRect), or use
        plain TCoord variables.

    AA105 mutate-while-iterating         error
        Never add, remove, move or re-shape PCB objects between a board
        iterator's First...Object and its last Next...Object. Collect into
        a TInterfaceList first, destroy the iterator, then modify. Editing
        mid-walk can loop forever and hang Altium.

    AA106 continue-break-in-try          warn
        Continue/Break inside a Try section that guards a PreProcess.
        Delphi runs the Finally on the jump; whether every DelphiScript
        build does is not established, so keep the jump outside the
        guarded section or verify it live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator

# --------------------------------------------------------------------------
# Source cleaning: comments and string contents become spaces, so offsets
# and line numbers stay exact.
# --------------------------------------------------------------------------


def clean_source(text: str) -> str:
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            j = text.find("}", i + 1)
            j = n - 1 if j < 0 else j
            for k in range(i, j + 1):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        if ch == "(" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*)", i + 2)
            j = n - 2 if j < 0 else j
            for k in range(i, j + 2):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 2
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                if text[j] == "\n":
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
            continue
        i += 1
    return "".join(out)


_ROUTINE = re.compile(r"(?im)^[ \t]*(?:function|procedure)\s+(\w+)")


@dataclass
class Routine:
    name: str
    start: int          # offset of the header in the cleaned text
    end: int            # offset just past the routine
    text: str           # cleaned text of the routine


def routines(clean: str) -> list[Routine]:
    heads = list(_ROUTINE.finditer(clean))
    out = []
    for idx, m in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(clean)
        out.append(Routine(m.group(1), m.start(), end, clean[m.start():end]))
    return out


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# --------------------------------------------------------------------------
# Block matching over the cleaned text.
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"[A-Za-z_]\w*|;|\S")
_OPENERS = {"begin", "case", "try", "record", "asm"}


def _tokens(text: str, start: int = 0) -> Iterator[tuple[str, int]]:
    for m in _TOKEN.finditer(text, start):
        yield m.group(0).lower(), m.start()


def try_sections(text: str, try_at: int) -> tuple[str, int, int] | None:
    """For the Try at ``try_at``: ("finally"|"except", section_start, end).

    section_start is where the Finally/Except keyword sits; end is the
    offset of the matching End.
    """
    depth = 0
    kind, section = None, -1
    for tok, pos in _tokens(text, try_at):
        if tok in _OPENERS:
            depth += 1
        elif tok == "end":
            depth -= 1
            if depth == 0:
                if kind is None:
                    return None
                return kind, section, pos
        elif depth == 1 and tok in ("finally", "except") and kind is None:
            kind, section = tok, pos
    return None


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

@dataclass
class Hit:
    rule: str
    severity: str
    offset: int          # in the full cleaned file
    message: str


_PRE = re.compile(
    r"\b(PCBServer|SchServer\s*\.\s*ProcessControl)\s*\.\s*PreProcess\b"
    r"[^;]*;", re.IGNORECASE)


def _post_for(server: str) -> re.Pattern:
    if server.lower().startswith("pcb"):
        return re.compile(r"\bPCBServer\s*\.\s*PostProcess\b", re.IGNORECASE)
    return re.compile(r"\bSchServer\s*\.\s*ProcessControl\s*\.\s*PostProcess\b",
                      re.IGNORECASE)


def rule_preprocess_try_finally(r: Routine) -> Iterator[Hit]:
    for m in _PRE.finditer(r.text):
        nxt = re.compile(r"\s*([A-Za-z_]\w*)").match(r.text, m.end())
        word = nxt.group(1).lower() if nxt else ""
        if word != "try":
            yield Hit("preprocess-needs-try-finally", "error", r.start + m.start(),
                      "PreProcess is not followed by Try; a failure before "
                      "PostProcess leaves 'a command is currently active'")
            continue
        sec = try_sections(r.text, nxt.start(1))
        if sec is None or sec[0] != "finally":
            yield Hit("preprocess-needs-try-finally", "error", r.start + m.start(),
                      "the Try after PreProcess has no Finally; PostProcess "
                      "must run on every exit")
            continue
        _, fin, end = sec
        if not _post_for(m.group(1)).search(r.text, fin, end):
            yield Hit("preprocess-needs-try-finally", "error", r.start + m.start(),
                      "the Finally after PreProcess does not call the "
                      "matching PostProcess")


_TENT = re.compile(r"\.\s*IsTenting(?:_Top|_Bottom)?\s*:=", re.IGNORECASE)
_MANUAL = re.compile(
    r"\.\s*SolderMaskExpansionMode\s*:=\s*eMaskExpansionMode_Manual\b",
    re.IGNORECASE)


def rule_tenting_manual_mask(r: Routine) -> Iterator[Hit]:
    first_manual = None
    mm = _MANUAL.search(r.text)
    if mm:
        first_manual = mm.start()
    for m in _TENT.finditer(r.text):
        if first_manual is None or first_manual > m.start():
            yield Hit("tenting-needs-manual-mask", "error", r.start + m.start(),
                      "IsTenting is written without SolderMaskExpansionMode := "
                      "eMaskExpansionMode_Manual before it; AD26 ignores it")


_RULE_ENABLED = re.compile(
    r"\b(\w*rule\w*)\s*\.\s*(Enabled|DRCEnabled)\s*:=\s*([^;]+);",
    re.IGNORECASE)
_READBACK = re.compile(r"\bReadBackRuleEnabled\s*\(", re.IGNORECASE)
_ADD_RULE = re.compile(r"\.\s*AddPCBObject\s*\(", re.IGNORECASE)
_FACTORY = re.compile(r"\bPCBRuleFactory\s*\(", re.IGNORECASE)


def rule_rule_enabled(r: Routine) -> Iterator[Hit]:
    reads_back = bool(_READBACK.search(r.text))
    for m in _RULE_ENABLED.finditer(r.text):
        if reads_back:
            continue
        value = m.group(3).strip().lower()
        tail = r.text[m.end():m.end() + 1500]
        # Enabling a rule the routine has just created (rule factory, then
        # AddPCBObject) sets a default before the rule exists on the board;
        # it is not a toggle of an existing rule.
        new_rule = (value == "true" and _ADD_RULE.search(tail)
                    and _FACTORY.search(r.text, 0, m.start()))
        if new_rule:
            continue
        yield Hit("rule-enabled-write", "error", r.start + m.start(),
                  f"{m.group(1)}.{m.group(2)} is written and never read back; "
                  "in AD26 this does not stick. Delete and recreate the "
                  "rule, or call ReadBackRuleEnabled and report a mismatch")


_RECORD_TYPES = ("TCoordRect", "TRect", "TPoint", "TCoordPoint",
                 "TLocation", "TBoundingRectangle")
_VAR_DECL = re.compile(
    r"^[ \t]*([A-Za-z_][\w \t,]*?)[ \t]*:[ \t]*(" + "|".join(_RECORD_TYPES) + r")[ \t]*;",
    re.IGNORECASE | re.MULTILINE)


def rule_record_field(r: Routine) -> Iterator[Hit]:
    names = set()
    head = re.search(r"\bbegin\b", r.text, re.IGNORECASE)
    decl = r.text[:head.start()] if head else r.text
    for m in _VAR_DECL.finditer(decl):
        for n in m.group(1).split(","):
            n = n.strip()
            if n and re.fullmatch(r"[A-Za-z_]\w*", n):
                names.add(n.lower())
    if not names:
        return
    body = head.start() if head else 0
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names))) +
                     r")\s*\.\s*(\w+)\s*:=", re.IGNORECASE)
    for m in pat.finditer(r.text, body):
        whole = re.compile(r"\b" + re.escape(m.group(1)) + r"\s*:=",
                           re.IGNORECASE)
        if whole.search(r.text, body, m.start()):
            # Materialized: the local was assigned a whole record earlier
            # (Loc := Rect.Location), and a field write on it then works.
            # eda-agent measured this; the failure is the field write on
            # a never-assigned local.
            continue
        yield Hit("record-field-assignment", "error", r.start + m.start(),
                  f"{m.group(1)}.{m.group(2)} := writes a field of a record "
                  "local that was never assigned; DelphiScript stops with "
                  "'Undeclared identifier'. Read the record from its "
                  "property first, or use plain TCoord variables")


_FIRST = re.compile(
    r"\b(\w+)\s*\.\s*(FirstPCBObject|FirstSchObject|First_SchObject)\b",
    re.IGNORECASE)
_PCB_ITERATOR = re.compile(r"\bFirstPCBObject\b", re.IGNORECASE)
_MUTATIONS = re.compile(
    r"\.\s*(RemovePCBObject|AddPCBObject|MoveByXY|MoveToXY|RotateBy|"
    r"FlipComponent|FlipXY|RotateAroundXY)\s*\(|"
    r"\b\w+\s*\.\s*(Width|SolderMaskExpansion|SolderMaskExpansionFromHoleEdge|"
    r"HoleSize|Size|Rotation|Layer|Net|X|Y|X1|Y1|X2|Y2)\s*:=",
    re.IGNORECASE)


_BOARD_ITER = re.compile(
    r"\b(\w+)\s*:=\s*\w+\s*\.\s*(BoardIterator_Create|SpatialIterator_Create)\b",
    re.IGNORECASE)


_STRUCTURAL = re.compile(
    r"\.\s*(RemovePCBObject|AddPCBObject)\s*\(", re.IGNORECASE)
_OBJ_CALL = re.compile(
    r"\b(\w+)\s*\.\s*(MoveByXY|MoveToXY|RotateBy|FlipComponent|FlipXY|"
    r"RotateAroundXY)\s*\(", re.IGNORECASE)
_OBJ_WRITE = re.compile(
    r"\b(\w+)\s*\.\s*(Width|SolderMaskExpansion|SolderMaskExpansionFromHoleEdge|"
    r"HoleSize|Size|Rotation|Layer|Net|X|Y|X1|Y1|X2|Y2|XLocation|YLocation)"
    r"\s*:=", re.IGNORECASE)


def _walk_windows(text: str, it: str) -> Iterator[tuple[int, int]]:
    """(start, end) of each walk of iterator ``it``: from a First call to
    the last Next call before the iterator is restarted or destroyed."""
    first = re.compile(r"\b" + re.escape(it) + r"\s*\.\s*FirstPCBObject\b",
                       re.IGNORECASE)
    nxt = re.compile(r"\b" + re.escape(it) + r"\s*\.\s*NextPCBObject\b",
                     re.IGNORECASE)
    stop = re.compile(r"\b(?:BoardIterator_Destroy|SpatialIterator_Destroy)"
                      r"\s*\(\s*" + re.escape(it) + r"\b", re.IGNORECASE)
    firsts = [m.end() for m in first.finditer(text)]
    for i, begin in enumerate(firsts):
        limit = firsts[i + 1] if i + 1 < len(firsts) else len(text)
        d = stop.search(text, begin, limit)
        if d:
            limit = d.start()
        last = None
        for m in nxt.finditer(text, begin, limit):
            last = m.end()
        if last is not None:
            yield begin, last


def rule_mutate_while_iterating(r: Routine) -> Iterator[Hit]:
    board_iters = {m.group(1) for m in _BOARD_ITER.finditer(r.text)}
    for it in sorted(board_iters, key=str.lower):
        for begin, end in _walk_windows(r.text, it):
            window = r.text[begin:end]
            # The walked objects: whatever First/Next is assigned to, and
            # anything assigned straight from one of those (Track := Obj).
            walked = {m.group(1).lower() for m in re.finditer(
                r"\b(\w+)\s*:=\s*" + re.escape(it) +
                r"\s*\.\s*(?:First|Next)PCBObject\b",
                r.text[max(0, begin - 200):end], re.IGNORECASE)}
            for m in re.finditer(r"\b(\w+)\s*:=\s*(\w+)\s*;", window):
                if m.group(2).lower() in walked:
                    walked.add(m.group(1).lower())
            hits = [mm.start() for mm in _STRUCTURAL.finditer(window)]
            hits += [mm.start() for mm in _OBJ_CALL.finditer(window)
                     if mm.group(1).lower() in walked]
            hits += [mm.start() for mm in _OBJ_WRITE.finditer(window)
                     if mm.group(1).lower() in walked]
            for pos in sorted(set(hits)):
                yield Hit("mutate-while-iterating", "error", r.start + begin + pos,
                          f"the board is changed inside the walk of {it}; "
                          "collect into a TInterfaceList, destroy the "
                          "iterator, then edit")


_JUMP = re.compile(r"\b(Continue|Break)\b", re.IGNORECASE)


def rule_continue_break_in_try(r: Routine) -> Iterator[Hit]:
    for m in _PRE.finditer(r.text):
        nxt = re.compile(r"\s*([A-Za-z_]\w*)").match(r.text, m.end())
        if not nxt or nxt.group(1).lower() != "try":
            continue
        sec = try_sections(r.text, nxt.start(1))
        if not sec:
            continue
        guarded = r.text[nxt.end(1):sec[1]]
        for j in _JUMP.finditer(guarded):
            # A jump inside a loop that is itself inside the guarded
            # section stays in the section; only a loop OUTSIDE the
            # section could carry control past PostProcess. Flag both,
            # as a warning, until a live run says which builds are safe.
            yield Hit("continue-break-in-try", "warn",
                      r.start + nxt.end(1) + j.start(),
                      f"{j.group(1)} inside the section guarded by PreProcess")


RULES: tuple[Callable[[Routine], Iterator[Hit]], ...] = (
    rule_preprocess_try_finally,
    rule_tenting_manual_mask,
    rule_rule_enabled,
    rule_record_field,
    rule_mutate_while_iterating,
    rule_continue_break_in_try,
)


def scan(text: str) -> list[Hit]:
    clean = clean_source(text)
    hits: list[Hit] = []
    for r in routines(clean):
        for rule in RULES:
            hits.extend(rule(r))
    return hits
