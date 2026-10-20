---
name: relayout
description: Re-lay out an existing Altium board in a reference design's house style with the AltiumAgent MCP server (profile, copy, place, route, compare). Use when asked to re-layout, re-place or re-route a board, or to build one in the style of another.
---

# Re-layout a board to a house style (AltiumAgent)

The reference's habits (spacing, sides, edge clearance, decoupling distance,
widths, via sizes and tenting, layer count) become a profile; the new layout
is placed and routed to it and checked against it.

## Rules

- Never edit the reference or an Altium sample. Work on a COPY of the
  project in a folder of the user's, opened in Altium. `place_apply`,
  `route_import_ses`, `route_set_via_tenting` and `design_undo_to_stage`
  refuse samples.
- Never start Altium; `app_diagnose` if it does not answer.
- Never call the result manufacturing-ready; no fab outputs without approval.
- Hand routing on the board is the user's: the broker locks it and never rips
  it up. Say which nets were kept, which were re-routed, and by which engine.

## Steps

1. **Profile the reference** (files only): `ref_profile(project=<reference>)`.
   Read the profile back to the user in a few lines (layers, spacing, sides,
   decap distance, via 0.4/0.2 tented both sides, and so on).
2. **Copy** the project to the user's folder (the user, or file tools; never
   overwrite anything) and open the copy in Altium. Then
   `design_session_start(requirement, project=<copy>, profile=<profile name>)`.
3. **Baseline**: `design_status` and `ref_compare(profile, project=<copy>)`.
   For a re-layout of the same design the comparison should match;
   anything it flags is the starting point.
4. **Placement**: `design_session_log(event="stage_enter", stage="placement")`
   (checkpoint), `place_plan(project=<copy>, profile=<name>)`; answer antenna
   questions from the datasheet; `place_check` until it has no errors (inherited
   findings on locked parts are fine and are reported apart); `place_apply(confirm=true)`;
   save; `design_gate_review(gate="placement")` and answer its rubric.
5. **Routing**: enter `routing`, then
   `route_broker_plan(project=<copy>, profile=<name>, reference=<reference .PcbDoc>)`
   and follow its steps: lock, the big part's escape copied from the reference
   (`route_refcopy`, when the parts are where the reference has them), pairs,
   critical nets, bulk, `route_situs` to finish, repour, DRC, `route_converge`
   until done or escalated (with the reference placement it copies clashing
   nets from the reference). Pours box autorouters in: there is no shelve tool
   yet, so ask the user to shelve them for the routing pass (Tools > Polygon
   Pours > Shelve) or rely on Freerouting, which takes them as planes. Save;
   `design_gate_review(gate="routing")`.
6. **Vias**: `route_via_style(profile=<name>)`; fix sizes, then
   `route_set_via_tenting` (dry run first) and confirm `verified` from the file.
7. **Compare and report**: `ref_compare(profile)` (expect a match),
   `pcb_run_drc`, `analyze_dfm`, `design_status`, `design_gate_review(gate="verification")`.
   Report: unrouted (target 0), DRC compared with the reference's own DRC,
   via style, the comparison, which nets were copied, kept or re-routed, and
   by which engine.

If a step makes things worse: `design_undo_to_stage(<stage>, confirm=true)`.
