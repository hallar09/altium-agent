# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the offline file readers.

Altium's own example projects are the regression corpus. They are
Altium's files, so they are never copied into this repository: the tests
read them where Altium installed them, or where ALTIUM_AGENT_EXAMPLES /
ALTIUM_AGENT_KAME point, and skip when they are absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Where Altium puts its shared documents: %PUBLIC%\Documents\Altium.
_ALTIUM_PUBLIC = Path(os.environ.get("PUBLIC") or Path(os.environ.get("SystemDrive", "C:") + "\\") / "Users" / "Public") \
    / "Documents" / "Altium"

EXAMPLES = Path(os.environ.get("ALTIUM_AGENT_EXAMPLES") or _ALTIUM_PUBLIC / "AD26" / "Examples")
SPIRIT = EXAMPLES / "SpiritLevel-SL1" / "SL1 Xilinx Spartan-IIE PQ208 Rev1.02.PrjPcb"
WIFI = EXAMPLES / "Mini PC" / "Mini PC - WiFi" / "WiFi_miniPCIe.PrjPcb"
KAME = Path(os.environ.get("ALTIUM_AGENT_KAME")
            or _ALTIUM_PUBLIC / "Sample - Kame_FMU" / "Kame_FMU.PrjPcb")

requires_examples = pytest.mark.skipif(not SPIRIT.exists(), reason="Altium AD26 example projects not installed")
requires_wifi = pytest.mark.skipif(not WIFI.exists(), reason="Altium Mini PC WiFi example not installed")
requires_kame = pytest.mark.skipif(not KAME.exists(), reason="Kame_FMU sample not installed")


@pytest.fixture(autouse=True)
def isolated_offline_context(tmp_path, monkeypatch):
    """Writes go to a temporary output folder; no default project or profile leaks."""
    from altium_agent.offline import context

    monkeypatch.setenv("ALTIUM_AGENT_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("ALTIUM_AGENT_PROJECT", raising=False)
    monkeypatch.delenv("ALTIUM_AGENT_DFM_PROFILE", raising=False)
    context.set_active_project(None)
    context.set_dfm_profile(None)
    yield
    context.set_active_project(None)
    context.set_dfm_profile(None)
