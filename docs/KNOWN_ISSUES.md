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
| A blank board's rules scoped to All (Width 10 mil, RoutingVias 50/28 mil, DiffPairsRouting 10-15 mil, MinimumSolderMaskSliver 10 mil, SolderMaskExpansion 4 mil) stay the fallback for every net the class rules miss, and agents left them alone on every board (SODIMM, MiniPC, MacroStick). | `pcb_audit_rules` reads the saved rules against the design's limits and returns each default that fights them, pair rules whose width was never set, same-scope rules that never apply and sign-off rules left off, each with the exact fixing call. The board-setup skill runs it before placement and at sign-off. |
| A pair rule's width could not be set: `pcb_create_design_rule` wrote only gap and uncoupled length, `pcb_set_rule_properties` updated 0 properties on kind 51, so every pair routed at Altium's 15 mil. | `pcb_create_design_rule(rule_type="differential_pairs", values_mm={min_width, pref_width, max_width, ...})` writes the widths on every layer; `pcb_set_rule_properties(dp_*_width_mm, dp_*_gap_mm, dp_max_uncoupled_mm)` edits an existing pair rule and reads each value back (`diff_pair_written`). The offline reader now returns pair widths (`dp_*_width_mm`), via sizes and uncoupled length. **Live-unverified** (the properties are the ones PcbAutomation.pas wrote on AD26). |
| No scoped Routing Via, polygon-connect, plane-connect, plane-clearance, annular-ring or sliver rule could be created, and no rule had a second scope. | New `rule_type`s `routing_via`, `polygon_connect`, `plane_connect`, `plane_clearance`, `annular_ring`, `solder_mask_sliver`, and `scope2`, with the identifiers the Kame FMU automation logged OK on AD26. Each reply carries Altium's `descriptor`. **Live-unverified** through this bridge. |
| Matched-length, max-via-count and routing-topology rules cannot be created by script; rule priority cannot be written. | **Open** (no proven rule kind; Priority is a read-only method). `tool_guide` lists them as not possible with what to do instead: measure lengths and via counts, and delete/recreate to reorder. |
| `obj_query` on `eRuleObject` returns a blank Name, so a rule cannot be targeted through it. | `tool_guide` sends rule work to `pcb_get_design_rules` / `pcb_get_rule_properties` / `pcb_set_rule_properties`, which match by name. |
| Width rules took whole mils only, so 0.1 mm became 4 mil. | `values_mm` on create and `min_width_mm` / `max_width_mm` / `favored_width_mm` on `pcb_set_rule_properties`. |

## Stack-up

| Issue | Handled by |
|---|---|
| `pcb_read_stackup` read dielectric types one off (DIELTYPE 0 as core, 1 as prepreg, 2 as surface). Altium writes core 1, prepreg 2, surface 3 (Kame_FMU, saved by Altium). `pcb_modify_layer` was right; agents "corrected" it into wrong stacks (SODIMM_Build: prepregs saved as core, cores as none). | Fixed in `offline/files/pcbdoc.py` (`DIELTYPE_NAMES`). Boards set up with the workaround need their dielectric types set again. |
| `pcb_add_layer` puts every signal layer above every plane; layer names and fractional thicknesses did not stick; a stack took dozens of calls. | `pcb_import_layer_stack(pcb_path, layers, dry_run)` builds Altium's stack text from the saved board and imports it with `MasterLayerStack.Import_FromParameters` (the Kame FMU automation's call on AD26). Inner copper is signal copper; planes are pours. **Live-unverified** through this bridge. |
| `pcb_modify_layer` took whole mils, so 0.6 mil copper became 1 mil. | Fractional mils and `copper_thickness_mm` / `dielectric_height_mm`. |
| `analyze_si_generate_stackup` only does 2, 4 or 6 layers. | **Open**; build larger stacks by hand with `analyze_si_impedance` per layer, then `pcb_import_layer_stack`. |

## Vias and DRC

| Issue | Handled by |
|---|---|
| No tool made a via type (drill pair); blind vias failed the LayerPairs rule and the drill output. | `pcb_add_drill_pair` (places and deletes a parked via; Altium keeps the pair) and `pcb_get_drill_pairs` (from the saved file). Live-tested on SODIMM_Build. |
| The route session and SES import modelled through vias only: a via drawn L1->L3 was checked as copper on every layer and imported Top to Bottom. | `route_session_draw(via_span="span")` (or `via_low_layer`/`via_high_layer` on a point) keeps a blind or buried via's span; the session's connectivity and DRC count it only on the layers it passes; the SES gives it a padstack with copper on those layers only, and `route_import_ses` places it with its low and high layer. The reply names spans with no drill pair. The engine (`route_session_route`, fan-out) still places through vias. **Live-unverified** in Altium (the bulk via placement with per-via layers is the inherited path). |
| `pcb_run_drc` leaves the HTML report focused; the next run silently re-reads the old report. | `pcb_run_drc(board_path=...)` focuses the board first. |
| `pcb_set_plane_net` does not set the net of AD26's split-plane objects. | **Open** (see GAPS.md). Use signal layers with pours, which `pcb_import_layer_stack` produces. |

## Usage

| Issue | Handled by |
|---|---|
| Long runs used up the user's limit: `pcb_run_drc` returned 100 full violations per run, session replies 40-60 rows each, and nine cold-started planners re-read the whole board (SODIMM, about 3M tokens). | Summaries by default: `pcb_run_drc(detail="summary")` gives counts by rule and the first 10 copper problems; `route_session_check(detail="summary")` and route/draw replies carry the first few rows and the counts. Big boards split into independent packets (`route_session_packets` forks one session per band that accepts only its nets; `route_session_merge` brings them back). `skills/routing/references/agents.md` says when splitting pays and how to brief a planner cheaply. |

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
- **Both sides by default.** `bottom` defaults to `auto`: passives go
  underneath once the parts would cover over half of one side, every SMD
  part once over 85%. Kept on top only, the benchmark boards (whose people
  put 50-60% of the parts underneath) had to fit 0.73-1.0 of the board on
  one side, and Kame ended with 67 overlapping pairs. Pass `bottom: "none"`
  for one-sided assembly; the plan's `levers.bottom_used` and notes say
  what `auto` chose.
- **Decoupling distance is a pull plus a pass, not a guarantee.** After the
  anneal every decoupling cap moves to the nearest legal spot by its IC's
  supply pin, or trades places with a part of its size there. Caps are
  shared out across the ICs on their net and never aimed at a connector
  (a card edge's VDD fingers had pulled every SODIMM cap to the edge, and a
  tie had sent all of them to U1 of eight DRAMs). `place_check` still flags
  caps beyond the profile's p90 (or 3 mm); on a full board some stay far.
- **No routability estimate.** A RUDY congestion map and an escape-via
  count were tried and dropped: on the benchmark they scored our failing
  placements as less congested than the people's, and the person's SODIMM
  (back-to-back DRAMs, through vias only) the same as ours. The trial route
  (`altium_agent.bench`) is the measure.
- **`place_apply` is `live_unverified`** until step A13 records it.
  `pcb.import_placement` sets absolute mils; the tool refuses a focused board
  that is not the plan's, or whose origin moved since planning.

## Routing (from BoardKit; phase (d))

| Issue | Handled by |
|---|---|
| Antenna keep-outs. | Keep-out regions and the Keep-Out layer are obstacles to the fan-out and the routing session. **Open**: a `place_plan` antenna keep-out is honoured only once drawn on the board as a keep-out region. |
| Nothing made the pours, so a board without them had ground and every supply routed as long tracks (WiFi with its pours taken off: 410 mm of copper and 51 vias against the person's 215 mm and 33). | `pcb_plan_pours` plans them as the example boards have them (ground everywhere, a supply on the layer named for it, islands for the rest); `route_session_pours` routes with them; `pcb_apply_pours` / `pcb_place_polygon` make them in Altium (**live-unverified**: `PCB_PlacePolygon` reuses only calls the rectangle pour and the board outline already make). |
| The fan-out and the router placed through vias even on a board with drill pairs, so blind and buried vias came only from paths drawn by hand. | With via types (the board's drill pairs, or `route_session_via_types` before they exist) `route_session_fanout` gives each power pad the shortest pair that reaches its pour, and `route_session_route` shrinks each via to the shortest pair spanning what its net uses there, then tries the nets still open once more. **Open**: the grid router itself still searches with through vias; the shrink frees layers afterwards, it does not plan a route around a blind span. |
| Route before pouring (pours become obstacles). | Poured nets are left to their pours, each of their pads gets a via to its plane (`route_power_fanout`), and the routing is followed by `pcb_repour_polygons`. |
| Fan-out vias ignore parts on the other side and pair corridors. | `route_power_fanout` keeps the board's clearance to every other net's copper on every layer (a via goes through them all), and runs after the pairs and critical nets. |
| Agents measured lengths, drew pairs and rendered layers with scripts of their own (the SODIMM job: ~390 scripts). | `route_session_lengths` (pad to pad along the copper, vias as stack depth, `layer_scale`, groups against a target, pair skew), `route_session_draw_pair` (both nets from one centreline at a gap) and `route_session_view(layers=, region_mm=)`. **Open**: `draw_pair` joins its ends to the pads only where the centreline's ends put them (start and end it between the two pads); no length tuning (meanders) yet. |
| Situs gives up after its first pass has boxed pins in. | Situs is the broker's finisher, not its first pass: `route_situs` (every part selected, Connections On Selected Components: no dialog, the board's own strategy, locked copper untouched) after the bulk, polled until the copper stops changing. Live-unverified (A17). |
| A reference-placed re-layout stalls in the autorouters (Kame_FMU_v2: Situs 79%). | `route_refcopy` copies the big part's escape and any clashing net whole from the reference, locked, when every part is where the reference has it; `route_converge` does it per round with a reference plan. Arcs are copied as short segments. Live-unverified (A18). |
| Pours left in place box the autorouter in (Situs 54% vs 74%+ shelved on Kame). | **Open** for Situs: no shelve/restore tool yet (docs/GAPS.md). A routing session routes before pours are poured and does not need it. |
| Diff pairs must be routed as pairs first. | First step of every plan (in-house engine, `diff_pairs`). |
| A re-layout must not silently remove hand routing. | Routed nets are locked first (`pcb_lock_net_routing`), kept as fixed copper, never ripped by `route_converge` (only `include_locked=false` unroutes of the plan's own nets), and SES copper that matches existing copper is not written twice. |
| Via tenting. | `pcb.set_via_tenting` sets manual mask mode, then tenting, and reads it back; `route_set_via_tenting` also confirms it from the saved file (tent flags 0x20/0x40 of the via record). **Live-unverified** (A15): the mask-mode write is the kind of via write that once crashed AD26's scripting engine. |

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
