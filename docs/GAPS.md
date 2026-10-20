# What the old systems still do that Altium Agent does not

Compared command by command: altium-mcp's bridge (`AltiumMCPBridge.pas`,
`AltiumMCPAutoLayout.pas`) and its Python tools, Brass's bridge
(`BrassBridge.pas`) and CLI, BoardKit's scripts (`tools\altium\*.pas`,
`boardkit.py`) and eda-agent. Each line says what is missing and what to use
meanwhile. Kept up to date as gaps close.

## Missing operations (Altium side)

| Old system | What it does | Altium Agent today |
|---|---|---|
| Brass `pcb.stack`, altium-mcp `pcb.stack_import` | write the whole layer stack in one call from Altium's own parameter string (`Import_FromParameters`), planes given their nets | layer by layer: `pcb_add_layer`, `pcb_modify_layer`, `pcb_set_plane_net`; no one-call stack import |
| altium-mcp AutoLayout `SetSplitPlaneNets`, Brass the same | give AD26's split-plane objects on a fresh board their net and pour them (they stay "No Net" otherwise) | `pcb_set_plane_net` sets the layer's net; the split-plane fix-up is not ported (check after setting plane nets) |
| BoardKit (Situs lesson), spec "pours shelved and restored automatically" | shelve the pours before routing and restore them after | **no tool**; shelve by hand (Tools > Polygon Pours > Shelve) or let Freerouting do the bulk (it takes pours as planes) |
| Brass `pcb.fanout` | Situs fan-out of every SMD pad | the in-house engine's plane fan-out stage (`pcb_autoroute(plane_fanout=true)`); no Situs fan-out |
| Brass `pcb.import`, `pcb.remove_rooms` | Import Changes driven without the ECO dialog; remove the rooms it makes | `proj_sync_pcb` (the ECO dialog answered by the dialog driver); rooms deleted one by one (`pcb_delete_object`) |
| Brass `pcb.pour` | draw and pour the brief's pours (GND on both outer layers, plane pours) | `pcb_place_polygon_rect`, `pcb_start_polygon_placement` (shapes one at a time) |
| Brass `pcb.unroute` | remove pours and fan-out, then unroute all | `pcb_unroute` (routing only; pours stay) |
| altium-mcp `doc.revert`, Brass `docs.reload` | revert a document to its saved state / reload changed files | close and reopen (`app_close_document`, `proj_close`, `proj_open`); `design_undo_to_stage` for whole stages |
| altium-mcp `erc.types` | set ERC rule severities | read only (`proj_get_erc_violations`); severities in Project Options by hand |
| BoardKit `RefPrep.pas` / `boardkit prep` | prepare a reference copy: origin to the lower-left corner, routing removed, pours kept | `pcb_unroute`; no origin move (`pcb_read_origin` only) |
| BoardKit `MakeBlank.pas` / `boardkit templates` | blank SchDoc/PcbDoc made by Altium once, then new projects with no clicks | `proj_create`, `proj_add_sheet`, `proj_add_document` (created through the bridge) |
| altium-mcp AutoLayout `DoNetTies` | net-tie handling on the board | `sch_set_net_tie` on the schematic side only |

## Missing workflows (Python side)

| Old system | What it does | Altium Agent today |
|---|---|---|
| Brass agent loop (`levers.ts`) | the user describes the placement in words; an LLM turns it into placer levers | the client model sets `place_plan` levers itself (no separate LLM call inside the server) |
| Brass `brass check` | ERC + DRC + schematic/board parity in one command | `design_status` (ERC, unrouted, DRC report, DFM, style) plus `analyze_sch_pcb_sync` |
| BoardKit briefs (`briefs\*.json`) | a written brief format (board, fab, rails, interfaces, critical nets, connectors, notes) that drives the run | the session requirement text and `design_validate_requirement`; the brief format is not read |
| altium-mcp `analysis/whatif.py`, `hop.py`, `astar.py`, `negotiate.py` | what-if placement moves with congestion scoring, short "hop" fixes, a negotiated-congestion router | not ported (the broker uses the in-house engine, Freerouting and Situs) |
| altium-mcp `analysis/variant.py` | route a placement variant offline with Freerouting to compare placements before committing | `place_plan` + `route_export_dsn` + `route_freerouting` on a saved variant, by hand |
| altium-mcp `tools/incremental_place.py` | place a few parts at a time with checks | `place_plan` with `fixed` / `keep` levers |
| altium-mcp `tools/workflow.py`, `project_ops.py`, `exports.py` | release packaging, release evidence and manifest, fab exports | deliberately not published: no fab outputs without the user's approval (`proj_run_outjob` exists for an approved run) |
| altium-mcp `engines/` placement, component_search, release_evidence, test_plan | engine helpers behind those workflows | not ported |
| eda-agent backends | KiCad and EasyEDA backends | Altium only (the fork kept Altium) |

## Done in a different way

- BoardKit `refcopy`/`rip` loop: `route_refcopy` and `route_converge` (with a
  reference plan) do it from the saved files and the bulk track/via commands;
  arcs are copied as short segments.
- BoardKit `ViaStyle.pas`: `pcb_apply_via_template`, `pcb_normalize_vias`,
  `route_set_via_tenting` with read-back from the file.
- BoardKit `RuleTool.pas`, `DeleteRule.pas`: `pcb_create_design_rule`,
  `pcb_set_rule_properties`, `pcb_delete_design_rule`, `pcb_set_rules_enabled`
  (verified, and delete/recreate where AD26 ignores Enabled).
- BoardKit `LockCopper.pas`: `pcb_lock_net_routing`. `EndCommand.pas`:
  `app_exit_active_command`, `app_recover`.
- Brass/altium-mcp per-call `X2.EXE -RScriptingSystem:RunScript`: replaced by the
  one persistent bridge (never a second X2.EXE).
