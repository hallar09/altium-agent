---
name: board-setup
description: Set up an Altium board for layout with the AltiumAgent MCP server - the layer stack-up (names, thicknesses, core/prepreg, impedance), via types (through, blind, buried drill pairs), net classes, differential pairs and every design rule, including fixing or deleting the default rules a blank Altium board arrives with. Use before placement or routing, when asked to set up, check or change stack-up, rules, constraints, via types, impedance or clearances, or when DRC is flooded with violations from rules nobody chose.
---

# Set up the stack, vias and rules (AltiumAgent)

Placement and routing can only be as good as the board they start on. On
every board built so far the time was lost here, not in the routing: a pair
rule still at Altium's 15 mil, a 50 mil default via the router kept using,
a sliver rule burying the copper problems, a stack-up read back wrong, a via
type decided after the placement had made it impossible.

Three rules carry most of it:

1. **The defaults are rules too.** A blank board has rules scoped to All
   (Width 10 mil, RoutingVias 50/28 mil, DiffPairsRouting 10-15 mil,
   MinimumSolderMaskSliver 10 mil, SolderMaskExpansion 4 mil, Clearance 10
   mil ...). They are the fallback for every object your rules miss. Change
   their values to the design's, or delete them once a rule of yours scoped
   All replaces them. Never leave one you did not choose.
2. **Decide the via types before placement.** Through, blind (L1-L3,
   L(n-2)-Ln) or via-in-pad changes what fits under a BGA and on both sides
   of the board. Two BGAs back to back on through vias fight over the same
   via sites (the SODIMM board stalled on exactly this).
3. **Read everything back.** Every tool returns what Altium now holds
   (`descriptor`, `*_written`, `rejected`). `pcb_audit_rules` and
   `pcb_read_stackup` read the saved file: `app_save_all` first.

## Rules of the house

- Work on a COPY of the project, never a sample or installed design.
- Use the API, not the Rules dialog: every value below is settable, and
  driving the dialogs has crashed Altium. If `tool_guide` finds nothing, ask.
- Every number has a source (fab, datasheet, calculator, the board). An
  assumed one is marked and listed for the user.

## Steps

1. **Read the board.** `pcb_read_stackup`, `pcb_read_rules`,
   `pcb_get_design_rules`, `pcb_get_drill_pairs`, `pcb_get_differential_pairs`.
   Note which rules are Altium's (`pcb_audit_rules` lists them in
   `defaults_present`).

2. **Stack-up, in one call.** Choose the layer order (every signal layer next
   to a solid reference; pours on signal layers serve as planes), the
   dielectrics and the copper from the fab's stack table. Check the
   impedances with `analyze_si_impedance` (or start from
   `analyze_si_generate_stackup` for 2, 4 or 6 layers). Then:
   `pcb_import_layer_stack(pcb_path, layers=[...], dry_run=True)`, read the
   names and the total thickness, run it with `dry_run=False`, `app_save_all`,
   and `pcb_read_stackup` to confirm names, thicknesses and core/prepreg.
   Do this before placement: it replaces the inner layers.

3. **Via types.** Through vias only, unless the density says otherwise
   (count the via sites under each BGA against the nets that must leave it,
   on both sides). Blind or buried: `pcb_add_drill_pair(low_layer,
   high_layer)` for each span, save, `pcb_get_drill_pairs` (placing any via
   with a new span also makes its type). In routing, blind and buried vias
   are drawn with `route_session_draw(via_span="span")`. Tell the user:
   blind vias change the fab cost and the drill files.

4. **Classes and pairs.** `pcb_create_net_class` for each class in the rule
   table (see the routing skill's `references/rules.md`), `pcb_create_diff_pair`
   for each pair.

5. **Audit the defaults.** `app_save_all`, then `pcb_audit_rules(pcb_path,
   min_width_mm, min_clearance_mm, via_diameter_mm, via_hole_mm,
   mask_sliver_mm, diff_pair_width_mm, diff_pair_gap_mm)` with the brief's and
   the fab's numbers. Apply each finding's `fix` (or its `alternative`).
   Typical result: `pcb_set_rule_properties` on Width, RoutingVias,
   DiffPairsRouting and MinimumSolderMaskSliver; `pcb_delete_design_rule` on
   a default that a rule of yours replaces, or on one that never applies.

6. **Class rules, exact values.** `pcb_create_design_rule(...,
   values_mm={...})`, mm not mils (0.1 mm is not a whole mil):
   - width per class; per layer where microstrip and stripline differ:
     scope `InNetClass('DDR') And OnLayer('Top Layer')`, created BEFORE the
     class rule (a new rule lands last in priority, and priority cannot be
     set by script);
   - pairs: `rule_type="differential_pairs"` with min/pref/max WIDTH and gap
     and `max_uncoupled`; the pair router uses this width, not the Width
     rule's, and every Width rule over those nets must allow it;
   - vias: `rule_type="routing_via"` scoped to the class that needs a small
     via; HoleSize minimum at or below the smallest hole;
   - pours: `rule_type="polygon_connect"` (relief, or `style="direct"` for
     GND vias with `scope2="InNetClass('GND') And IsVia"`), `plane_connect`,
     `plane_clearance`;
   - copper to edge: a clearance rule scoped `IsBoardOutline Or
     IsBoardCutoutRegion`;
   - mask: `solder_mask_expansion`, `solder_mask_sliver` at the fab's dam.
   Read each reply's `descriptor`.

7. **What a script cannot make.** Matched-length, max-via-count and
   routing-topology rules: `tool_guide("matched length")`. Write the budgets
   into the routing plan and measure them (`analyze_si_pair_skew`,
   `pcb_read_net_statistics`); tell the user they are not Altium rules.

8. **Audit again** until `pcb_audit_rules` reports no failures, and check the
   numbers against the fab (`analyze_dfm_use_profile`). Show the user the rule
   table with sources, the stack-up with impedances, and the via types.

## During routing and at sign-off

- Noisy rules (silk, sliver, unrouted) may be switched off for a pass with
  `pcb_set_rules_enabled`; AD26 does not always keep a scripted enable, and
  the reply says so (delete and recreate the rule then).
- `pcb_run_drc(allow_modal=True, answer_dialogs=True, drc_pours="run_as_is",
  board_path=<PcbDoc>)`: the path makes the run check the board, not the last
  report. Altium stops at 500 violations; `analyze_drc_report` says when that
  hid copper problems.
- Before sign-off: `pcb_audit_rules(..., signoff=True)` fails on every
  sign-off rule still off. Tent vias with `route_set_via_tenting`.
