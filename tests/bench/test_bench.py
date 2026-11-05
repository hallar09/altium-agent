# SPDX-License-Identifier: Apache-2.0
"""The placement and routing benchmark (altium_agent.bench).

Most tests build a small board in memory. Those marked ``wifi`` read
Altium's own WiFi example when it is installed and are skipped otherwise;
they are the ones that prove the pieces agree on a real design.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from altium_agent.bench import connectivity as C
from altium_agent.bench import mask, report
from altium_agent.bench.run import RunOptions, agent_command, transcript_stats
from altium_agent.bench.score import placement_metrics, result_metrics
from altium_agent.bench.transform import (apply_placements, move_components, positions, stage_parts,
                                          strip_routing)
from altium_agent.offline.files.pcbdoc import (NONE16, Pad, PcbComponent, PcbDocument, StackLayer, Track,
                                               Via, read_pcbdoc)
from altium_agent.placement.footprints import At, board_box, footprints_of

_PUBLIC = Path(os.environ.get("PUBLIC") or Path(os.environ.get("SystemDrive", "C:") + os.sep) / "Users" / "Public")
WIFI = _PUBLIC / "Documents" / "Altium" / "AD26" / "Examples" / "Mini PC" / "Mini PC - WiFi" / "WiFi.PcbDoc"
wifi = pytest.mark.skipif(not WIFI.exists(), reason="Altium's WiFi example is not installed")


def _comp(i, ref, x, y, rot=0.0, side="top", locked=False):
    return PcbComponent(i, ref, "FP", "", x, y, rot, side, locked, 1.0, "", "", "")


def _pad(name, net, comp, x, y, layer=1, size=1.0):
    return Pad(name, layer, net, comp, x, y, size, size, 0.0, False, 2, 0.0)


def board(with_route: bool = True) -> PcbDocument:
    """Two resistors on a 40 x 20 mm board, net A between them (routed with
    a track on the top), net B through a via to the bottom."""
    outline = [{"x": 0, "y": 0}, {"x": 40, "y": 0}, {"x": 40, "y": 20}, {"x": 0, "y": 20}]
    doc = PcbDocument(
        Path("t.PcbDoc"), {}, (0.0, 0.0), outline,
        [StackLayer(0, "Top", 0x01000001, "copper"), StackLayer(1, "Bottom", 0x0100FFFF, "copper")],
        [_comp(0, "R1", 10, 10), _comp(1, "R2", 30, 10)], ["A", "B"], [], [], [], [])
    doc.pads = [_pad("1", 0, 0, 9, 10), _pad("2", 1, 0, 11, 10),
                _pad("1", 0, 1, 29, 10), _pad("2", 1, 1, 31, 10, layer=32)]
    # footprint silk (component-owned) survives a strip
    doc.tracks = [Track(33, NONE16, 0, 8, 9, 12, 9, 0.2)]
    if with_route:
        doc.tracks += [Track(1, 0, NONE16, 9, 10, 29, 10, 0.3),          # A
                       Track(1, 1, NONE16, 11, 10, 11, 15, 0.3),         # B, top, to the via
                       Track(32, 1, NONE16, 11, 15, 31, 15, 0.3),        # B, bottom
                       Track(32, 1, NONE16, 31, 15, 31, 10, 0.3)]
        doc.vias = [Via(1, 11, 15, 0.6, 0.3, 1, 32)]
    return doc


# --------------------------------------------------------------------------- transform

def test_strip_keeps_footprints_and_removes_routing():
    s = strip_routing(board())
    assert s.vias == []
    assert [t.layer for t in s.tracks] == [33]
    assert len(s.pads) == 4


def test_moving_parts_onto_themselves_changes_nothing():
    d = board()
    same = apply_placements(d, list(positions(d).values()))
    assert max(abs(a.x - b.x) + abs(a.y - b.y) for a, b in zip(d.pads, same.pads)) < 1e-6


def test_a_move_and_flip_keeps_the_footprint_and_swaps_its_layers():
    d = board(with_route=False)
    m = move_components(d, {"R1": At(20, 5, 90, "bottom")})
    a = {f.ref: f for f in footprints_of(d)}["R1"]
    b = {f.ref: f for f in footprints_of(m)}["R1"]
    assert sorted((p.name, p.x, p.y) for p in a.pins) == sorted((p.name, p.x, p.y) for p in b.pins)
    assert {p.layer for p in m.pads if p.component == 0} == {32}
    assert {t.layer for t in m.tracks if t.component == 0} == {34}     # silk follows to the bottom


def test_moving_a_part_that_is_not_there_is_refused():
    with pytest.raises(KeyError, match="R9"):
        move_components(board(), {"R9": At(0, 0, 0, "top")})


def test_staged_parts_are_off_the_board_and_apart():
    d = stage_parts(strip_routing(board()), ["R1", "R2"])
    boxes = [board_box(f) for f in footprints_of(d)]
    assert all(b.min_x > 40 for b in boxes)
    a, b = boxes
    assert a.max_y <= b.min_y or b.max_y <= a.min_y or a.max_x <= b.min_x or b.max_x <= a.min_x


# --------------------------------------------------------------------------- connectivity

def test_a_routed_board_is_complete_and_a_stripped_one_is_not():
    full = C.summary(C.connect(C.copper_of(board())))
    assert full["completion"] == 1.0 and full["connections_needed"] == 2
    bare = C.summary(C.connect(C.copper_of(strip_routing(board()))))
    assert bare["missing_connections"] == 2


def test_a_track_on_the_wrong_layer_joins_nothing():
    d = board(with_route=False)
    d.tracks = [Track(32, 0, NONE16, 9, 10, 29, 10, 0.3)]      # pads of A are on the top
    assert C.connect(C.copper_of(d))["A"].missing == 1


def test_a_pour_joins_what_sits_inside_it():
    d = board(with_route=False)
    d.polygons = [{"name": "A", "net": "A", "layer": "TOP", "shelved": False,
                   "outline_mm": [[0, 0], [40, 0], [40, 20], [0, 20]]}]
    assert C.connect(C.copper_of(d))["A"].missing == 0
    assert C.connect(C.copper_of(d))["B"].missing == 1


def test_an_engine_result_counts_only_its_own_copper():
    d = strip_routing(board())
    routed = board()
    segs = [C.Seg(d.net_name(t.net), t.layer, t.x1, t.y1, t.x2, t.y2, t.width)
            for t in routed.tracks if t.component == NONE16]
    dots = [C.Dot("B", 11, 15, 0.6)]
    m = result_metrics(d, segs, dots)
    assert m["all"]["completion"] == 1.0
    assert m["vias"] == 1 and m["length_mm"] == 50.0      # 20 (A) + 5 + 20 + 5 (B)


# --------------------------------------------------------------------------- scoring

def test_placement_metrics_flag_overlaps_and_parts_off_the_board():
    d = board(with_route=False)
    clash = move_components(d, {"R2": At(10, 10, 0, "top")})
    assert placement_metrics(clash)["overlap_pairs"] == 1
    off = move_components(d, {"R2": At(60, 10, 0, "top")})
    assert placement_metrics(off)["outside_outline"] == 1


# --------------------------------------------------------------------------- mask

def test_the_mask_is_off_without_the_variable(monkeypatch):
    monkeypatch.delenv(mask.ENV, raising=False)
    assert mask.entry_for("anything.PcbDoc") is None


def test_a_missing_mask_file_is_loud(monkeypatch, tmp_path):
    monkeypatch.setenv(mask.ENV, str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError, match="nope.json"):
        mask.entry_for(tmp_path / "x.PcbDoc")


def test_a_mask_entry_is_found_by_any_spelling_of_the_path(monkeypatch, tmp_path):
    pcb = tmp_path / "Sub" / "B.PcbDoc"
    m = mask.write(tmp_path / "mask.json", {str(pcb): {"strip_routing": True}})
    monkeypatch.setenv(mask.ENV, str(m))
    assert mask.entry_for(str(pcb).upper() if os.name == "nt" else pcb) == {"strip_routing": True}
    assert mask.entry_for(tmp_path / "other.PcbDoc") is None


def test_applying_a_mask_never_edits_the_board_it_was_given():
    d = board()
    before = (len(d.tracks), len(d.vias), [c.x_mm for c in d.components])
    mask.apply(d, {"strip_routing": True, "stage": ["R1"]})
    assert (len(d.tracks), len(d.vias), [c.x_mm for c in d.components]) == before


# --------------------------------------------------------------------------- runners

def test_the_agent_sees_only_its_server_and_the_runs_skills(tmp_path):
    entry = {"name": "b-place", "outputs": str(tmp_path / "out")}
    args, cfg = agent_command(tmp_path, entry, RunOptions(claude=__file__))
    for flag in ("--strict-mcp-config", "--setting-sources", "--max-budget-usd", "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--setting-sources") + 1] == "project"
    env = json.loads(cfg.read_text())["mcpServers"]["AltiumAgent"]["env"]
    assert env[mask.ENV] == str(tmp_path / "mask.json")
    assert env["ALTIUM_AGENT_OUTPUT_DIR"] == entry["outputs"]


def test_transcript_stats_reads_cost_turns_and_tool_calls(tmp_path):
    log = tmp_path / "t.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__AltiumAgent__place_plan"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__AltiumAgent__place_plan"},
                                                      {"type": "text", "text": "hi"}]}},
        "not json",
        {"type": "result", "total_cost_usd": 0.42, "num_turns": 7, "is_error": False, "subtype": "success"},
    ]) + "\nnot json either\n")
    s = transcript_stats(log)
    assert s["cost_usd"] == 0.42 and s["turns"] == 7
    assert s["tool_calls"] == {"mcp__AltiumAgent__place_plan": 2}


def test_the_report_has_a_row_per_result_and_a_line_per_runner(tmp_path):
    (tmp_path / "results").mkdir()
    for label, routed in (("baseline", 0.5), ("agent", 0.8)):
        (tmp_path / "results" / f"b-place@{label}.json").write_text(json.dumps({
            "name": "b-place", "label": label, "ok": True, "result": {"cost_usd": 1.0 if label == "agent" else None},
            "placement": {"errors": 0, "overlap_pairs": 0, "outside_outline": 0},
            "routing_vs_human": {"signal_completion": routed, "human_signal_completion": 1.0}}))
    text = report.render(tmp_path)
    assert text.count("| b-place |") == 2
    assert "| agent | 1 | 1 |" in text and "| baseline | 1 | 1 |" in text


# --------------------------------------------------------------------------- the real board

@wifi
def test_the_connectivity_model_finds_the_person_s_board_complete():
    s = C.summary(C.connect(C.copper_of(read_pcbdoc(WIFI))))
    assert s["completion"] == 1.0, s["incomplete_nets"]


@wifi
def test_a_prepared_task_shows_every_tool_the_same_stripped_board(tmp_path, monkeypatch):
    from altium_agent.bench import corpus
    from altium_agent.bench.prepare import prepare

    spec = corpus.load(ids=["wifi"])[0]
    rec = prepare([spec], ["place"], tmp_path / "run")
    entry = rec["tasks"][0]
    monkeypatch.setenv(mask.ENV, str(tmp_path / "run" / "mask.json"))
    full = read_pcbdoc(Path(entry["pcb"]))
    light = read_pcbdoc(Path(entry["pcb"]), primitives=False)
    assert [t for t in full.tracks if t.component == NONE16 and 1 <= t.layer <= 32] == []
    assert full.vias == []
    assert positions(full) == positions(light)        # staged the same way for every reader
    assert "R1" not in json.loads((tmp_path / "run" / "answers" / "wifi.json").read_text())["fixed"]
    # the person's board is untouched, and only the copy is masked
    monkeypatch.delenv(mask.ENV)
    assert len(read_pcbdoc(WIFI).vias) > 0
    assert not (Path(entry["folder"]) / "RESULT.json").exists()


def test_a_failed_agent_run_is_a_failure_not_someone_else_s_plan(tmp_path, monkeypatch):
    """A Claude Code that could not run (here: not signed in) must not be
    scored with the baseline's plan lying in the same output folder."""
    import altium_agent.bench.run as R

    out = tmp_path / "out"
    out.mkdir()
    (out / "baseline.json").write_text("{}")
    folder = tmp_path / "task"
    folder.mkdir()
    entry = {"name": "b-place", "task": "place", "folder": str(folder), "outputs": str(out)}

    def fake_run(args, cwd, stdout, **kw):
        stdout.write(json.dumps({"type": "result", "is_error": True, "num_turns": 1, "total_cost_usd": 0,
                                 "result": "Not logged in"}) + "\n")
        return type("P", (), {"returncode": 1})()

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    rec = R.run_agent(tmp_path, entry, RunOptions(claude=__file__))
    assert "Not logged in" in rec["error"]
    assert "plan" not in rec


def test_the_agent_does_not_inherit_a_host_claude_session():
    from altium_agent.bench.run import agent_env

    host = {"PATH": "p", "CLAUDECODE": "1", "CLAUDE_CODE_HOST_SESSION_ID": "x", "ANTHROPIC_BASE_URL": "http://h",
            "CLAUDE_AGENT_SDK_VERSION": "1", mask.ENV: "m", "ANTHROPIC_API_KEY": "k"}
    assert agent_env(host) == {"PATH": "p", "ANTHROPIC_API_KEY": "k"}
    # outside a Claude session a deliberate base URL is the user's to keep
    assert agent_env({"PATH": "p", "ANTHROPIC_BASE_URL": "http://proxy"}) == {"PATH": "p",
                                                                             "ANTHROPIC_BASE_URL": "http://proxy"}


def test_a_rerun_cannot_read_the_previous_run_s_answer(tmp_path, monkeypatch):
    import altium_agent.bench.run as R

    folder, out = tmp_path / "task", tmp_path / "out"
    folder.mkdir()
    out.mkdir()
    (folder / "RESULT.json").write_text('{"plan": "old.json", "notes": "what I did last time"}')
    seen = {}

    def fake_run(args, cwd, stdout, **kw):
        seen["old_visible"] = (Path(cwd) / "RESULT.json").exists()
        stdout.write(json.dumps({"type": "result", "is_error": False, "num_turns": 3}) + "\n")
        (Path(cwd) / "RESULT.json").write_text('{"plan": "new.json"}')
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    entry = {"name": "b-place", "task": "place", "folder": str(folder), "outputs": str(out)}
    rec = R.run_agent(tmp_path, entry, RunOptions(claude=__file__, label="v2"))
    assert seen["old_visible"] is False
    assert rec["plan"] == "new.json"
    assert rec["transcript"].endswith("b-place@v2.jsonl")
    assert list((tmp_path / "transcripts").glob("b-place.RESULT-*.json"))


def test_runners_are_compared_on_the_tasks_they_share(tmp_path):
    (tmp_path / "results").mkdir()

    def put(task, label, routed):
        (tmp_path / "results" / f"{task}@{label}.json").write_text(json.dumps({
            "name": task, "label": label, "ok": True, "result": {},
            "routing_vs_human": {"signal_completion": routed}}))

    put("a-place", "baseline", 0.2)
    put("b-place", "baseline", 0.9)      # the agent did not run this one
    put("a-place", "agent", 0.5)
    text = report.render(tmp_path)
    assert "Over the 1 task(s) every runner has a result for: a-place." in text
    assert "| baseline | 1 | 1 | 0 | 0.200 |" in text


def test_copper_repeated_by_a_later_pass_counts_once():
    d = strip_routing(board())
    seg = C.Seg("A", 1, 9, 10, 29, 10, 0.3)
    m = result_metrics(d, [seg, C.Seg("A", 1, 9, 10, 29, 10, 0.3)], [C.Dot("B", 11, 15, 0.6)] * 2)
    assert m["length_mm"] == 20.0 and m["vias"] == 1
