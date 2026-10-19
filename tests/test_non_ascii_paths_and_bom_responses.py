# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""A non-ASCII profile path and a response with a byte-order mark both read.

Both showed up on a machine with a non-English locale.
"""

from __future__ import annotations

import inspect

from altium_agent.config import _pointer_encoding
from altium_agent.diag import health
from altium_agent.diag.checks import Status


def test_pointer_with_an_accented_path_is_read_not_crashed(tmp_path,
                                                          monkeypatch):
    pointer = tmp_path / "workspace.txt"
    pointer.write_bytes("C:/Users/Jos\u00e9/Altium Agent/workspace".encode(
        _pointer_encoding()))
    monkeypatch.setenv("ALTIUM_AGENT_POINTER_FILE", str(pointer))
    check = health._check_pointer_file()
    # It may legitimately disagree with the configured workspace; what it
    # must not do is fail to decode the file.
    assert check.name != "workspace pointer file readable", check.message


def test_an_undecodable_pointer_is_reported_not_raised(tmp_path, monkeypatch):
    pointer = tmp_path / "workspace.txt"
    pointer.write_bytes(b"\x81\x8d\x8f\x90\x9d")
    monkeypatch.setenv("ALTIUM_AGENT_POINTER_FILE", str(pointer))
    import altium_agent.config as config
    monkeypatch.setattr(config, "_pointer_encoding", lambda: "utf-8")
    check = health._check_pointer_file()
    assert check.status == Status.FAIL
    assert check.name == "workspace pointer file readable"


def test_the_response_reader_tolerates_a_byte_order_mark():
    from altium_agent.bridge import altium_bridge
    src = inspect.getsource(altium_bridge)
    reads = [ln for ln in src.splitlines()
             if "open(response_path" in ln and '"r"' in ln]
    assert reads, "the response read moved; point this test at it"
    assert all('encoding="utf-8-sig"' in ln for ln in reads), reads
