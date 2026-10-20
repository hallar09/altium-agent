# Altium Agent

An MCP server, a CLI (`altium-agent`) and Claude skills for designing boards in
Altium Designer, aiming to take a board from a requirement (or a reference
design) to a checked, unreleased Altium project without anyone clicking for the
AI, and to say honestly what is left.

Altium Agent forks the Altium half of
[eda-agent](docs/PROVENANCE.md) (Apache-2.0) as its foundation and will add
the offline readers, analysis suites and DFM of altium-mcp-pro, the placement,
reference mode and checks of Brass and BoardKit, a routing broker, house-style
profiles, verified writes and a self-healing bridge. Everything borrowed is
listed in [docs/PROVENANCE.md](docs/PROVENANCE.md).

> **Status: phases (a) to (c).** The bridge, dispatcher, simulator,
> DelphiScript linter (with the Altium Agent hardening rules), doctor, tool
> catalog and generated reference (a); the offline readers, analysis and DFM
> (b); and the Brass placer with house-style profiles (c) are in place, and
> the offline test suite passes. **Nothing has been verified against a live Altium by Altium Agent
> yet**: every bridge tool is badged `simulator` or `live_unverified`, never
> `live_verified`, until the release run in
> [docs/RELEASE_VERIFICATION.md](docs/RELEASE_VERIFICATION.md) records it.
> Phases (b) to (f) are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What is in it

- **One transport to Altium**: a persistent polling DelphiScript bridge
  (per-request ids, a queue shared fairly between Claude sessions, keep-alive,
  auto-shutdown, crash and fault detection). Nothing launches
  `X2.EXE -RScriptingSystem:RunScript` per call, and nothing starts a second
  Altium.
- **Around 580 tools, grouped by the document they act on**: `app_`, `proj_`,
  `sch_`, `pcb_`, `lib_`, `obj_`, `route_`, `place_`, `ref_`, `analyze_`,
  `design_`, `audit_`, plus the meta-tools `tool_catalog`, `tool_invoke` and
  `tool_guide`. See [docs/TOOL_NAMING.md](docs/TOOL_NAMING.md) and the
  generated [docs/TOOL_REFERENCE.md](docs/TOOL_REFERENCE.md).
- **Two honest badges per tool**. Maturity: `offline`, `simulator`,
  `live_verified`, `live_unverified`. Interaction: `readonly`, `silent`,
  `modal`, `partial`. `live_verified` comes only from a record written while a
  live Altium answered (`altium-agent verify record`).
- **Millimetres at the API**, with the unit in the parameter name (`x_mm`,
  `width_mm`). The inherited eda-agent tools still speak mils; they are listed
  in `src/altium_agent/core/LEGACY_UNIT_TOOLS.json`, a list a test only lets
  shrink.
- **Hardening from the field** (BoardKit's known issues), each enforced by a
  lint rule or a test: PreProcess/PostProcess always in Try/Finally, collect
  first then modify, no writes to never-assigned record locals, via tenting
  only after manual mask mode, rule enable/disable read back (it does not stick
  in AD26), net names starting with `-`, DRC reports cut off at 500, and never
  a second X2.EXE. See [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).
- **Design files without Altium**: PrjPcb, PcbDoc, SchDoc, SchLib/PcbLib and
  OutJob readers (from altium-mcp-pro) behind about 95 offline tools:
  `pcb_read_*`, `sch_read_*`, `proj_read_*` for what is in the files, and
  `analyze_*` for ERC, schematic/PCB sync, DFM against fab profiles, SI
  calculators and board-aware impedance, PDN, thermal, EMC heuristics and
  gates. On Altium's Kame_FMU sample the schematic and board agree net for
  net (158 of 158). Gates are pre-checks: a PASS is never a statement that a
  board is ready to manufacture.
- **Self-healing bridge**: `app_diagnose` names what is holding Altium (no
  Altium, two Altiums, bridge not started or halted, a modal dialog, "a command
  is currently active", a dialog off-screen or on another monitor, the
  TextInputHost overlay) and says exactly what to click; `app_recover` applies
  only the three safe recoveries.
- **Canonical circuit blocks**: `design_add_circuit_block` folds a whole block into a `DesignPlan` in one call, allocating refdes, wiring every pin to the right net and tagging power / ground and roles. Twelve of them: `decoupling`, `pullup`, `pulldown`, `series_resistor`, `voltage_divider`, `rc_lowpass`, `rc_highpass`, `led_indicator`, `crystal`, `pi_filter`, `mosfet_low_side`, `mosfet_high_side`. `design_list_circuit_blocks` returns each one's parameter contract.
- The eda-agent design harness, DesignPlan and circuit blocks, schematic
  placer, layout engine, library and part-sourcing tools, SPICE, audits,
  checkpoints, OutJobs, dashboard and live view, all carried over.

## Install (Windows)

Altium Designer (AD26 is the target) must be installed; Altium Agent never
starts it for you.

```powershell
cd $env:USERPROFILE\Documents\altium-agent
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\altium-agent install-scripts
```

`install-scripts` copies the DelphiScript project to
`%USERPROFILE%\Altium Agent\scripts\` and stamps each unit, so `doctor` can tell
a stale deployment from a current one.

Then attach the bridge once per Altium session: in Altium, **File > Run
Script... > browse to `AltiumAgent.PrjScr` > `Dispatcher.pas` > StartMCPServer
> Run**. A small "Altium Agent MCP" status window appears. Check everything
with:

```powershell
.venv\Scripts\altium-agent doctor
```

`doctor` checks the install, that exactly one Altium is running, that the
bridge answers and its scripts match this checkout, a read-only round trip,
the self-heal diagnosis, and (optionally) Freerouting and its Java.

## Register the MCP server with Claude

Altium Agent is a stdio MCP server; its command is
`.venv\Scripts\altium-agent.exe` in the checkout, with no arguments; the
configuration needs its full path. With Claude Code:

```powershell
claude mcp add --scope user AltiumAgent -- $env:USERPROFILE\Documents\altium-agent\.venv\Scripts\altium-agent.exe
```

or the equivalent entry under `mcpServers` in Claude's configuration:

```json
"AltiumAgent": {
  "type": "stdio",
  "command": "C:\\Users\\<you>\\Documents\\altium-agent\\.venv\\Scripts\\altium-agent.exe",
  "args": [],
  "env": {}
}
```

Clients that cannot load ~580 tool schemas can set `ALTIUM_AGENT_TOOLSET=minimal`
in `env`; only `tool_catalog` and `tool_invoke` are advertised and every other
tool stays reachable through them. `ALTIUM_AGENT_DISABLE_DASHBOARD=1` skips the
local web dashboard for strict stdio clients.

## Quick starts

These describe the intended flows. Steps marked *(phase x)* arrive with that
phase; until then the inherited eda-agent tools named here are what exists.

**A new design from a requirement.** Ask Claude to design the board with the
`autodesign` skill. It opens a durable session (`design_session_start`), loops
on `design_next_action`, builds a DesignPlan (circuit blocks, datasheet-cited
parts), lays out and executes the schematic (`design_execute_plan`), checks it
(`proj_run_erc`, `design_audit_schematic`), checkpoints before each risky stage
(`app_checkpoint`), and resumes from the journal after a lost context
(`design_session_resume`). Placement to house style *(phase c)* and the routing
broker *(phase d)* replace the inherited placer and router.

**A re-layout from a reference design.** Profile the reference
(`ref_profile`), copy the project to a folder of your own and open the copy,
plan a placement to the profile (`place_plan profile=...`; an RF module's
antenna keep-out is asked for, never guessed), check it (`place_check`),
apply it to the focused copy (`place_apply confirm=true`), then route with the
broker *(phase d)* and compare the result with the reference (`ref_compare`).
Samples and installed designs are never edited.

**A review of an existing board.** `analyze_board_review` from the design
files alone (ERC, schematic/PCB sync, DFM against a fab profile, decoupling,
placement), then `analyze_review_design` for netlist hygiene,
the `audit_` tools for pins, decoupling and nets, `pcb_layout_audit` for
geometry, and `analyze_drc_report` on Altium's DRC report, which separates
copper problems from silk/mask noise and flags a report cut off at 500.

## When Altium does not answer

Call `app_diagnose` (or run `altium-agent doctor`). It returns findings with the
exact words of what to do. `app_recover` with `confirm=True` applies only these
safe recoveries, and only when the diagnosis calls for them:
`dismiss_information_dialog`, `end_active_command` (closes a PCB/schematic
transaction left open by a handler that died) and `bring_dialog_on_screen`.
It never starts or kills Altium and never answers a question for you.

## Known limitations

The Altium-side issues and how each is handled are in
[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md). These are the ones you meet as
a user.

**This tool is experimental. Please read this section before using on a design you haven't backed up.**

> Bridge changes are checked by Free Pascal and a linter before they ship, which cannot prove Altium's own DelphiScript engine accepts them: the two differ on which identifiers exist, and an undeclared one faults at runtime rather than at compile time. [`docs/RELEASE_VERIFICATION.md`](docs/RELEASE_VERIFICATION.md) is the procedure for closing that gap on a release, starting with a self-test that runs inside Altium and needs no document.

### UI automation synthesises real keyboard and mouse input

Most of this project talks to Altium through the scripting bridge, which addresses a window handle directly and cannot affect anything else. The `app_*` UI automation tools are different, and are used where Altium offers no other route: `application.execute_menu` reports success while invoking nothing, `GetMenu` returns 0 on Altium's DevExpress bars, and whole dialogs (Update From Libraries, Preferences, the wizards) have no scripting API at all.

**Synthesised input is not addressed to a window.** `keybd_event` and `mouse_event` are delivered to whatever is active at the instant they fire, and a click lands on whatever is under the pointer. So these tools:

- **take focus.** Menus and clicks need Altium in front, so running them while you are typing will interrupt you
- **cannot be confirmed the way a property write can.** A keystroke has no read-back; anything that matters is verified afterwards with a bridge read
- could, without containment, deliver an event to another application if focus or the pointer moved

What contains that: a foreground check runs **immediately before every event**, including between a key press and its release, and refocuses Altium rather than failing; coordinates are refused unless the point is over a window belonging to Altium's own process; and no tool accepts a window handle from the caller, so an arbitrary window cannot be addressed. `tests/test_foreground_guard.py` enforces all three, checking the event guard per line of source rather than per function, because a function that checks once and then emits five events in a loop would pass a naive test while firing four unchecked events.

**To switch it off entirely:**

```
ALTIUM_AGENT_UI_AUTOMATION=0
```

Every synthesised event is then refused. Reading stays available on purpose, because dialog detection is how the rest of the system notices Altium is blocked on a modal.

One case is not solvable: Altium's menu bar carries entries that are commands rather than menus (Place a Comment, Share, Open Home page, Preferences), and nothing distinguishes them. Measured across all 17 bar items: identical MSAA state including `HASPOPUP`, identical `accDefaultAction`, identical UIA control type. Listing such an entry clicks it, and clicking it runs it.

Full detail in [`docs/ui-automation.md`](docs/ui-automation.md).

### Altium DelphiScript engine can crash

Some tool paths trigger DelphiScript compile or runtime errors ("Undeclared identifier…", "Could not convert variant of type (Dispatch) into type (OleStr)", etc.). When that happens, the script project halts mid-execution and the polling loop stops responding. You will see one of:

- An Altium error dialog stating the problem
- Your MCP client timing out waiting for a response

**Recovery:** in Altium Designer, open the script project tab and press the **red Stop** button in the Script IDE toolbar (equivalently **Run > Stop** from the menu, or **Ctrl+F3**; use **Ctrl+Pause/Break** if the script is stuck in an infinite loop). This stops the halted debugger. Then re-launch the polling loop via **File > Run Script... > StartMCPServer > Run**.

This is an ongoing reliability effort. Every identified crash is either fixed or guarded. If you hit a new one, the Altium error dialog tells you the exact identifier or line. Opening an issue with that text helps us harden the relevant path.

### UNC network paths need current scripts

A UNC path (`\\server\team\board.PrjPcb`) opens like any other with the current scripts. Scripts deployed before the fix drop one leading backslash, so the file is not found and the error names a path that looks almost right. Run `altium-agent install-scripts --force` to update them, or use a mapped drive letter (`Z:\team\board.PrjPcb`) in the meantime.

### Text above Latin-1 becomes question marks

Altium's DelphiScript strings are single-byte, so the bridge carries text as one byte per character. Any character above U+00FF is replaced with `?` on the way in, silently. Accented Latin, the micro sign, and the degree sign are all below that boundary and survive; the ohm sign and any CJK text do not, so `10Ω` arrives as `10?`.

This shows up most often on imported parts: LCSC descriptions are frequently Chinese, and `lib_easyeda_import` passes the description straight through. If you need those fields readable, set them to a transliteration before importing, or edit them in Altium afterwards.

### Altium tool buttons relying on internal scripting pause while the server is running

Altium itself uses DelphiScript internally for many built-in commands (some ribbon buttons, panel actions, menu items). **While the `altium-agent` polling loop is active, those built-in commands may become temporarily unresponsive** because Altium's scripting engine is single-threaded and currently owned by our polling loop.

**The polling loop owns the scripting engine for as long as it's running.** While it runs, Altium's own script-backed buttons sit waiting. The loop exits when either:

- The MCP client calls `app_detach` (or the dashboard **Detach** button is clicked); `app_detach` saves the edited documents (the button saves nothing), the loop exits within ~500 ms, and Altium becomes fully responsive, OR
- **10 minutes of total silence** from the MCP client (no commands AND no keep-alive pings) triggers the built-in auto-shutdown

In practice, while an MCP client is attached and sending keep-alive pings every 30 s, the loop will never time out on its own; you need to either have the AI call `app_detach` or close the MCP client session entirely. After the client disconnects, expect up to ~10 minutes for the loop to auto-exit unless you use **Detach** to release it immediately.

### ECO (sch → PCB update) opens a modal, and there is no silent API

The Altium Schematic API exposes no scripted ECO executor: `IECO` only records proposed changes, no `DM_Execute` is documented, and no factory hands a script an `IECO`. `PCB:UpdatePCBFromProject` turned out not to be a real process id, so the handler that called it no-opped while reporting success. `proj_sync_pcb` now invokes `WorkspaceManager:Compare`, which does the real work and blocks on the change-order dialog.

Two consequences worth knowing before you call it.

It **blocks the polling loop** until the dialog is answered, so it is not an unattended call on its own. It also **refuses while a schematic is focused**: Altium answers that case with "Cannot compare a source document against its owner project" and changes nothing. Focus the board with `app_set_active_document` first.

What it reports is narrower than it looks. `components_in_sync` counts component **presence** only, so footprint swaps, designator and parameter edits and net changes are all invisible to it, and `dialog_outcome_verified` is always false because the handler cannot see which button was pressed.

**To drive it end to end**, use the GUI tools rather than answering by hand:

```
app_run_ui_command("Design|Update PCB Document <board>.PcbDoc", allow_commit=True)
```

That clicks the menu and answers the dialogs in one call, which matters because a modal blocks the bridge and a second call would never arrive. It validates before executing, and without `allow_commit` it presents the change order instead of applying it.

## Rules Altium Agent keeps

- Only your design copies are edited, never the source projects or Altium's
  samples.
- Nothing is called manufacturing-ready; fabrication outputs only after your
  explicit approval.
- Freerouting (GPL) is never bundled: when a project's routing policy allows
  third-party engines, a Freerouting jar you installed runs as a separate
  process (`ALTIUM_AGENT_FREEROUTING_JAR`, `ALTIUM_AGENT_JAVA`).

## Tests

```powershell
.venv\Scripts\python -m pytest -n auto
```

The suite runs offline without Altium. Tests that drive Win32 UI APIs run on
Windows and skip elsewhere. `python scripts/altium/build.py` lints the
DelphiScript (it aborts on any error) and builds `AltiumAgent.pas`;
`python scripts/gen_tool_reference.py` regenerates the tool reference, which a
test keeps current.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): layout of the code and the phase plan
- [docs/TOOL_NAMING.md](docs/TOOL_NAMING.md): groups, verbs, units, badges
- [docs/TOOL_REFERENCE.md](docs/TOOL_REFERENCE.md): every tool (generated)
- [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md): what Altium does to scripts, and how each is handled
- [docs/PROVENANCE.md](docs/PROVENANCE.md): every borrowed piece, its licence and what changed
- [docs/RELEASE_VERIFICATION.md](docs/RELEASE_VERIFICATION.md): the live verification run
- [docs/PART_SOURCING.md](docs/PART_SOURCING.md): where parts come from (`altium` is the only backend)

Altium and Altium Designer are trademarks of Altium Limited. Altium Agent is
not affiliated with or endorsed by Altium Limited.
