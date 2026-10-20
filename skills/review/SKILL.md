---
name: review
description: Review an Altium design (schematic and board) with the AltiumAgent MCP server, from its files and pictures, and write the findings. Use when asked to review, check or audit a board or schematic, before ordering or at any stage.
---

# Review an Altium design (AltiumAgent)

A review reads, measures and looks; it changes nothing. Most of it works
from the saved files with Altium closed.

## Rules

- Change nothing. No placement, routing, rule or library edits, unless the
  user asks for a fix afterwards.
- Never call the design manufacturing-ready. Say what was checked, what
  passed, what failed, and what still needs a person.
- Cite the datasheet for every device claim; say so when one could not be
  checked.

## Steps

1. **State**: `design_status(project=...)` gives ERC, unrouted, the newest
   DRC report (and whether it is older than the board), DFM and open issues.
   Choose the fab profile with the user (`analyze_dfm_profiles`).
2. **Schematic**: `analyze_erc`, `analyze_review_design`, `design_audit_schematic`
   (needs Altium) or the offline `analyze_board_review`; decoupling and pins by
   datasheet (`design_datasheet_checklist`); `design_gate_review(gate="schematic")`
   and read every sheet picture.
3. **Board**: `analyze_board_review` (ERC, sync, DFM, decoupling, placement);
   `analyze_sch_pcb_sync`; `pcb_read_ratsnest`; `analyze_drc_report` on Altium's
   report (copper problems apart from silk/mask noise, cut-off at 500 flagged);
   `design_gate_review(gate="placement")` and `(gate="routing")`, reading every
   picture against the rubric.
4. **Style** (when a reference is named): `ref_profile` of the reference, then
   `ref_compare(profile, project=...)`, and `route_via_style(profile=...)` for
   via sizes and tenting read from the file.
5. **Signal and power** where it matters: `analyze_si_*`, impedance of the
   pairs against the stack-up, skew of pairs, current capacity of supply widths.

## Report

Group by severity: must fix (would fail or mis-function), should fix,
consider. Each finding: what, where (designator, net, marker number on a
picture), why (rule, datasheet page, measurement), and the suggested fix.
End with what was not checked. Offer to write it to a document.
