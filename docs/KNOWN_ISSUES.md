# Known issues

What Altium Designer (AD26) does to scripts, and what Altium Agent does about
each. Most of these were learned the hard way while building boards with
BoardKit and altium-mcp; the "Handled by" column says where Altium Agent
enforces the fix, so a regression fails a test or the linter rather than a
board. Items marked **open** are not handled yet.

## Altium and the bridge

| Issue | Handled by |
|---|---|
| A second X2.EXE takes the bridge's workspace and its requests vanish; launching `X2.EXE -RScriptingSystem:RunScript` per call starts one. | Altium Agent never starts Altium. `doctor` and `app_diagnose` fail on zero (`ALTIUM_NOT_RUNNING`: start it yourself) or two (`MULTIPLE_ALTIUM`) instances; relaunching the bridge refuses unless exactly one Altium runs. `tests/test_never_a_second_altium.py`. |
| A script that died between `PCBServer.PreProcess` and `PostProcess` leaves "A command is currently active and save cannot be completed"; every later save and bridge call times out. | Every PreProcess/PostProcess pair is in Try/Finally (linter rule `preprocess-needs-try-finally`, an error; 12 handlers fixed). `app_diagnose` reports `COMMAND_ACTIVE`, and `app_recover` `end_active_command` OKs the box and unwinds the open levels. |
| A modal dialog (update prompt, save prompt) blocks every script. | `app_diagnose` reports `MODAL_DIALOG` with its title and buttons. Only a single-button information box is dismissed automatically; anything that asks a question is left to you. |
| A dialog opens on another monitor, or off every monitor. | `DIALOG_ON_OTHER_MONITOR` says where; `DIALOG_OFF_SCREEN` is fixed by `bring_dialog_on_screen`. |
| The Windows text-input overlay (TextInputHost) sits over a dialog and swallows automated clicks. | `app_diagnose` reports `TEXTINPUTHOST_OVERLAY` with the fix (click into the dialog once, or close the touch keyboard). |
| A script that fails to compile stays paused ("another instance is busy"). | `app_diagnose` reports `SCRIPT_HALTED` (bridge window present, not polling): Run > Stop, close the script tab, start the bridge again. |
| One bridge at a time: two tools driving Altium at once collide. | One transport: the persistent polling bridge with a fair queue between Claude sessions. Do not run altium-mcp or BoardKit live calls while Altium Agent is attached. |
| Import Changes (ECO) needs one click on Execute Changes, then Close. | Inherited dialog driver answers it when the caller allows modal tools; the tool is badged `modal`. |
| DRC with shelved or modified pours opens "Repour Polygons and Run DRC / Run DRC", and the Design Rule Checker itself is modal; the DRC call times out. | `pcb_run_drc(allow_modal=True, answer_dialogs=True)` lets the dialog driver press Run and Close on the checker while the bridge waits. The polygon prompt is answered only as told: `drc_pours="run_as_is"` presses the plain Run DRC, `drc_pours="repour"` repours first and also needs `allow_repour=True` because it changes copper; the default leaves it for a person and says so. **Not yet live-verified**: the dialog captions come from BoardKit's notes, and RELEASE_VERIFICATION A11 checks them. The report lands on disk either way and `analyze_drc_report` reads it. |

## DelphiScript

| Issue | Handled by |
|---|---|
| Record fields cannot be assigned (`R.Left := ...` fails with "Undeclared identifier: Left"). | Linter rule `record-field-assignment` (error) for record locals that were never assigned whole; copy into plain `TCoord` variables. |
| Changing objects while an iterator walks them skips or repeats objects. | Linter rule `mutate-while-iterating`: collect first, then modify. Five inherited handlers still do it and are in the lint baseline (below). |
| `Continue`/`Break` inside `Try` do not behave as in Delphi. | Linter rule `continue-break-in-try` (warning). |

## PCB rules and vias

| Issue | Handled by |
|---|---|
| Scripted `Rule.Enabled := False` does not stick in AD26. | `pcb_set_rules_enabled` and `pcb_set_rule_properties` read every rule back after PostProcess and report `verified` / `mismatched`; a mismatch makes the call fail (`ok: false`) and names the fix: delete the rule (`pcb_delete_design_rule`) while routing and recreate it (`pcb_create_design_rule`) after. Linter rule `rule-enabled-write` flags any other enable write without a read-back. |
| Altium's blank-board `MinimumSolderMaskSliver` (0.254 mm) ignores "disabled". | Same: delete and recreate at the fab's real dam width. |
| Via tenting by script is ignored while the via's `SolderMaskExpansionMode` follows the rule. | Linter rule `tenting-needs-manual-mask` (error): set `eMaskExpansionMode_Manual` before `IsTenting_Top/Bottom`. The read-back of tenting arrives with the phase (d) via tools. |
| Altium's batch DRC stops at 500 violations, and on a dense board silk and mask noise fills all 500 and hides copper problems. | `analyze_drc_report` flags a report at the limit as incomplete, lists copper problems in full with mm locations, counts the noise, and names the rules to delete while routing. |

## Arguments

| Issue | Handled by |
|---|---|
| A net named `-5V` or `-VBAT` given as `--nets -5V` is read as an option. | The CLI joins list options as `--nets=-5V` and splits names itself (`core/cli_args.py`, `tests/test_net_names_with_a_leading_dash.py`). |

## Renders, checkpoints and status (phase e)

- **Offline renders draw symbols as boxes** with their pins (the sheet files
  carry no symbol art the reader keeps), and leave poured polygons out so the
  tracks under them show. Live renders (`pcb_render_svg`, `design_visual_review`)
  draw what Altium sends.
- **Reviews, checkpoints and status read the saved files.** Unsaved edits in
  Altium are in none of them: save before a gate, a stage entry or a status call.
- **PNG needs a Chromium-family browser** (Edge on Windows, or
  `ALTIUM_AGENT_BROWSER`); without one the SVG is still written.
- **DRC report coordinates are absolute**; the gate review subtracts the board
  origin to mark them. A report older than the board is flagged `stale`.
- **`design_undo_to_stage` closes the project in Altium without saving**, then
  restores and reopens it. If Altium asks a save question it cannot answer,
  run `proj_close(save=false)` and call again with `altium_closed=true`. A
  safety checkpoint is taken first, so the undo can be undone. Live-unverified (A16).

## Placement (phase c)

- **Footprint boxes include silkscreen and courtyards.** On dense boards these
  overlap by design (Kame_FMU's body gaps are negative at p25), so a profile
  whose body gaps are habitually negative switches the placer to copper
  spacing (`body: "copper"`). Silkscreen clashes are then left to DRC.
- **The Python placer is slower than Brass's TypeScript.** Kame_FMU (102 parts)
  takes about 7 s at `effort` 0.1 and 17 s at 0.5. **Open**: at those efforts
  one or two of Kame's large ICs stay unplaced inside the original outline;
  `place_check` reports them and `place_apply` refuses the plan.
- **Decoupling distance is a pull, not a guarantee.** `place_check` flags caps
  beyond the profile's p90 (or 3 mm); raise `decap_weight` and re-plan.
- **`place_apply` is `live_unverified`** until step A13 records it.
  `pcb.import_placement` sets absolute mils; the tool refuses a focused board
  that is not the plan's, or whose origin moved since planning.

## Routing (from BoardKit; phase (d))

| Issue | Handled by |
|---|---|
| Freerouting ignores intent: crystals, clocks, pairs. | The broker routes pairs, clock and crystal nets and short local nets with the in-house engine before Freerouting sees the bulk, as fixed copper. |
| Antenna keep-outs. | DSN export carries the board's keep-out regions and Keep-Out layer. **Open**: a `place_plan` antenna keep-out is honoured only once drawn on the board as a keep-out region. |
| Autoroute before pouring (pours become obstacles). | Pours are exported as planes of their net, poured nets are left to the pours, and every route step is followed by `pcb_repour_polygons`. |
| Freerouting times out on big boards. | `route_freerouting` runs it as a job (`route_freerouting_status`), with its own timeout, and `route_import_ses` applies the result. |
| Freerouting's `java` is not on PATH; 2.x needs Java 25. | `routing/freerouting.py` uses `ALTIUM_AGENT_JAVA`, a `jre` beside the jar, then PATH, and reads the Java a jar needs from its main class; `route_engines` reports a mismatch instead of a class-version crash. |
| Freerouting 2.5's native CLI crashes (`UnsupportedFeatureError` for `ObstacleArea`) on boards with keep-outs. | The jar is preferred when both are installed. |
| Freerouting sends usage analytics by default. | Every run passes `--usage_and_diagnostic_data.disable_analytics=true`. |
| Fan-out vias ignore parts on the other side and pair corridors. | Batches run with Freerouting's fan-out off and pairs already routed. **Open** for a whole-board run. |
| Situs gives up after its first pass has boxed pins in. | Situs is the broker's finisher, not its first pass: `route_situs` (every part selected, Connections On Selected Components: no dialog, the board's own strategy, locked copper untouched) after the bulk, polled until the copper stops changing. Live-unverified (A17). |
| A reference-placed re-layout stalls in the autorouters (Kame_FMU_v2: Situs 79%, Freerouting worse each pass). | `route_refcopy` copies the big part's escape and any clashing net whole from the reference, locked, when every part is where the reference has it; `route_converge` does it per round with a reference plan. Arcs are copied as short segments. Live-unverified (A18). |
| Pours left in place box the autorouter in (Situs 54% vs 74%+ shelved on Kame). | **Open**: no shelve/restore tool yet (docs/GAPS.md). Freerouting takes pours as planes and does not need it. |
| Diff pairs must be routed as pairs first. | First step of every plan (in-house engine, `diff_pairs`). |
| A re-layout must not silently remove hand routing. | Routed nets are locked first (`pcb_lock_net_routing`), exported as fixed wiring, never ripped by `route_converge` (only `include_locked=false` unroutes of the plan's own nets), and SES copper that matches existing copper is not written twice. |
| Via tenting. | `pcb.set_via_tenting` sets manual mask mode, then tenting, and reads it back; `route_set_via_tenting` also confirms it from the saved file (tent flags 0x20/0x40 of the via record). **Live-unverified** (A15): the mask-mode write is the kind of via write that once crashed AD26's scripting engine. |

Measured on Kame_FMU (offline, Freerouting 2.5.0 jar on Java 25): with three
nets' copper taken up and everything else fixed, Freerouting put back 10 of
11 connections in 106 s (one FMU_VBAT connection left). With all of Kame's
routing taken up (pours kept as planes), one Freerouting run on two cores left
16 of 424 items unconnected after 18 passes and 13 minutes: the bulk tier
works, and a dense six-layer board still needs the in-house tiers,
`route_converge` and a person for the last connections.

## Debt carried from eda-agent

- **Lint baseline**: `scripts/altium/lint_baseline.json` holds 56 findings
  downgraded to warnings: 51 schematic handlers whose
  `SchServer.ProcessControl.PreProcess`/`PostProcess` pair is not in
  Try/Finally, and 5 PCB handlers that modify while iterating
  (`PCB_AddTestpointsForNetClass`, `PCB_CopyDesignatorsToMechLayer`,
  `PCB_FilletCorners`, `PCB_MoveTracksToLayer`, `PCB_ReplicateLayout`). New
  findings are errors; the baseline may only shrink.
- **Units**: 95 inherited tools take mils or unsuffixed lengths
  (`core/LEGACY_UNIT_TOOLS.json`). New tools take millimetres.
- **Nothing is live-verified yet**: the release run in
  docs/RELEASE_VERIFICATION.md has not been done for Altium Agent, so every
  bridge tool is `simulator` or `live_unverified`.
- **Windows-only tests**: the tests that call Win32 UI APIs (menu clicks,
  dialog automation, control shapes, the foreground guard, parts of the
  workspace-pointer and local-provider tests) skip on other systems, so a
  Linux CI run does not cover them. Run the suite on Windows before a release.
