# Placing the usual circuits

Read the part's datasheet layout section first (`lib_fetch_datasheet`): when
it gives a recommended layout, follow it, and say so.

## Decoupling

- One cap per supply pin, at the pin, the smallest value closest, on the
  same side as the IC. The loop pin, cap, ground via should be the smallest
  thing on the board.
- Bulk caps (10 uF and up) near where the supply enters the IC's area, not
  at each pin.
- A cap on the far side of the board is only as good as the two vias between
  them; keep it for when the top is full.

## Switching regulators (buck, boost)

- The hot loop first: input cap, switch, diode or low-side FET (or the IC's
  VIN and GND pins) as tight as they go, on one side.
- The inductor next to the switch node; the switch node is short and wide,
  with nothing sensitive under or beside it.
- Output caps by the inductor, feedback divider by the IC's FB pin, its
  top tapped at the load (route the sense line later), away from the switch node.

## Crystals and oscillators

- Right at the oscillator pins, load caps between crystal and IC pins,
  nothing else between them. No signals under the crystal; a ground guard
  around it if the datasheet asks.

## USB, Ethernet and other differential pairs

- Connector, ESD, (common-mode choke), IC in a straight line, the pair's
  pins facing each other, so the pair runs short with no layer change.
- ESD right at the connector, before anything else.

## RF modules and antennas

- The antenna end at the board edge, overhanging if the datasheet allows.
  Its keep-out is clear of copper and parts on every layer.
- Matching parts between the RF pin and the feed, in line, as close as they fit.

## Analog and sensors

- Away from switching supplies, clocks and high-current paths, on the far
  side of the board from them where possible.
- Reference and filter parts at the ADC or sensor pins.

## Connectors, buttons, LEDs

- Connectors where the enclosure and the brief put them, on the edge, mating
  side out. Buttons and LEDs where the user reaches and sees them.

## Power and heat

- The power path in one direction: input connector, protection, regulators,
  loads. Short and wide; no detours through the signal area.
- Hot parts (regulators, drivers, power FETs) apart from each other, near
  large copper, with room for thermal vias (`pcb_calc_thermal_vias`), and away
  from parts that drift with temperature (crystals, references, sensors).

## Routability

- Leave a channel between rows of pins and their neighbours: two or three
  track widths and clearances, more under a BGA or a dense QFN.
- Turn parts so their ratsnest does not cross; a crossing on the ratsnest
  is a via, or a detour, later.
- Look at the picture: a part that sits where its lines fan out in every
  direction is usually in the right place; one whose lines all leave on one
  side wants to move that way.
