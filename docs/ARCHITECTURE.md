# Architecture

## One transport

```
Claude ── MCP stdio ──> altium_agent.server ──> bridge/ ──(files)──> Dispatcher.pas in Altium
                              │                     request_<id>.json   (TTimer poll in
                              └─ cli.py (same code) response_<id>.json   StatusForm.pas)
```

The DelphiScript project `scripts/altium/AltiumAgent.PrjScr` is started once
per Altium session (`StartMCPServer`). Its timer polls the workspace
(`%USERPROFILE%\Altium Agent\workspace`, pointer in
`C:\ProgramData\altium-agent\workspace-path.txt`) for request files, runs the
named handler and writes the response. The Python side (`bridge/`) writes
requests with per-request ids (protocol v2), shares the queue fairly between
Claude sessions (session slots), keeps the bridge alive and detects a dead,
halted or stale script (script stamps). This is the only way Altium Agent
talks to Altium: nothing launches `X2.EXE -RScriptingSystem:RunScript` per
call and nothing starts Altium.

UI work Altium has no script API for (answering a known dialog, reading a
menu) goes through `ui/`, which drives the already-running Altium's windows
and is badged `modal` or `partial`.

## Packages (`src/altium_agent/`)

| Package | What lives there |
|---|---|
| `bridge/` | the polling-bridge client, protocol, session slots, process manager |
| `schemas/` | request and response models |
| `tools/` | MCP tool registration by group; `metadata.py` assigns the badges; `agent.py` holds the Altium Agent additions; `offline/` the ported file tools, published under Altium Agent names by `publish.py` |
| `design/` | the design harness: sessions and journal (with rewinds), DesignPlan, circuit blocks, schematic placer, discipline, guidance; gate reviews (`gate_review.py`), stage checkpoints and undo (`stage_checkpoints.py`), `design_status` (`status.py`) |
| `render/` | SVG renderers for boards and sheets, the rasterizer (Edge/Chromium), and `file_geometry.py`, which builds their input from the saved files with a review overlay |
| `routing/` | the inherited grid router and DRC repair planner; the broker (`broker.py`), clash convergence (`converge.py`), via styles and tenting (`via_style.py`), Specctra DSN/SES (`specctra.py`, `ses_import.py`), the external Freerouting runner (`freerouting.py`) and `service.py` for the `route_` tools |
| `placement/` | the inherited force-directed solver (`autoplace.py`); the Brass placer port (`footprints`, `shape`, `antenna`, `placer`), plan checks (`check.py`) and the tool-facing `service.py` |
| `reference/` | house style: `profile.py` measures a board and derives placer levers, `compare.py` flags departures |
| `analysis/` | read-only analysis: DRC report summary, the design model, ERC, schematic/PCB sync, DFM, SI, copper and ratsnest, review (from altium-mcp-pro) |
| `analysis/engines/` | SI/PI/EMC/thermal engines and fab profiles (kicad-mcp-pro via altium-mcp-pro) |
| `offline/` | headless readers: eda-agent's schematic readers, and `offline/files/` for PrjPcb, PcbDoc, SchDoc, SchLib/PcbLib and OutJob (altium-mcp-pro); `context.py` holds the read-anywhere, write-only-to-outputs rule |
| `layout/` | the inherited board layout engine |
| `diag/` | `doctor`, self-heal diagnosis and recovery, live-verification records |
| `core/` | shared policy: backend, units policy, CLI argument handling |
| `dashboard/` | the local web dashboard and live view |
| `ui/` | dialog driver, window and control reading (Win32, MSAA, UIA) |
| `server.py` / `cli.py` | the MCP server entry point and the `altium-agent` CLI |

`scripts/altium/` holds the Pascal units, `Dispatcher.pas`, `build.py` (lint,
then bundle into `AltiumAgent.pas`), `lint.py` and `lint_hardening.py`.

## Testing

- **Offline**: pure-Python tests, the bulk of the suite.
- **Simulator**: `tests/altium_simulator.py` answers bridge commands the way
  Altium does, including its failure modes (for example a rule enable that
  does not stick), so handlers and tools are exercised without Altium.
- **Cross-checks**: the Pascal sources are linted and checked against the
  Python side (every command a tool sends has a dispatcher route; the wheel
  ships every unit).
- **Live**: docs/RELEASE_VERIFICATION.md. A tool becomes `live_verified` only
  through `altium-agent verify record` during that run.

## Phases

| Phase | Content | State |
|---|---|---|
| (a) Foundation | fork and cut-down of eda-agent, groups and badges, mm policy, hardening lint and Pascal fixes, verified rule enable, self-heal, DRC report, doctor, generated reference, docs | done, offline only |
| (b) Analysis and offline | altium-mcp-pro's offline readers, SI/PI/EMC/thermal/DFM engines, gates and fab profiles as ~95 `read`/`analyze_` tools; DRC checker and polygon prompt answered by the dialog driver | done, offline; DRC dialogs await live verification |
| (c) Placement and reference | Brass placer ported to Python (numpy), house-style `ref_profile` / `ref_compare`, `place_plan` / `place_check` offline, `place_apply` live | done; `place_apply` awaits live verification (A13) |
| (d) Routing broker | `route_broker_plan` (pairs first, critical nets, bulk by Freerouting or in-house, pours left to pours, hand routing locked), external Freerouting jobs with DSN/SES import, `route_converge`, via styles with tenting read-back from the file | done; `route_freerouting`, `route_import_ses`, `route_set_via_tenting` await live verification (A14, A15) |
| (e) Render-and-review, checkpoints | offline board and sheet renders with numbered findings and ratsnest; `design_gate_review` (pictures + numbers + rubric per gate, journaled); a checkpoint on every stage entry and `design_undo_to_stage`; `design_status` | done; `design_undo_to_stage`'s close/reopen in Altium awaits live verification (A16) |
| (f) Acceptance | skills `autodesign`, `relayout`, `review`; Situs as the broker's finisher; `route_refcopy`; per-project engine policy (`altium_agent.json`); the runbook (docs/ACCEPTANCE.md) and the gap list (docs/GAPS.md) | the four acceptance runs are live and wait for the user's Altium (docs/ACCEPTANCE.md); nothing is live-verified yet |
