# SPDX-License-Identifier: Apache-2.0
"""Tables of a run's results: one row per task and runner, and the totals
that say whether a change helped."""

from __future__ import annotations

import json
import statistics
from pathlib import Path


def load_results(run_dir: Path) -> list[dict]:
    out = []
    for p in sorted((run_dir / "results").glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec.setdefault("label", p.stem.rsplit("@", 1)[-1])
        out.append(rec)
    return out


def _f(v, digits: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def row(rec: dict) -> dict:
    """The headline numbers of one result."""
    r = rec.get("result") or {}
    pl = rec.get("placement") or {}
    rv = rec.get("routing_vs_human") or {}
    pv = rec.get("placement_vs_human") or {}
    return {
        "task": rec["name"], "label": rec.get("label"), "ok": rec.get("ok"),
        "error": r.get("error") or rv.get("error"),
        "place_errors": pl.get("errors"), "overlaps": pl.get("overlap_pairs"),
        "outside": pl.get("outside_outline"), "unplaced": len(pl.get("unplaced") or []) if pl else None,
        "moved_fixed": len(pl.get("moved_fixed") or []) if pl else None,
        "decap_far": pl.get("decap_far"), "hpwl_x": pv.get("hpwl"),
        "signal_done": rv.get("signal_completion"), "human_signal_done": rv.get("human_signal_completion"),
        "vias_x": rv.get("vias"), "length_x": rv.get("length"),
        "cost_usd": r.get("cost_usd"), "turns": r.get("turns"), "seconds": r.get("seconds"),
    }


COLUMNS = [("task", "task"), ("label", "runner"), ("place_errors", "place err"), ("overlaps", "overlaps"),
           ("outside", "off board"), ("unplaced", "unplaced"), ("decap_far", "decap far"),
           ("hpwl_x", "HPWL x human"), ("signal_done", "routed"), ("human_signal_done", "human routed"),
           ("vias_x", "vias x human"), ("length_x", "length x human"), ("cost_usd", "$"),
           ("turns", "turns"), ("seconds", "s")]


def table(rows: list[dict]) -> str:
    head = "| " + " | ".join(h for _, h in COLUMNS) + " |"
    sep = "|" + "|".join("---" for _ in COLUMNS) + "|"
    lines = [head, sep]
    for r in rows:
        cells = []
        for k, _ in COLUMNS:
            v = r.get(k)
            if k == "task" and r.get("error"):
                v = f"{v} (error: {str(r['error'])[:60]})"
            cells.append(_f(v, 3 if k in ("signal_done", "human_signal_done") else 2))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def shared_tasks(rows: list[dict]) -> set[str]:
    """The tasks every runner label in ``rows`` has a result for."""
    by: dict[str, set[str]] = {}
    for r in rows:
        by.setdefault(r["label"] or "?", set()).add(r["task"])
    return set.intersection(*by.values()) if by else set()


def totals(rows: list[dict], tasks: set[str] | None = None) -> dict[str, dict]:
    """Per runner label: how many tasks scored, and the medians that matter,
    over ``tasks`` only (default: all), so runners are compared on the same
    boards."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        if tasks is None or r["task"] in tasks:
            by.setdefault(r["label"] or "?", []).append(r)

    def med(rs, k):
        vals = [x[k] for x in rs if isinstance(x.get(k), (int, float))]
        return round(statistics.median(vals), 3) if vals else None

    return {label: {"tasks": len(rs), "scored": sum(1 for x in rs if x["ok"]),
                    "place_errors_total": sum(x["place_errors"] or 0 for x in rs),
                    "routed_median": med(rs, "signal_done"), "hpwl_x_median": med(rs, "hpwl_x"),
                    "vias_x_median": med(rs, "vias_x"), "length_x_median": med(rs, "length_x"),
                    "cost_usd_total": round(sum(x["cost_usd"] or 0 for x in rs), 2)}
            for label, rs in sorted(by.items())}


def render(run_dir: Path) -> str:
    rows = [row(r) for r in load_results(run_dir)]
    rows.sort(key=lambda r: (r["task"], r["label"] or ""))
    shared = shared_tasks(rows)
    t = totals(rows, shared)
    out = [f"# Benchmark: {run_dir.name}", "", table(rows), "", "## Per runner", "",
           f"Over the {len(shared)} task(s) every runner has a result for: {', '.join(sorted(shared)) or 'none'}.",
           ""]
    out.append("| runner | tasks | scored | place errors | routed (median) | HPWL x human | vias x human "
               "| length x human | $ |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for label, v in t.items():
        out.append(f"| {label} | {v['tasks']} | {v['scored']} | {v['place_errors_total']} | "
                   f"{_f(v['routed_median'], 3)} | {_f(v['hpwl_x_median'])} | {_f(v['vias_x_median'])} | "
                   f"{_f(v['length_x_median'])} | {_f(v['cost_usd_total'])} |")
    out += ["", "Ratios are result / person on the same board (1.0 = as the person did). 'routed' is the share "
            "of connections the copper makes, by bench/connectivity.py; 'human routed' is the same measure on "
            "the person's board, the ceiling of that model on that board. Results are scored without "
            "Altium's DRC (the in-house engine routes to the board's clearance rules)."]
    return "\n".join(out) + "\n"
