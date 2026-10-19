# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""Defects in the existing footprint tools, each pinned by its behaviour.

Every test here fails on the code before the fix:

* lib_create_standard_footprint rounded silkscreen and courtyard track
  coordinates to whole mils;
* easyeda_create_standard_footprint refused every tab and bga, and could
  not pass an exposed pad, a courtyard, or a body through;
* easyeda_audit_footprint_vs_datasheet read holes as 0, never matched a
  shape name, ignored pad rotation, read a polygon's side count as its
  height, and never read paste expansion;
* lib_audit_footprint_vs_datasheet turned "footprint not found" into a
  pad count failure, or raised;
* kicad_generate_pcb and kicad_generate_project used placeholder boxes
  without saying so.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.test_cross_validate import get_batch_field, next_batch_op


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _ops(payload: str) -> list[str]:
    out, rest = [], payload
    while rest:
        op, rest = next_batch_op(rest)
        if op:
            out.append(op)
    return out


# --- lib_create_standard_footprint ------------------------------------------


class _Recorder:
    def __init__(self, reply=None, raises=None):
        self.calls = []
        self.reply = reply
        self.raises = raises

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if self.raises is not None:
            raise self.raises
        if self.reply is not None:
            return self.reply
        return {"added": 1, "failed": 0, "total": 1}


def _library_tools(monkeypatch, bridge):
    from altium_agent.tools import library as lib

    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    lib.register_library_tools(mcp)
    return mcp.tools


def test_standard_footprint_tracks_keep_fractional_mils(monkeypatch):
    from altium_agent.design.footprint_gen import generate_footprint

    bridge = _Recorder()
    tools = _library_tools(monkeypatch, bridge)
    kw = dict(pitch=59.055, pad_w=31.496, pad_h=39.37, body_w=78.74,
              body_h=49.213, courtyard=9.843)
    asyncio.run(tools["lib_create_standard_footprint"](
        name="R_1206", family="chip", pin_count=2, **kw))
    tracks = next(p for c, p in bridge.calls
                  if c == "library.add_footprint_tracks")["tracks"]
    sent = [{k: float(get_batch_field(op, k))
             for k in ("x1", "y1", "x2", "y2", "width")}
            for op in _ops(tracks)]
    geom = generate_footprint("chip", 2, **kw)
    want = [{k: float(t[k]) for k in ("x1", "y1", "x2", "y2", "width")}
            for t in geom.all_tracks()]
    assert sent == want
    assert any(v["x1"] == pytest.approx(-39.37) for v in sent), (
        "the body edge was rounded to a whole mil")


# --- easyeda_create_standard_footprint ---------------------------------------














# --- easyeda_audit_footprint_vs_datasheet ---------------------------------------

_SRC = {"datasheet_url": "https://example.invalid/ds.pdf",
        "reference": "Fig. 1, p. 2", "part_number": "TEST"}
MIL = 1 / 0.0254
















# --- lib_audit_footprint_vs_datasheet ------------------------------------------

_SPEC = {"source": _SRC, "dual_row": {"count": 8, "pitch": 1.27, "span": 5.4,
                                      "pad_w": 1.55, "pad_h": 0.6}}


def test_altium_audit_of_a_missing_footprint_is_not_a_pad_count(monkeypatch):
    from altium_agent.bridge.exceptions import PreconditionError

    bridge = _Recorder(raises=PreconditionError(
        "Footprint not found: NOPE", code="NO_FOOTPRINT"))
    tools = _library_tools(monkeypatch, bridge)
    out = asyncio.run(tools["lib_audit_footprint_vs_datasheet"](
        footprint_name="NOPE", spec_json=_SPEC))
    assert out["ok"] is False and "could not read footprint" in out["reason"]
    assert out["findings"] == [] and out["error_code"] == "NO_FOOTPRINT"


def test_altium_audit_of_a_refusal_reply_is_not_a_pad_count(monkeypatch):
    bridge = _Recorder(reply={"success": False, "error": {
        "code": "NO_FOOTPRINT", "message": "Footprint not found: NOPE"}})
    tools = _library_tools(monkeypatch, bridge)
    out = asyncio.run(tools["lib_audit_footprint_vs_datasheet"](
        footprint_name="NOPE", spec_json=_SPEC))
    assert out["ok"] is False and "not found" in out["reason"]
    assert not any(f.get("check") == "pad_count" for f in out["findings"])


def test_altium_audit_of_a_real_empty_footprint_still_reports_it(monkeypatch):
    bridge = _Recorder(reply={"name": "EMPTY", "pad_count": 0, "pads": []})
    tools = _library_tools(monkeypatch, bridge)
    out = asyncio.run(tools["lib_audit_footprint_vs_datasheet"](
        footprint_name="EMPTY", spec_json=_SPEC))
    assert out["ok"] is False
    assert any(f["check"] == "pad_count" for f in out["findings"])


# --- kicad_generate_pcb / kicad_generate_project -------------------------------

_PLAN = {"spec": "t", "summary": "t", "sheets": [{"name": "main"}],
         "zones": [], "parts": [
             {"refdes": "R1", "lib_ref": "R", "value": "10k",
              "footprint": "NoSuchLib:R_0603", "status": "existing"},
             {"refdes": "R2", "lib_ref": "R", "value": "1k",
              "status": "existing"}],
         "nets": [{"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                                         {"refdes": "R2", "pin": "1"}]}],
         "bom": [], "design_rules": [], "open_questions": []}








