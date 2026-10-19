# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""Real-Altium integration tests.

These tests require:
  - Altium Designer running
  - The AltiumAgent.PrjScr script project loaded and StartMCPServer running
  - The fixture project at tests/integration/fixtures/AltiumAgentTest.PrjPcb
    open and focused

Tests are skipped automatically when these preconditions aren't met.
Set ALTIUM_AGENT_INTEGRATION=1 in the environment to require them (CI).
"""
