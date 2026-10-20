# Acceptance runbook

The four acceptance runs, live in Altium Designer with no computer-use
clicks. They are run by an AI client with the AltiumAgent MCP server
registered (see the README), on copies in a folder of the user's; nothing
under `%PUBLIC%\Documents\Altium` is edited. Record each run's result and
every tool problem in `docs/ACCEPTANCE_LOG.md` (date, Altium build, what
passed, what did not, the tool and its arguments).

Before any run: `altium-agent doctor` passes; `app_diagnose` is clean;
RELEASE_VERIFICATION Part A has been run far enough to trust the tools the
run depends on (at least A1 to A4, A11 and A13 to A17).

## 1. Install and tests

| Check | Pass when |
|---|---|
| `altium-agent doctor` | every check passes with exactly one Altium running and the bridge started |
| `pytest -n 8` (with `ALTIUM_AGENT_EXAMPLES`, `ALTIUM_AGENT_KAME`) | all pass offline |
| `python scripts/gen_tool_reference.py` | `docs/TOOL_REFERENCE.md` unchanged (the test checks it) |

## 2. Re-layout: Kame_FMU_v2 from the Kame_FMU sample

Skill: `relayout`. Reference: `%PUBLIC%\Documents\Altium\Sample - Kame_FMU`
(read only). Target: a copy in the user's folder (BoardKit's `projects\Kame_FMU_v2`).

1. `ref_profile(project=<sample .PrjPcb>, name="Kame_FMU")`.
2. Copy the sample's project files to the target folder; open the copy.
   `design_session_start(requirement="Kame_FMU_v2 re-layout", project=<copy>, profile="Kame_FMU")`.
3. Reference placement (the designer's part positions): routing only is redone.
   `pcb_unroute(expect_file, include_locked=false)` on the copy. Set
   `pcb_polygon_repour_mode("never")` for the routing pass. **Gap**: shelving
   the pours (BoardKit: Situs reached 54% with them in place, 74%+ shelved) has
   no tool yet; Freerouting does not need it (pours go to the DSN as planes),
   Situs does, so until it exists either shelve them by hand (Tools > Polygon
   Pours > Shelve) and say so in the log, or let Freerouting do the bulk.
4. `route_broker_plan(project=<copy>, profile="Kame_FMU", reference=<sample .PcbDoc>)`:
   expect `reference_placement: true` and a first step copying U12's escape.
   Follow the steps; `route_converge` copies clashing nets from the reference.
5. Restore the pours, `pcb_repour_polygons`, `route_via_style("Kame_FMU")`,
   `route_set_via_tenting` as needed, `pcb_run_drc`.

| Pass when | Measured by |
|---|---|
| 0 unrouted | `design_status` (unrouted_connections) and DRC Un-Routed Net |
| DRC equals the sample's own: 1 dead no-net pour NONET_L06_P008 | `analyze_drc_report` on both boards' reports |
| all free vias 0.4/0.2 mm, tented both sides | `route_via_style("Kame_FMU")` matches |
| `ref_compare("Kame_FMU")` matches | `ref_compare` |
| DFM `jlcpcb_multilayer` passes | `analyze_dfm` |
| report: copper autorouted vs copied, per engine | the plan, the converge rounds, `design_session_report` |

## 3. New layout: WiFi_Card schematic to a new board

Skill: `autodesign`, from the schematic-to-board stage on. Source: BoardKit's
`projects\WiFi_Card` (or a copy of the Mini PC WiFi example's schematic).

1. `design_session_start(project=<WiFi_Card .PrjPcb>)`, `proj_sync_pcb`, board shape for the
   mini PCIe form factor (`place_plan(keep_outline=false, levers={"shape": "minipcie-full"})`
   sizes it; or keep the outline the schematic's project already has).
2. `place_plan` asks for the SPWF01SA (U1) antenna keep-out: answer from its
   datasheet; draw the keep-out as a keep-out region before routing.
3. `route_broker_plan` and its steps; `route_converge` to done.

| Pass when | Measured by |
|---|---|
| copper DRC clean | `analyze_drc_report` (copper_violations 0) |
| DFM pass (the fab chosen with the user) | `analyze_dfm` |
| report which engines the broker used and why | the plan's steps (`engine`, `why`) and converge rounds |

## 4. From scratch: USB HID macro-recorder stick

Skill: `autodesign`. Brief: BoardKit's `briefs\macro_stick.json` (RP2040, USB
plug to the PC and receptacle for the keyboard through PIO-USB, W25Q16 QSPI
flash, ESD on both pairs, 3.3 V LDO behind a PPTC, Record/Play/Next and BOOTSEL
buttons, red/green status LED, SWD pads, 4 layers, JLC). The spec's wording
says USB-A plug and receptacle; the brief says USB-C; the user decides.

| Pass when | Measured by |
|---|---|
| datasheet-cited BOM | `design_generate_bom` and the session's citations |
| ERC 0 | `proj_run_erc` / `analyze_erc` |
| DRC 0, unrouted 0 | `pcb_run_drc`, `design_status` |
| both USB pairs 90 ohm, skew within 0.1 mm | `analyze_si_*` against the stack-up; `pcb_calc_length_match` |
| design report | `design_session_report` |

## 5. Docs

README (install, register, quick starts), KNOWN_ISSUES, PROVENANCE, and the
three skills under `skills/`. Then the `.claude.json` change is proposed, not
applied, and `docs/GAPS.md` lists what the old systems still do.
