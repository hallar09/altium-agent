---
name: routing
description: Route an Altium board yourself with the AltiumAgent MCP server - choose the track, via and clearance rules for this design from its nets, currents, impedances, fab and density; put power and ground in pours and planes; then route net by net in a routing session, checking and looking after each step, and import the result to Altium. Use when asked to route, re-route, finish routing or set up the routing rules of a PCB, even if the user only says "route it".
---

# Route a board (AltiumAgent)

You route the board. Every decision is yours: the rules, the order, which
nets go first, their width and vias, the path of the ones that matter. The
routing session carries out each action on the saved board, checks it at
once (what is still open, what breaks a clearance) and draws it. Its
in-house engine lays a net when you ask it to; you decide which, and you
judge the result.

Three rules carry most of the result:

1. **Every rule is chosen for this design, with a reason.** Widths, vias and
   clearances come from what each net carries, the stack-up, the fab and the
   density. A template default is not a choice.
2. **Power and ground go in pours and planes, not tracks.** Their pads get a
   short via to the plane first; the signals route round them. Routed as
   tracks among the signals, power and ground take the room the signals need
   (Altium's WiFi example: 66% of the signal connections that way, all of them this way).
3. **A few nets at a time, then look.** Check and view after each step. A
   bad result is ripped up and routed again, in another order or another
   width, while it is still small.

## Working within a usage limit

Read `references/agents.md` before splitting work. In short: agents save
your context, never the total; split only independent offline work, and
for a big board use packets (`route_session_packets`, one planner per
packet, `route_session_merge`). You are the only writer to Altium. Replies
are summaries (`route_session_check` with `detail="full"` only when you need
the list); look at a render at milestones, not after every call. Before
starting more than two agents, tell the user how many and what for.

## Rules of the house

- Work on a COPY of the project, never a sample or installed design.
- Hand routing already on the board is the user's: never rip it up.
- Never call the result manufacturing-ready; no fab outputs without approval.

## Steps

1. **Read the board.** `pcb_read_summary`, `pcb_read_stackup`, `pcb_read_rules`,
   `pcb_read_net_statistics`, `pcb_get_differential_pairs`, `pcb_get_drill_pairs`.
   The stack-up, the via types and the rules come first (the board-setup
   skill): routing on Altium's default rules is routing to rules nobody chose.

2. **Choose the rules.** Follow `references/rules.md`: classify every net,
   take each number from the fab, a calculator or a datasheet, and write the
   table before routing anything. Show it, with what rests on an assumption.

   | Class | Nets | Width (mm) | Clearance (mm) | Via (dia/hole mm) | Why |
   |---|---|---|---|---|---|

   In Altium, put it on the board (`pcb_create_net_class`,
   `pcb_create_design_rule` with `values_mm`, `pcb_create_diff_pair`; read
   back with `pcb_read_rules`), and save. Then `pcb_audit_rules` with the
   table's numbers: Altium's blank-board rules (Width, RoutingVias,
   DiffPairsRouting, MinimumSolderMaskSliver ...) are scoped to All and stay
   the fallback for every net the table misses. Apply each finding's fix
   (`pcb_set_rule_properties`, `pcb_delete_design_rule`) until it passes.
   A pair's width lives in its pair rule (`dp_*_width_mm`), not in the Width
   rule. You pass the same widths and vias to each route action too.

3. **Power and ground: pours and planes.** Every ground and every supply that
   feeds more than a couple of pins gets copper area: an inner plane
   (`pcb_set_plane_net`), or a pour (`pcb_place_polygon_rect`; GND on both
   outer layers of a 2-layer board). Save. A supply too local for a pour is
   routed as a track, at its current's width.

4. **Open the session.** `route_session_start(project=<copy>)`: the rules on
   the board, the layers, the pour and plane nets, every net by tier, what is
   open. `route_session_view` to see it.

5. **Power and ground first.** `route_session_fanout`: a via beside each power
   and ground pad, on its plane (pads on a layer their net is poured on are
   left to the pour). Pass your via and stub width. Note the `unresolved` pads.

6. **The nets that matter, one at a time.** Differential pairs, RF feeds,
   crystal and clock lines, sense and feedback lines, switch nodes, short
   power runs. Either draw the path yourself (`route_session_draw`: points
   with their layer, the via's point twice where the layer changes) or ask
   the engine for that one net with its class's width (`route_session_route`).
   A pair is one call: `route_session_draw_pair` takes the pair's
   centreline and lays both nets at the gap, vias side by side; never draw
   P and N separately point by point. Check what it did; view; rip up
   (`route_session_ripup`) and redo it if it is not what a careful designer
   would draw. On a board with blind or buried
   via types, draw the escapes that need them with `via_span="span"`: the via
   then blocks only the layers it passes, which is what frees the other
   side's via sites. The engine (`route_session_route`) places through vias.

7. **The rest, a few nets at a time.** `route_session_route(nets=[...])`, 5 to 10
   nets per call, local and short ones first, then by block, then the long
   ones. Their class's width and via each time. After every call, read what is
   still open and any violation; `route_session_view` every few calls and READ
   the PNG. When nets stay open: rip up the last group and route it in another
   order, route the blocking net first, or give it room (another layer, a
   narrower width where the class allows). Supplies with no plane go here, at
   their current's width.

8. **Finish.** The pads the fan-out could not reach: route their nets.
   `route_session_check` until nothing is open and nothing violates (beyond
   what the board had). Then stitch: `route_session_fanout(nets=<ground nets>,
   stitch_spacing_mm=5)`, closer by RF and fast edges; stitching last keeps its
   grid out of the routing's way.

9. **Land it.** `route_session_export`, then in Altium on the copy:
   `route_import_ses(job_id, expect_file)`, `pcb_repour_polygons`,
   `pcb_run_drc(..., board_path=<PcbDoc>)` (without the path a second DRC
   can silently re-read the last report). Blind and buried vias come
   through the import with their layers when they were drawn with
   `route_session_draw(via_span="span")`; make each via type first with
   `pcb_add_drill_pair` (the draw reply names any that is missing).
   Fix what Altium's DRC finds (it is the final word), in a new session if
   needed, and save.

10. **Report.** The rule table and its reasons; what you routed by hand and
    why; open connections (target 0); DRC; the fan-out and stitching vias;
    anything the user should confirm (a current, the fab).

## When it does not route

- **Many nets left open:** check the widths first. Routed at the template's
  preferred width where the pin pitch needs the minimum, a board will not
  finish (WiFi: 66% at 0.3 mm, 86% at 0.25 mm before anything else changed).
  Narrow only the nets at the fine-pitch part, not the whole board.
- **A power net drawn as long tracks:** it has no pour or plane; give it one.
- **Two BGAs back to back on through vias** (memory on both sides): it
  routes. Altium's own DDR4 SODIMM has eight DRAMs back to back and 785
  through vias, no blind ones. A through via of a net both parts use (the
  command/address bus, every supply) serves both sides at once, so plan
  each pair's escape together and share those sites; only the nets one side
  uses alone (its DQ) need sites of their own. Blind vias make it easier,
  not possible.
- **Length budgets** are not Altium rules a script can make: measure them in
  the session after each group, never with a script of your own:
  `route_session_lengths(nets, from_ref="X1", to_ref="U1", groups=[{name,
  nets, target: "<strobe net>", tolerance_mm}], pairs=[[P, N]],
  layer_scale={"MidLayer2": 1.19})` gives each net pad to pad (vias as the
  stack depth they cross), each lane member against its strobe, and the
  pair skew.
- **Seeing one layer or one corner:** `route_session_view(layers=["MidLayer2"])`
  or `region_mm=[x1, y1, x2, y2]`. A whole-board picture of eight layers
  hides exactly the escape you need to see.
- **Stuck once the rules are right:** the placement is the problem. Say where
  it is congested and move parts (the placement skill) rather than lower the
  rules below the fab's.

`route_broker_plan` gives the order a board is routed in (pairs, critical nets,
the power fan-out, the rest, stitching); read it as a checklist, then do the
work net by net in the session.
