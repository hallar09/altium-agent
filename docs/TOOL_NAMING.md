# Tool naming

Every MCP tool is named `group_verb_object`, all lower case.

## Groups

The group is the first token and says what the tool acts on. A test
(`tests/test_tool_groups_and_badges.py`) rejects any tool outside these.

| Group | Acts on | Category in the catalog |
|---|---|---|
| `app_` | Altium itself, the bridge, checkpoints, diagnosis and recovery | application |
| `proj_` | the project: compile, ERC, sheets, variants, OutJobs | project |
| `sch_` | schematic documents | schematic |
| `pcb_` | PCB documents | pcb |
| `lib_` | libraries, footprints, symbols, parts, datasheets | library |
| `obj_` | the generic object layer: query, modify, select, view, highlight | generic |
| `route_` | routing: plans, the routing broker, repairs | routing |
| `place_` | placement to a profile or house style | placement |
| `ref_` | reference designs and house style (`ref_profile`, `ref_compare`) | reference |
| `analyze_` | read-only analysis: DRC report, SPICE, review | analysis |
| `design_` | the design harness: sessions, DesignPlan, blocks, execution | design |
| `audit_` | schematic and board audits | audit |
| `tool_` | the meta-tools `tool_catalog`, `tool_invoke`, `tool_guide` | meta |

## Verbs

Prefer the shared set `get` (one, known) · `list` (enumerate) · `create` ·
`add` · `set` · `update` · `delete` · `remove` · `place` · `move` · `copy` ·
`run` · `export` · `import` · `open` · `close` · `save` · `attach` · `find` ·
`check` · `calc` · `plan` · `sync` · `render`. Domain verbs that are already
clear (`flip`, `align`, `repour`, `tune`, `panelize`) are kept. The group
carries the subsystem, so the object drops redundant words
(`app_get_status`, not `get_altium_status`).

## Bulk variants

A tool that acts on many objects takes a list and has a plural object
(`pcb_place_tracks`, `pcb_set_rules_enabled`). A read with a single-item
sibling keeps the `_many` suffix (`proj_get_component_info_many`). Bulk
tools do the work in one bridge round trip, collect first and modify after
(see docs/KNOWN_ISSUES.md), and report per item.

## Units

Every length a tool takes or returns is in **millimetres**, and the unit is
in the parameter name: `x_mm`, `width_mm`, `clearance_mm`, `angle_deg`. A
bare `x` or `width` is not allowed in a new tool. `core/units_policy.py`
classifies each parameter, and `tests/test_units_policy.py` enforces it.

The tools inherited from eda-agent speak mils (`x_mils`) or bare numbers.
They are listed in `src/altium_agent/core/LEGACY_UNIT_TOOLS.json`. That
list may only shrink: the test fails if a tool is added to it, or if a
listed tool already conforms and was not removed. `place_`, `ref_` and
`analyze_` tools may never be on it. A legacy tool is converted by giving
it `_mm` parameters (keeping the mil ones for a deprecation period is
allowed only under a different name).

The IPC command strings sent to the bridge (`"pcb.move_component"`) are
internal and are not covered by this convention.

## Badges

Every tool carries two badges, served by `tool_catalog` and printed in
docs/TOOL_REFERENCE.md.

**Maturity**, assigned in `tools/metadata.py`, strongest evidence first:

- `offline`: pure Python, runs in the test suite with no Altium.
- `live_verified`: a record in `tools/live_verified.json`, written by
  `altium-agent verify record` while a live Altium answered.
- `simulator`: every bridge command it sends has a handler in the Altium
  simulator, so it can be exercised without Altium.
- `live_unverified`: needs a live Altium and has no Altium Agent record.
  Being listed is not evidence that it was ever run.

**Interaction**: `readonly`, `silent` (changes the design, no dialog),
`modal` (opens a blocking Altium dialog; the dialog driver or a person
answers it), `partial` (leaves the job for a follow-up tool).

## Altium Agent renames

| old (eda-agent) | new |
|---|---|
| sim_run | analyze_sim_run |
| sim_setup_analysis | analyze_sim_setup |
| sim_get_results | analyze_sim_results |
| sim_get_readiness | analyze_sim_readiness |
| sim_attach_model | sch_attach_spice_model |
| sim_attach_primitives | sch_attach_spice_primitives |
| datasheet_fetch | lib_fetch_datasheet |
| footprint_check_package | lib_check_package |
| footprint_render_package | lib_render_package |
| part_search | lib_search_parts |
| part_fetch | lib_fetch_part |
| review_design | analyze_review_design |

## Inherited rename map (from eda-agent)

These renames happened in eda-agent before the fork and are kept for
readers of older notes. The `sim -> sim_` table below is history: those
tools are now `analyze_` and `sch_` (see the Altium Agent renames above).

### application -> app_

| old | new |
|---|---|
| get_altium_status | app_get_status |
| attach_to_altium | app_attach |
| save_all | app_save_all |
| detach_from_altium | app_detach |
| ping_altium | app_ping |
| get_server_report | app_get_report |
| create_document | app_create_document |
| get_open_documents | app_list_documents |
| diag_workspace | app_diag_workspace |
| get_active_document | app_get_active_document |
| set_active_document | app_set_active_document |
| get_altium_version | app_get_version |
| get_preferences | app_get_preferences |
| execute_menu | app_run_menu |
| set_intent | app_set_intent |
| get_clipboard_text | app_get_clipboard |

### project -> proj_

| old | new |
|---|---|
| create_project | proj_create |
| open_project | proj_open |
| save_project | proj_save |
| close_project | proj_close |
| get_project_documents | proj_list_documents |
| add_document_to_project | proj_add_document |
| remove_document_from_project | proj_remove_document |
| get_project_parameters | proj_get_parameters |
| set_project_parameter | proj_set_parameter |
| project_push_params_to_sheets | proj_push_parameters |
| get_nets | proj_get_nets |
| export_netlist | proj_export_netlist |
| compile_project | proj_compile |
| load_project_sheets | proj_load_sheets |
| get_bom | proj_get_bom |
| get_component_info | proj_get_component_info |
| get_component_info_many | proj_get_component_info_many |
| export_pdf | proj_export_pdf |
| cross_probe | proj_cross_probe |
| get_design_stats | proj_get_stats |
| get_board_info | proj_get_board_info |
| annotate | proj_annotate |
| generate_output | proj_run_output |
| get_focused_project | proj_get_focused |
| export_step | proj_export_step |
| export_dxf | proj_export_dxf |
| export_image | proj_export_image |
| get_outjob_containers | proj_list_outjob_containers |
| run_outjob | proj_run_outjob |
| run_outjob_all | proj_run_outjob_all |
| generate_fab_package | proj_generate_fab_package |
| get_variants | proj_list_variants |
| get_active_variant | proj_get_active_variant |
| set_active_variant | proj_set_active_variant |
| create_variant | proj_create_variant |
| get_open_projects | proj_list_open |
| get_messages | proj_get_messages |
| find_component | proj_find_component |
| get_connectivity | proj_get_connectivity |
| get_connectivity_many | proj_get_connectivity_many |
| force_recompile | proj_force_recompile |
| get_compile_freshness | proj_get_compile_freshness |
| import_document | proj_import_document |
| get_project_path | proj_get_path |
| set_document_parameter | proj_set_document_parameter |
| compare_sch_pcb | proj_compare_sch_pcb |
| update_pcb | proj_sync_pcb |
| update_schematic | proj_sync_schematic |
| get_design_differences | proj_get_differences |
| lock_designator | proj_lock_designator |
| get_project_options | proj_get_options |

### generic -> obj_ (generic primitives, selection, view, fonts)

| old | new |
|---|---|
| query_objects | obj_query |
| modify_objects | obj_modify |
| create_object | obj_create |
| delete_objects | obj_delete |
| batch_modify | obj_batch_modify |
| batch_create | obj_batch_create |
| batch_delete | obj_batch_delete |
| copy_objects | obj_copy |
| get_object_count | obj_count |
| select_objects | obj_select |
| deselect_all | obj_deselect_all |
| zoom | obj_zoom |
| switch_view | obj_switch_view |
| refresh_document | obj_refresh_document |
| set_grid | obj_set_grid |
| highlight_net | obj_highlight_net |
| clear_highlights | obj_clear_highlights |
| crossref_net | obj_crossref_net |
| get_font_spec | obj_get_font_spec |
| get_font_id | obj_get_font_id |
| get_document_info | obj_get_document_info |
| generic_run_process | obj_run_process |

### generic -> proj_ (project-level: compile, ERC, sheets, components)

| old | new |
|---|---|
| run_erc | proj_run_erc |
| get_erc_violations | proj_get_erc_violations |
| add_sheet | proj_add_sheet |
| delete_sheet | proj_delete_sheet |
| get_unconnected_pins | proj_get_unconnected_pins |
| replace_component | proj_replace_component |

### generic -> sch_ (schematic edit / place / query)

| old | new |
|---|---|
| set_sch_components_parameters | sch_set_components_parameters |
| stub_unconnected_pins | sch_stub_pins |
| place_rectangle | sch_place_rectangle |
| place_line | sch_place_line |
| place_note | sch_place_note |
| place_sheet_symbol | sch_place_sheet_symbol |
| place_sheet_entry | sch_place_sheet_entry |
| place_bus_entry | sch_place_bus_entry |
| place_bus | sch_place_bus |
| place_net_label | sch_place_net_label |
| place_port | sch_place_port |
| place_power_port | sch_place_power_port |
| place_no_erc | sch_place_no_erc |
| place_junction | sch_place_junction |
| place_image | sch_place_image |
| place_wires | sch_place_wires |
| place_sch_components_from_library | sch_place_components |
| get_sheet_parameters | sch_get_sheet_parameters |

Already-compliant `sch_*` tools (kept): sch_set_sheet_size, sch_set_units,
sch_add_directive, sch_get_directives, sch_get_constraint_groups,
sch_place_harness_connector, sch_place_cross_sheet_connector,
sch_place_text_frame, sch_generate_toc, sch_set_net_tie,
sch_increment_designators, sch_toggle_pin_visibility, sch_set_component_part_id,
sch_set_component_unique_id, sch_replicate_component, sch_place_probe,
sch_add_datafile_link.

### sim -> sim_

| old | new |
|---|---|
| sch_get_simulation_readiness | analyze_sim_readiness |
| sch_attach_spice_model | sch_attach_spice_model |
| sch_attach_spice_primitives | sch_attach_spice_primitives |
| analyze_sim_run | analyze_sim_run (unchanged) |

### render and review

| old | new |
|---|---|
| sch_render_svg | sch_render_svg (unchanged) |
| pcb_render_svg | pcb_render_svg (unchanged) |
| design_visual_review | design_visual_review (unchanged) |
| export_bom_html | proj_export_bom_html |
| design_review_snapshot | design_review_snapshot (unchanged) |
| design_lint_report | design_lint_report (unchanged) |
| datasheet_checklist | design_datasheet_checklist |
