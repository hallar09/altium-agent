# Placement and routing benchmark

Finished boards laid out by people, stripped, redone by Altium Agent (its
defaults, or Claude driving its tools with a skill) and scored against the
person's board. It exists so a change to a skill or a tool can be measured
instead of judged by eye.

`layout/bench.py` measures the in-house engines on boards read from a live
Altium. This one (`altium_agent.bench`) measures the flow a user gets, from
the saved files, with no Altium running.

## Run it

```powershell
.venv\Scripts\python -m altium_agent.bench prepare --tasks place route
.venv\Scripts\python -m altium_agent.bench run "<run folder>" --runner baseline --jobs 4
.venv\Scripts\python -m altium_agent.bench run "<run folder>" --runner agent --skill skills\placement --label placement-v1 --only wifi-place
.venv\Scripts\python -m altium_agent.bench report "<run folder>"
```

`prepare` prints the run folder (under `%USERPROFILE%\Altium Agent\bench\runs`).
The agent runner uses the `claude` CLI, which must be signed in once
(`claude`, then `/login`); the desktop app's sign-in is separate.
Every runner's results land in the same run, under its `--label`, so
`report` puts a skill change next to the baseline and the previous version.

## The boards

`boards.json`: Altium's own examples (WiFi, Bluetooth Sentinel, Kame FMU,
SpiritLevel SL1, a DDR4 SODIMM), from easy (tier 1) to hard (tier 3). They
are only read; every run works on copies. For each board the manifest says
which parts a brief would pin (`fixed`, `auto` by default: locked parts,
connectors, mounting holes, parts with no connected pin) and answers the
placer's antenna question from the person's own board.

## The tasks

- **place**: the routing is stripped and every part that is not fixed is
  moved beside the board, where Import Changes leaves it. The result is a
  placement plan (`place_session_export`'s or `place_plan`'s JSON). It is
  scored with `place_check`'s rules (overlaps, parts off the board,
  decoupling distance, connectors off the edge), half-perimeter wirelength,
  and a **trial route** by the in-house engine (power fan-out first): a
  placement that cannot be routed is not a good placement.
- **route**: the routing is stripped and the person's placement is kept.
  The result is a routing: the SES a routing session exports, or a routed
  board saved by Altium (`score --pcb`).

How the copy is stripped without writing a PcbDoc: `prepare` writes
`mask.json`, and with `ALTIUM_AGENT_BENCH_MASK` pointing at it, the offline
reader (`read_pcbdoc`, which every offline tool goes through) returns the
copy as its task. The person's figures go in `answers/`, outside the folder
the agent works in.

## The runners

- **baseline**: no model. `place_plan` with the fixed parts kept, or the
  in-house engine on the stripped board (power fan-out, the rest, stitching).
  What a skill has to beat to be worth its tokens.
- **agent**: `claude -p` in the task folder, reading `BRIEF.md`, with only
  the AltiumAgent server (pointed at the mask), only the skills given with
  `--skill` (copied into the task folder; `--setting-sources project` keeps
  your installed skills out), file tools limited to the folder, a budget
  (`--budget`, USD) and a timeout. The transcript, cost, turns and tool
  calls are kept in `transcripts/` and the result.

## Reading the numbers

Every figure is computed the same way on the person's board and on the
result, and ratios are result / person (1.0 = as the person did).

- `routed` is the share of connections the copper makes
  (`bench/connectivity.py`: pads, tracks, arcs, vias, pours as their
  outlines, planes). `human routed` is the same model on the person's
  board: its ceiling there. It reads 100% on WiFi, Bluetooth and the
  SODIMM, 99.7% on SL1 (a net tie) and 97.5% on Kame (pours whose arcs the
  reader does not expand).
- Vias and length count only what a router lays down; footprint copper is
  left out on both sides.
- The person's board is the yardstick, not the target: less wire than the
  person used is not better if the result fails a check their board passes.

## Not covered yet

- **The live flow.** `place_apply`, `route_import_ses` and `pcb_run_drc`
  need a running Altium, so landing the result and Altium's own DRC are not
  run here. A board routed in Altium can be scored with
  `score <run> <board>-route --label <name> --pcb <saved .PcbDoc>`.
- **DRC.** Results are not checked by Altium's DRC. The in-house engine
  routes to the board's rules (and a routing session reports the in-house
  exact DRC); an Altium result should be checked with `pcb_run_drc` before
  it is scored.
- **Datasheet questions.** The brief gives the antenna keep-outs, so the
  placement task measures placement, not datasheet lookup.
