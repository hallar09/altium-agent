# Skills

Claude skills that ship with Altium Agent. Each drives the AltiumAgent MCP
server for one kind of job, with the project's rules (copies only, no second
Altium, no fab outputs without approval, datasheet first) written in.

| Skill | For |
|---|---|
| [autodesign](autodesign/SKILL.md) | a new board from a requirement, stage by stage, with a gate review at each |
| [relayout](relayout/SKILL.md) | re-placing and re-routing a board to a reference's house style (profile, copy, place, route with refcopy, compare) |
| [review](review/SKILL.md) | reviewing a schematic and board from the files and the pictures, changing nothing |

**Claude Code**: copy a skill folder into your user skills directory
(`%USERPROFILE%\.claude\skills\<name>\SKILL.md`) or a project's
`.claude\skills`. They do not replace the `altium-board` skill; keep or remove
that one yourself.

**Other clients**: `design_autonomy_guide` and `tool_guide` give the same
protocol without a skill file.
