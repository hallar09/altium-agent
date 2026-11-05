---
name: placement
description: Place the parts of an Altium board yourself with the AltiumAgent MCP server - floorplan by function, place every part and its satellites, check and look after each move, then export the plan to Altium. Use when asked to place, re-place, lay out or arrange the components of a PCB, even if the user only says "lay it out".
---

# Place a board (AltiumAgent)

You decide where every part goes. The session tools carry out each move on
the saved board, check it at once (overlap, the board edge, decoupling
distance, wirelength) and draw it. No placer runs on its own: `place_plan` is
there to give you a first draft to refine, never the answer.

## Rules of the house

- Work on a COPY of the project, never a sample or installed design.
- Parts the brief or the designer fixed (connectors on the edge, mounting
  holes, locked parts) stay where they are. They are locked, and the session
  refuses to move them without `force`.
- Never call the result manufacturing-ready.

## Steps

1. **Open the session.** `place_session_start(project=<copy>)`. Read the
   board outline, the parts (largest first, with their schematic sheet) and
   the ones still off the board. `place_session_view` to see the start.

2. **Floorplan before any part.** Write down, in a few lines, where each
   functional block goes and why. A block is a schematic sheet, or an IC
   and the parts around it. Decide from:
   - the fixed parts: each block sits by the connector it serves;
   - signal flow: input to processing to output, short and in one direction;
   - noise: switching supplies and clocks away from analog, RF and sensors;
   - heat: hot parts spread out, near copper and the edge, not under tall parts;
   - RF: the antenna at the edge, its keep-out clear on every layer.

3. **Place the big parts**, block by block. `place_session_part(<IC>)` shows
   what it connects to and where those parts are. Turn it so its pins face
   their partners, then `place_session_move`. Leave room around the pin
   fields for the satellites and for the tracks that leave them.

4. **Place each IC's satellites around it**, in one move per group.
   - Decoupling caps at their supply pins, the smallest value closest, on
     the same side. `place_session_part(<cap>)` names the pin it serves.
   - Crystals and their load caps next to the oscillator pins.
   - Series resistors next to the driving pin; pull-ups by the IC.
   - ESD and filters right at the connector.
   - See `references/placement.md` for switchers, USB, RF, analog and
     power paths.

5. **After every group:** read the move's findings; fix any overlap or
   off-board part at once. Then `place_session_view` and READ the PNG. Look
   for crossed ratsnest that a rotation would undo, a cap on the wrong side of
   its IC, satellites blocking the tracks out of a pin field, and parts packed
   so tight a track cannot pass. `place_session_undo` when a move made things worse.

6. **Tidy.** Align rows of passives, turn polarised parts the same way,
   keep the designators readable. The bottom side only when the top is full,
   and only for passives and flat parts.

7. **Check.** `place_session_check`: no errors, decoupling within its limit
   (default 3 mm), connectors at the edge, nothing off the board. Explain any
   warning you keep.

8. **Export.** `place_session_export`, then `place_check(plan)`. In Altium:
   `place_apply(plan, confirm=true)` on the copy, then save. Then route it
   (the routing skill).

9. **Report.** The floorplan and why, what you placed where, the check, and
   anything you were unsure about (an antenna keep-out, a thermal limit) for
   the user to confirm.
