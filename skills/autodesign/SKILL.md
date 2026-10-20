---
name: autodesign
description: Design a new PCB in Altium Designer from a requirement with the AltiumAgent MCP server (schematic, placement, routing, review). Use when asked to design a board from scratch or run a spec-to-board design.
---

# Design a board from a requirement (AltiumAgent)

The server owns the sequence, the gates and the durable state; you do each
stage, look at the result, and record it. A run survives context loss: the
session journal, not the chat, is the truth.

## Rules that are never bent

- Work only in a project folder of the user's (never under
  `%PUBLIC%\Documents\Altium` or `Program Files`; the tools refuse those).
- Never start Altium. If it does not answer: `app_diagnose`, then
  `app_recover` for what it offers, else tell the user exactly what to click.
- Never call the design manufacturing-ready. No Gerbers, drill, pick-and-place
  or fab packages without the user's explicit approval.
- Datasheet first: every device fact (pinout, decoupling, layout rules,
  absolute maximums) cited from the manufacturer's datasheet
  (`lib_fetch_datasheet`, `design_datasheet_checklist`). Never invent one.
- Freerouting (GPL) runs only as the user's own install, only if the project
  allows third-party engines (`route_engines` says).
- A tool badged `live_unverified` may be wrong: confirm what it claims (read
  back, render, measure) before building on it, and tell the user when it
  misbehaves.

## Start

1. `app_ping` (Altium really answers; a status read does not prove it), then
   `app_diagnose` (one instance, no dialog, the script running), `tool_guide`,
   `design_autonomy_guide` once.
2. Agree the requirement with the user in ONE message: what you will assume,
   and only the questions you cannot settle yourself.
3. `proj_create` in the user's folder, then
   `design_session_start(requirement, project=<.PrjPcb>, profile=<style, if any>)`.
   From now on every `design_session_log(event="stage_enter")` checkpoints the
   project; `design_undo_to_stage` goes back to any stage in one call.

## The loop

`design_next_action` -> do the stage with its `suggested_tools` -> check its
exit gate -> `design_session_log(event="stage_result", status="ok", data=<numbers>)`.
`blocked` -> ask the user, then `design_session_log(event="resolved")`.
A stage that fails 3 times escalates to a question for the user by itself:
surface it, do not loop past it.
Save in Altium before every gate, stage entry and status call: the reviews
read the saved files.

| Stage | Do | Gate |
|---|---|---|
| requirement, architecture | `design_validate_requirement`; parts with `lib_search_parts` / `lib_fetch_part` (in stock), datasheets fetched | user agrees the block diagram and the parts |
| plan, plan_verification | DesignPlan: `design_list_circuit_blocks`, `design_add_circuit_block`, `design_add_part`; `design_validate_plan`, `design_review_plan` | plan validates; every IC's decoupling and every interface from its datasheet |
| library_readiness | missing symbols and footprints: `lib_create_*`; footprints checked against the package drawing with `lib_check_package` and `lib_render_package` | no unverified footprint |
| schematic_emit | `design_execute_plan`; `proj_compile`, `proj_run_erc`, `design_audit_schematic`; `design_generate_bom` | ERC 0 errors; `design_gate_review(gate="schematic")` answered |
| sch_to_pcb | `proj_sync_pcb` (its ECO dialog is answered by the dialog driver); `analyze_sch_pcb_sync` | every net and part on the board |
| rules_stackup | `pcb_set_board_shape`; layers (`pcb_add_layer`, `pcb_modify_layer`, `analyze_si_generate_stackup` for impedance); rules (`pcb_create_design_rule`), diff pairs (`pcb_create_diff_pair`, `pcb_get_diff_pair_rules`) | rules match the fab profile (`analyze_dfm_use_profile`) |
| placement | `place_plan` (answer its antenna questions; `profile=` for a house style), `place_check` until clean, `place_apply(confirm=true)` | `design_gate_review(gate="placement")` answered, no error marker |
| routing | `route_broker_plan` and its steps in order (pairs, critical nets, bulk by Freerouting or in-house, `route_situs` to finish), `pcb_repour_polygons`, `pcb_run_drc`, `route_converge` until done | 0 unrouted, 0 copper DRC; pairs within the skew asked for; `design_gate_review(gate="routing")` answered |
| pours_tuning | ground pours, stitching (`pcb_place_stitching_vias`), `route_via_style` and `route_set_via_tenting` to the style | via style matches, tenting verified from the file |
| verification | `pcb_run_drc`, `analyze_drc_report`, `analyze_board_review`, `analyze_dfm`, `design_status` | `design_gate_review(gate="verification")` answered; open issues listed, none hidden |
| outputs | only with the user's explicit approval | `design_session_report` written |

## Looking

At every gate: `design_gate_review(gate)`, then Read every PNG it returns
and answer each rubric item from the picture, citing marker numbers. Numbers
decide pass or fail; the pictures catch what numbers miss. Never pass a gate
with an open error marker or an unexplained ratsnest line.

## Checkpoints

Every `stage_enter` takes a checkpoint of the project files (save first).
Also take one with `app_checkpoint` (it saves Altium's open documents first)
before each high-risk mutating stage (sch_to_pcb, routing, pours_tuning).

## When something goes wrong

- A stage made things worse: `design_undo_to_stage(stage, confirm=true)`.
- Altium stopped answering: `app_diagnose` -> `app_recover`.
- A tool misbehaves: note it (`design_session_log(event="note", topic="issue")`)
  and tell the user; try one reasonable workaround, not five.
- `design_status` any time: stage, ERC, unrouted, DRC, DFM, style, next action.
