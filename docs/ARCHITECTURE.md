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
| `tools/` | MCP tool registration by group; `metadata.py` assigns the badges; `agent.py` holds the Altium Agent additions |
| `design/` | the design harness: sessions and journal, DesignPlan, circuit blocks, schematic placer, discipline, guidance |
| `routing/` | route planning and repair today; the routing broker (phase d) |
| `placement/` | placement engines; placement to a profile (phase c) |
| `reference/` | reference designs and house style: `ref_profile`, `ref_compare` (phase c) |
| `analysis/` | read-only analysis: DRC report summary; altium-mcp-pro's SI/PI/EMC/DFM suites (phase b) |
| `offline/` | headless readers for .SchDoc / .PcbDoc / libraries (olefile) |
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
| (b) Analysis and offline | altium-mcp-pro's offline readers, SI/PI/EMC/thermal/DFM engines and fab profiles; DRC-with-pours prompt in the dialog driver | next |
| (c) Placement and reference | Brass placement and checks, `ref_profile` / `ref_compare`, `place_` tools to a house style | |
| (d) Routing broker | `route_` broker over Altium's interactive router, Situs and external Freerouting; pairs first; via styles with tenting read-back; converge clashes | |
| (e) Render-and-review, checkpoints | board renders for review, design_status, journal-driven resume | |
| (f) Acceptance | live verification run; Kame_FMU_v2 re-layout, WiFi_Card new layout, USB HID macro stick from scratch; skills `autodesign`, `relayout`, `review` | |
