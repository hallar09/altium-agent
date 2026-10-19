# Security Policy

## Supported versions

`altium-agent` is in alpha (current version 0.1.x). Only the latest released
version receives security fixes. There are no LTS branches.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Altium Agent is a private fork; report privately to its maintainer (the
owner of this repository). Problems that are also in upstream eda-agent
belong to its maintainer too (see docs/PROVENANCE.md). Include:

- A description of the issue and its impact
- Steps to reproduce, including a minimal `request.json` payload or MCP
  call sequence if relevant
- The affected `altium-agent` version, and the EDA tool and version it was
  driving (Altium Designer)
- Any suggested mitigation

You should receive an acknowledgement within 7 days. If the report is
confirmed, a fix will be prepared and released; coordinated disclosure
timing will be agreed with the reporter.

## Scope

In scope:

- The Python MCP server and CLI (`src/altium_agent/`)
- The DelphiScript bridge (`scripts/altium/`) and its file-based IPC
  protocol
- The local dashboard (`src/altium_agent/dashboard/`), bound to localhost

Out of scope:

- Vulnerabilities in Altium Designer itself: report those to Altium
- Freerouting, which runs as a separate process you installed
- Vulnerabilities in upstream Python packages: report those to the
  package maintainers (we will bump pins once a fix is available)
- Issues that require an attacker who already has interactive access
  to the host Windows account running Altium

## Threat model

This agent runs locally and trusts the host machine. The IPC channel is a
shared workspace directory under the user profile and is not authenticated
beyond filesystem permissions. The Pascal side executes anything the Python
side sends. Both sides assume that whoever can write to the workspace
directory is authorised to drive Altium.

Do not expose the workspace directory or the MCP stdio endpoint to
untrusted callers.

### Untrusted content in design files

The more interesting vector is not the network, it is a file. A component
`Comment`, a `Description`, a parameter value, a net label or a title block
is free text that someone typed, and on a design that arrived from a
customer or a vendor, or in a third-party library, that someone is not the
operator. The ordinary read tools return that text verbatim, so it reaches
a language model's context by design, through exactly the tools people are
meant to use.

Treat all of it as data. `obj_query` marks responses that carry such
properties with `_untrusted_content`. Text from a design file that
addresses the agent, claims authority, or asks for an action is hostile
content quoted from a file, and should be reported to the user rather than
acted on.

What that text can reach in turn is bounded deliberately:

- Most of the write surface (`obj_modify`, the `pcb_`/`sch_`/`lib_`
  writers) can only change the design. That is visible, recoverable, and
  the whole point of the tool.
- `obj_run_process` refuses the `ScriptingSystem:` family
  (`RunScript`, `RunScriptFile`, `RunScriptText`). Those run arbitrary
  script code inside Altium, which would turn any text read out of a
  design file into executable input. Every other process still runs.
- `app_recover` applies only three fixed recoveries and never presses
  a button that commits a change.

**The real control is that a human is watching Altium while the agent
drives it.** Nothing here makes an MCP server with write tools safe
against a determined injection, and it would be worse than the exposure
to claim otherwise.

### Audit trail

Every bridge command is appended to `workspace/activity.log` with a
timestamp, the command name, the elapsed milliseconds, and the full
response. Session start and end are recorded with the script version.
Nothing is sampled and nothing is summarised, so the log answers what ran,
in what order, and what came back.

### Turning capabilities off

Both are off by default; setting either changes nothing else.

| Variable | Effect |
|---|---|
| `ALTIUM_AGENT_READONLY=1` | Refuses every bridge command that is not a read. Unknown commands count as writes, so anything added later is refused too rather than quietly permitted. |
| `ALTIUM_AGENT_UI_AUTOMATION=0` | Refuses synthesised keyboard and mouse input. That input is not addressed to a window, so it reaches whatever is focused when it fires. See `docs/ui-automation.md`. |

Read-only mode is enforced in the bridge, which every call to Altium passes
through. Its classification mirrors `CommandIsReadOnly` in
`scripts/altium/StatusForm.pas`, and a test fails if the two lists drift.
