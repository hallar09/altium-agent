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
| **open**: DRC with shelved or modified pours opens "Repour Polygons and Run DRC / Run DRC" and the DRC call times out. | `pcb_run_drc` refuses without `allow_modal=True`. Answering this prompt from the dialog driver is phase (b) work; until then answer it in Altium. The report still lands on disk and `analyze_drc_report` reads it. |

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

## Routing (from BoardKit; phase (d))

These shape the routing broker and are **open** until it exists:
Freerouting ignores intent (crystals, antenna keep-outs) and must be checked;
autoroute before pouring (pours become obstacles); altium-mcp's Freerouting
tool times out on big boards, so the broker will run Freerouting as a background
process and import the SES; Freerouting's `java` is not on PATH; fan-out vias
ignore parts on the other side and planned pair corridors; Situs gives up after
its first pass has boxed pins in; diff pairs must be routed as pairs before
autorouting; and a re-layout must not silently remove hand routing.

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
