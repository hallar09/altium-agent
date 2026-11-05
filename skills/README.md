# Skills

Claude skills that ship with Altium Agent. Each drives the AltiumAgent MCP
server for one kind of job, with the project's rules (copies only, no second
Altium, no fab outputs without approval, datasheet first) written in.

| Skill | For |
|---|---|
| [autodesign](autodesign/SKILL.md) | a new board from a requirement, stage by stage, with a gate review at each |
| [relayout](relayout/SKILL.md) | re-placing and re-routing a board to a reference's house style (profile, copy, place, route with refcopy, compare) |
| [review](review/SKILL.md) | reviewing a schematic and board from the files and the pictures, changing nothing |
| [board-setup](board-setup/SKILL.md) | the stack-up in one call, via types (blind/buried drill pairs), classes, pairs and every rule, with Altium's blank-board default rules audited and fixed or deleted before anything is placed |
| [routing](routing/SKILL.md) | routing a board yourself, net by net in a routing session: rules chosen per net class (current, impedance, fab, pitch), power and ground in pours and planes with fan-out vias, check and look after each step, import to Altium |
| [placement](placement/SKILL.md) | placing a board yourself in a placement session: floorplan by function, every part and its satellites placed, checked and looked at after each move, exported as a plan for Altium |

**Claude Code**: copy a skill folder into your user skills directory
(`%USERPROFILE%\.claude\skills\<name>\SKILL.md`) or a project's
`.claude\skills`. They do not replace the `altium-board` skill; keep or remove
that one yourself.

**Other clients**: `design_autonomy_guide` and `tool_guide` give the same
protocol without a skill file.
