# Provenance

Every piece of Altium Agent that came from somewhere else, its licence, and
what was changed. The sources were read, never edited.

## Sources

| Source | Where it was read | Version | Licence | Used as |
|---|---|---|---|---|
| eda-agent (George Saliba) | `%USERPROFILE%\OneDrive\Documents\eda-agent-main` | 0.7.1 | Apache-2.0, with NOTICE | the foundation: forked whole, then cut down |
| altium-mcp-pro (altium-mcp-pro contributors) | `%USERPROFILE%\Downloads\kicad-mcp-pro-main\altium-mcp-pro` | 0.2.0 | MIT; its `engines/` adapted from kicad-mcp-pro (Osman Aslan, MIT) | phase (b): offline file readers, analysis engines, DFM and their offline tools |
| Brass | `%USERPROFILE%\Downloads\brass` | (unversioned) | Apache-2.0, with NOTICE; parts derived from copperhead (Chouhan Industries, Apache-2.0) | phase (c): placement, reference checks. Nothing copied yet |
| BoardKit | `%USERPROFILE%\Documents\BoardKit` | (unversioned) | **no licence file** | lessons only (see below) |
| Freerouting | installed by the user | any | GPL-3.0 | an external process only, never vendored or linked |

## eda-agent (Apache-2.0)

Copied: `src/eda_agent` (as `src/altium_agent`), `tests`, `scripts/altium`
(the DelphiScript bridge, dispatcher and units), `scripts/gen_tool_reference.py`,
`scripts/update_reference.py`, `scripts/verify_distribution.py`,
`scripts/train/`, `skills/`, `docs/` (TOOL_NAMING, RELEASE_VERIFICATION,
PART_SOURCING, AUTONOMOUS_DESIGN, ui-automation, altium-delphiscript),
`LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`.

Every copied file keeps its SPDX line and copyright, and carries the change
notice `Modified for Altium Agent (2026): see docs/PROVENANCE.md` (Apache-2.0
section 4(b)). `NOTICE` keeps eda-agent's notice text and adds Altium Agent's.

Changed:

- **Renamed** everywhere: package `eda_agent` to `altium_agent`, CLI
  `eda-agent` to `altium-agent`, environment prefix `EDA_AGENT_` to
  `ALTIUM_AGENT_`, `Altium_API.PrjScr` to `AltiumAgent.PrjScr`, the bundle
  `Altium_MCP.pas` to `AltiumAgent.pas`, the workspace to
  `%USERPROFILE%\Altium Agent\workspace`, the status window to "Altium Agent
  MCP".
- **Removed**: the KiCad and EasyEDA backends (bridges, tools, extension,
  emitters, their tests and `docs/BACKENDS.md`), the `plan_` and `calc_`
  tool groups (their helpers stay as library code), `.github/`.
- **Moved**: `fileio` to `offline`, `web` to `dashboard`, `route` to
  `routing`; new `reference` and `analysis` packages.
- **Tools renamed** into the Altium Agent groups (docs/TOOL_NAMING.md).
- **Maturity** reworked: `live_only` split into `live_verified` (only from an
  Altium Agent record) and `live_unverified`. eda-agent's own live evidence is
  deliberately not carried over: it was recorded against eda-agent, not this
  fork.
- **Pascal**: 12 handlers' PreProcess/PostProcess wrapped in Try/Finally;
  `ReadBackRuleEnabled` added and used by `PCB_SetRulesEnabled` and
  `PCB_SetRuleProperties`.
- **Linter** extended with the hardening rules (`scripts/altium/lint_hardening.py`).
- **Doctor**: single-instance check, read-only canary, self-heal and
  Freerouting checks.

## Written for Altium Agent

`diag/self_heal.py`, `diag/verify.py`, `analysis/drc_report.py`,
`core/units_policy.py`, `core/cli_args.py`, `tools/agent.py`,
`scripts/altium/lint_hardening.py`, `core/LEGACY_UNIT_TOOLS.json`,
`tools/live_verified.json`, and their tests. They carry the Apache-2.0 SPDX
line and no third-party copyright.

## BoardKit (no licence file)

BoardKit is the user's own working kit and has no licence file, so **no
BoardKit code or text has been copied**. What Altium Agent took from it is
knowledge: its README "Known issues" list (docs/KNOWN_ISSUES.md says how each
is handled) and the idea of a DRC summary (`analysis/drc_report.py` was written
from Altium's report format, not from `drc_summary.py`). Before any BoardKit
code (briefs, fab profiles, scripts) is copied, the owner must say under which
licence it may be.

## altium-mcp-pro and kicad-mcp-pro (MIT)

Copied in phase (b), from altium-mcp-pro 0.2.0. Every file keeps an
`SPDX-License-Identifier: MIT` line with its copyright, names the file it came
from, and says it was modified for Altium Agent. The licence texts are in
THIRD_PARTY_NOTICES.md; the package licence is declared `Apache-2.0 AND MIT`.

| altium-mcp-pro | Altium Agent | Changes |
|---|---|---|
| `formats/` (records, ini, prjpcb, pcbdoc, schdoc, sch_netlist, libraries, outjob) | `offline/files/` | imports; the mm-per-mil factor now comes from `altium_agent.units` |
| `errors.py` | `offline/errors.py` | base class renamed `OfflineError` |
| `discovery.py` | `find_projects` in `offline/context.py` | only the folder walk kept; process and install lookups dropped (Altium Agent has its own) |
| `config.py`, `paths.py`, `session.py`, `modes.py` | replaced by `offline/context.py` (Altium Agent's) | reading allowed anywhere; writing only under the Altium Agent output folder, never beside a design or into Altium's folders; no operating modes |
| `analysis/` model, bom, consistency, copper, dfm, erc, overlap, review, si, board | `analysis/` | imports; the fab-profile data path; stack-up layer names matched without spaces or case; a footprint that is one of the part's alternate models is flagged `pcb_is_an_alternate_model` |
| `engines/` and `engines/data/*.json` (from kicad-mcp-pro) | `analysis/engines/` | imports, environment variable names, unit factor |
| `tools/` analysis, inspection, quality, engineering, manufacturing, common, project, schematic, pcb, library, registry | `tools/offline/` | renamed into the Altium Agent groups by `publish.py` (Altium Agent's); offline gates carry a scope note that PASS is not manufacturing-ready; `validate_design` lost its live branch; OutJob runs and release packaging are not published |
| `tests/` formats, analysis, netlist, examples, parity (read-only cases) | `tests/offline_files/` | imports; example paths from `ALTIUM_AGENT_EXAMPLES` / `ALTIUM_AGENT_KAME` |

Not copied (later phases or not wanted): `analysis/` astar, hop, negotiate,
whatif, render, circuit, variant (placement and routing, phases c and d);
`formats/specctra.py` (routing broker, phase d); `engines/` placement,
component_search, release_evidence, svc_test_plan; `tools/` workflow,
project_ops, autolayout, routing, incremental*, live*, exports; the
DelphiScript bridge (Altium Agent keeps one transport).

## Brass and copperhead (Apache-2.0)

When phase (c) copies code, each file keeps its header (including "adapted
from copperhead"), and Brass's NOTICE text is added to ours.

## Freerouting (GPL-3.0)

Never copied, bundled, imported or linked. A jar the user installed is run as
a separate process with a DSN file in and an SES file out
(`ALTIUM_AGENT_FREEROUTING_JAR`, `ALTIUM_AGENT_JAVA`), which keeps the GPL out
of this code base.
