# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro tests/test_formats.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
from __future__ import annotations

import struct

from altium_agent.offline.files.ini import IniFile
from altium_agent.offline.files.prjpcb import read_project
from altium_agent.offline.files.records import (
    iter_records,
    parse_length_mil,
    parse_properties,
    sch_coord_to_mil,
    text_records,
)


def _rec(text: str, kind: int = 0) -> bytes:
    payload = text.encode("cp1252") + b"\x00"
    return struct.pack("<I", len(payload) | (kind << 24)) + payload


def test_iter_text_and_binary_records():
    raw = _rec("|RECORD=1|NAME=A") + struct.pack("<I", 3 | (1 << 24)) + b"\x01\x02\x03" + _rec("|RECORD=2")
    recs = list(iter_records(raw))
    assert [r.kind for r in recs] == [0, 1, 0]
    assert [r["RECORD"] for r in text_records(raw)] == ["1", "2"]


def test_rules_prefix_bytes():
    raw = b"\x01\x00" + _rec("|NAME=Clearance|GAP=10mil")
    (props,) = list(text_records(raw, prefix_bytes=2))
    assert props["NAME"] == "Clearance"


def test_properties_first_wins_and_utf8_override():
    props = parse_properties("|name=one|NAME=two|%UTF8%DESC=" + "µF".encode("utf-8").decode("cp1252") + "|DESC=?F")
    assert props["NAME"] == "one"
    assert props["DESC"] == "µF"


def test_units():
    assert parse_length_mil("60384.8426mil") == 60384.8426
    assert abs(parse_length_mil("0.254mm") - 10.0) < 1e-9
    assert sch_coord_to_mil("54", "50000") == 545.0


def test_ini_numbered_sections():
    ini = IniFile.parse("[Document2]\nDocumentPath=b.SchDoc\n[Document1]\nDocumentPath=a.SchDoc\n[Design]\nX=1=2\n")
    assert [s.get("DocumentPath") for s in ini.numbered("Document")] == ["a.SchDoc", "b.SchDoc"]
    assert ini.section("design").get("x") == "1=2"


def test_read_project(tmp_path):
    (tmp_path / "Main.SchDoc").write_bytes(b"")
    prj = tmp_path / "Board.PrjPcb"
    prj.write_text(
        "[Design]\nHierarchyMode=2\nOutputPath=out\n"
        "[Document1]\nDocumentPath=Main.SchDoc\n"
        "[Document2]\nDocumentPath=Board.PcbDoc\n"
        "[Parameter1]\nName=Revision\nValue=B\n"
        "[ProjectVariant1]\nDescription=Lite\nVariationCount=2\n"
        "Variation1=Designator=R5|UniqueId=\\AAA|Kind=1|AlternatePart=\n"
        "Variation2=Designator=U2|UniqueId=\\BBB|Kind=2|AlternatePart=LM317\n",
        encoding="utf-8",
    )
    p = read_project(prj)
    assert [d.kind for d in p.documents] == ["SCH", "PCB"]
    assert p.documents[0].exists and not p.documents[1].exists
    assert p.parameters == {"Revision": "B"}
    v = p.variants[0]
    assert v.name == "Lite"
    assert [(c.designator, c.kind) for c in v.changes] == [("R5", "not_fitted"), ("U2", "alternate")]
    assert p.summary()["hierarchy_mode"] == "2"
