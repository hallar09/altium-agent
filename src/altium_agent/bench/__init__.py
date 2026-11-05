# SPDX-License-Identifier: Apache-2.0
"""The placement and routing benchmark: finished boards, stripped, redone,
scored against the person who laid them out.

layout/bench.py measures the in-house engines on boards read from a live
Altium. This package measures the flow a user gets: the AltiumAgent tools
(and Claude driving them, with or without a skill) on copies of real
projects, from the saved files, with no Altium needed:

* corpus.py     the boards (benchmarks/boards.json) and what a brief pins
* transform.py  strip routing, stage parts, apply a plan (in memory)
* mask.py       show the tools a copy as its task (ALTIUM_AGENT_BENCH_MASK)
* prepare.py    a run folder: copies, masks, briefs, the person's answers
* run.py        the baseline and agent runners, and scoring a result
* score.py      placement checks and wirelength, trial routes, routing figures
* connectivity.py  which pads the copper joins, one model for both sides
* report.py     the tables

``python -m altium_agent.bench --help`` for the commands.
"""
