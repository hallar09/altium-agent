# Choosing the routing rules for a design

Every number in the rule table comes from one of four places, and the table
says which: the **fab** profile, a **calculator**, a **datasheet**, or the
**board** itself (its pitch, its stack-up). A number from none of them is a
guess, and is marked as one.

## 1. The fab: the floor under everything

`analyze_dfm_profiles` lists the built-in fab profiles; `design_load_fab_profile`
validates one. Ask the user which fab, or take it from the project's OutJob or
notes; if neither says, use a standard (not advanced) tier and say so.

From it: minimum track, minimum space, minimum drill and annular ring. The
smallest via is `drill_min + 2 x annular_ring`. Nothing in the table goes
below these, and the defaults sit above them (a margin is cheaper than a
respin): signal width and space at about 1.25 x the fab minimum.

`design_synthesize_rules(profile, net_class_map=..., options=...)` turns the
fab and the classes into `pcb_create_design_rule` calls (integer mils), with
widths from IPC-2221 for `class_current_a` and differential geometry for
`diff_pair_target_ohms`. Use it, then check every number it returns against
this file: it skips a rule it has no input for, and says so in `notes`.

## 2. Classify every net

Use the net names, the pins they reach (`pcb_read_pads`, `proj_get_component_info`
with `with_pin_nets`) and the datasheets (`lib_fetch_datasheet`), not the names
alone.

| Class | How to recognise it | Width from | Notes |
|---|---|---|---|
| Ground | GND, AGND, DGND, PGND, VSS | (a plane) | Pours and planes, fan-out vias only. |
| Power | regulator outputs and inputs, VBUS, VBAT, rails | current | Pour or plane when it feeds more than a few pins; otherwise a track sized for its current. |
| Power, high current | motor, LED string, charger, input before the regulator | current | Wide track or pour; vias in parallel (see 4). |
| Differential pair | USB D+/D-, Ethernet, LVDS, MIPI, CAN (loosely) | impedance | Pair object (`pcb_create_diff_pair`), coupled width and gap. |
| Controlled impedance | RF feed (50 ohm), high-speed single-ended | impedance | Reference plane directly below, no via in the run if avoidable. |
| Clock and crystal | XTAL, OSC, CLK | the signal default | Short and direct, guarded by ground; route early. |
| Analog, sensitive | ADC inputs, VREF, feedback nodes, sense lines | the signal default | Away from switching nodes; feedback tapped at the load. |
| Switching node | SW, LX of a switcher | current | As short and wide as the layout allows; no other net under it. |
| Signal | everything else | density | The default class. |

## 3. Track widths

- **Signals: from the density.** Find the finest pitch a signal must pass
  between (the smallest pin pitch of a part with routed pins, from
  `pcb_read_pads`). Two pads at pitch `p` with pad width `w` leave
  `p - w` for a track and two clearances: if the default width does not fit,
  the part's escape needs a neck-down rule scoped to it (width down to the
  fab minimum for its pins), not a thinner default for the board. Use the
  default width everywhere else: about 0.15 to 0.2 mm on a dense board,
  0.25 mm where there is room.
- **Power: from the current.** The current a net carries comes from the
  datasheet of what drives or draws it (a regulator's rated output, a load's
  maximum), never from the name. `pcb_calc_trace_width_for_current(current_amps,
  copper_oz, delta_t_c, layer)` or `analyze_si_current_capacity` (IPC-2221);
  10 C rise for outer layers is the usual target, inner layers need about
  twice the width. When the current is unknown, say so and assume the
  supply's rating.
- **Impedance: from the stack-up.** `pcb_read_stackup` gives the dielectric
  height and Dk to the reference plane; `analyze_si_solve_width(target_ohm,
  geometry, height_mm, er, spacing_mm)` gives the width (and, with
  `spacing_mm`, the differential gap). USB 90 ohm differential, Ethernet
  100 ohm differential, RF 50 ohm single-ended. If the stack-up cannot reach
  the target at a width the fab can make, say so rather than routing it
  wrong: `analyze_si_stackup_for_interfaces` proposes one that can.

## 4. Vias

- **The routing via**: the smallest the fab makes reliably, unless the board
  gives a reason to go bigger. For escape between pads at pitch `p`, the via
  pad must fit `p - clearance`; a 0.5 mm-pitch QFN takes a 0.4/0.2 via
  between rows, a 0.8 mm BGA a 0.45/0.2. On a board with room, 0.6/0.3 is
  cheaper and kinder to yield.
- **Power vias by current**: a 0.3 mm drill via with standard plating carries
  roughly 1 A for a 10 C rise; use several in parallel for more, and check
  hot spots with `pcb_calc_thermal_vias` (it sizes a via field for a power
  pad, in watts).
- **Fan-out vias** for power and ground pads are the routing via, placed by
  the power pass; a high-current pad gets several.

## 5. Clearances

The fab's minimum space with the same margin as widths, and more where the
voltage asks for it: mains and high-voltage nets need creepage and clearance
by IPC-2221 or the safety standard (`analyze_creepage`). Keep the board
outline clearance the board already has, unless the fab needs more.

## 6. The rules Altium already has

A blank board is not empty: Width, Clearance, RoutingVias, DiffPairsRouting,
SolderMaskExpansion, MinimumSolderMaskSliver, HoleSize, PlaneClearance,
PlaneConnect, PolygonConnect and the rest are there, scoped to All. Altium
uses the highest-priority rule whose scope matches, so these decide every
object your class rules miss. `pcb_audit_rules` compares them with the table
and returns each fix. In short:

- **Width**: its minimum at or below the board's minimum, its preferred the
  signal default (`pcb_set_rule_properties(name="Width", min_width_mm=...)`).
- **RoutingVias**: the routing via, not 50/28 mil.
- **DiffPairsRouting**: the pair width and gap (`dp_*_width_mm`,
  `dp_*_gap_mm`), or delete it once every pair has a rule of its own.
- **MinimumSolderMaskSliver**: the fab's dam (`gap_mm`), not 0.254 mm.
- **Two rules of a kind with the same scope**: the lower priority one never
  applies; delete it.

Priority cannot be set by script and a new rule lands last in its kind: to
make a rule outrank an existing one, delete the other and recreate it after.

## 7. Layer-specific geometry

Microstrip and stripline need different widths for the same impedance. A
rule per layer group, scoped `InNetClass('DDR') And OnLayer('Top Layer')`,
created before the class-wide rule, carries it; the same for pair rules
(`IsDifferentialPair And OnLayer('Mid-Layer 2')`).

## 8. Write it down

Before routing, the table in SKILL.md step 2, one row per class: nets (or a
pattern and a count), width, clearance, via, and the source of each number
("fab: JLCPCB standard, 0.127 mm min", "IPC-2221, 1.5 A, 1 oz, 10 C",
"Z0 90 ohm on 0.2 mm prepreg, Dk 4.3", "pitch 0.5 mm: neck-down on U3 pins").
A number with no source is marked "assumed" and listed for the user to
confirm.
