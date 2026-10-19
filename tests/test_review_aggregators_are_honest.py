# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Modified for Altium Agent (2026): see docs/PROVENANCE.md
"""A review that read nothing must not read as a clean design.

Each backend has a one-call review that fans out to many checks and
folds the answers into a summary. That summary is the thing a reviewer
reads, and it is the one place where "I could not look" and "I looked
and it was fine" collapse into the same words if nobody is careful.

They collapsed. ``easyeda_review_board`` answered "23 audits run, 0
violations" with no editor connected, because audits_run reported the
size of the audit registry, which is a fact about the code rather than
a measurement of the board. The refusals were listed correctly further
down, so the detail was right and the headline said the opposite.

The other two were checked because of it, which is the point of writing
this once for all three: ``kicad_full_review`` reported ok:True with
every section failed, and ``design_review_snapshot`` said nothing at
all when it fetched nothing, leaving an empty list to be noticed.

The tests are shaped around the FAILURE, not the tool: each aggregator
is driven with everything underneath it broken, and the reply must not
be mistakable for a healthy design.
"""

from __future__ import annotations

import asyncio

import pytest


class _Mcp:
    def __init__(self):
        self.tools: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _healthy_looking(reply: dict) -> bool:
    """Would a reviewer skimming this conclude the design is fine?

    Only the fields a summary is read by. Detail buried under a key
    like `refused` is not what someone acts on when the headline says
    the run succeeded.
    """
    if reply.get("ok") is True:
        return True
    # Altium's aggregator carries no ok, so it is judged on whether it
    # says anywhere that nothing was read.
    if "ok" not in reply:
        words = " ".join(str(v) for k, v in reply.items()
                         if k.startswith("_")).lower()
        return "nothing was read" not in words
    return False



def test_the_altium_review_says_so_when_it_fetched_nothing(monkeypatch):
    """Driven, not read.

    A first version of this checked that the source contained the key,
    which is the kind of guard that survives the defect it exists to
    catch: several branches build this reply, and a structural check
    cannot tell whether the one that runs is the one it matched. So the
    tool is called with every section failing and the reply is read.
    """
    from altium_agent.tools import review as review_mod

    mcp = _Mcp()
    review_mod.register_review_tools(mcp)

    class _FailingBridge:
        """Answers every request by failing, as an unreachable editor
        would once each section tried to use it."""

        def send_command(self, *a, **k):
            raise RuntimeError("Altium is not reachable")

        def __getattr__(self, name):
            def _boom(*a, **k):
                raise RuntimeError("Altium is not reachable")
            return _boom

    monkeypatch.setattr(review_mod, "get_bridge", lambda: _FailingBridge())

    reply = asyncio.run(mcp.tools["design_review_snapshot"]())
    assert isinstance(reply, dict)

    assert reply.get("_sections_fetched") == [], (
        f"sections were fetched from a bridge that fails everything: "
        f"{reply.get('_sections_fetched')}")
    assert not _healthy_looking(reply), (
        f"a review that fetched nothing does not say so anywhere a "
        f"reader would notice: {sorted(reply)}")


