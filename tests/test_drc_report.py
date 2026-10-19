# SPDX-License-Identifier: Apache-2.0
"""Altium's DRC report is read for what matters, and a report cut off at
500 violations says it is incomplete.

The fixture is generated in Altium's report markup (front matter, the two
summary tables, one section per rule with zoom links), so the parser is
tested on the shape it meets without shipping a real design's report.
"""

from __future__ import annotations

import asyncio

import pytest

from altium_agent.analysis import drc_report as dr


def _report(rows: dict, sections: dict, total: int | None = None) -> str:
    total = sum(rows.values()) if total is None else total
    front = (
        '<tr class="front_matter"><td class="front_matter_column1">Filename:</td>'
        '<td class="front_matter_column2"></td><td class="front_matter_column3">'
        '<a href="file:///C:/b/Board.PcbDoc"><acronym title="x">C:\\b\\Board.PcbDoc'
        '</acronym></a></td></tr>'
        '<table class="DRC_summary_header"><tr><td class="DRC_summary_header_col1">'
        'Warnings:</td><td class="DRC_summary_header_col2"></td>'
        '<td class="DRC_summary_header_col3">0</td></tr><tr>'
        '<td class="DRC_summary_header_col1">Rule Violations:</td>'
        '<td class="DRC_summary_header_col2"></td>'
        f'<td class="DRC_summary_header_col3" style="color : red">{total}</td></tr></table>')
    summary = ('<table><tr><th class="column1">Warnings</th><th class="column2">Count'
               '</th></tr><tr><td class="column1">Total</td><td class="column2">0</td>'
               '</tr></table><br><table><tr><th class="column1">Rule Violations</th>'
               '<th class="column2">Count</th></tr>')
    for i, (rule, n) in enumerate(rows.items()):
        summary += (f'<tr class="onmouseout_odd"><td class="column1"><a href="#ID{i}">'
                    f'{rule}</a></td>\n<td class="column2">{n}</td>\n</tr>')
    summary += f'<tr><td class="column1">Total</td><td class="column2">{total}</td></tr></table>'
    body = ""
    for rule, items in sections.items():
        body += f'<table><tr><th style="text-align : left" class="rule">{rule}</th></tr>'
        for text, (x1, y1, x2, y2) in items:
            link = (f"dxpprocess://PCB:Zoom?document=C:\\b\\Board.PcbDoc;Action=AREA_DYNAMICZOOM"
                    f"|Location1.X={x1}mil|Location2.X={x2}mil|Location1.Y={y1}mil"
                    f"|Location2.Y={y2}mil|Absolute=True")
            body += (f'<tr><td class="column1"><a href="{link}" class="callback">'
                     f'<acronym title="{link}">{text}</acronym></a><br></td></tr>')
        body += "</table>"
    return "<html><body><table>" + front + "</table>" + summary + body + "</body></html>"


CLEAR = "Clearance Constraint (Gap=0.1mm) (All),(All)"
SLIVER = "Minimum Solder Mask Sliver (Gap=10mil) (All),(All)"
SILK = "Silk to Silk (Clearance=10mil) (All),(All)"


def test_counts_copper_and_noise_separately():
    html = _report({CLEAR: 2, SLIVER: 30, SILK: 5},
                   {CLEAR: [("Clearance Constraint: (0.08mm &lt; 0.1mm) Between Track and Pad",
                             (1000, 1000, 1010, 1010))] * 2,
                    SLIVER: [("Minimum Solder Mask Sliver Constraint", (0, 0, 1, 1))] * 30})
    s = dr.parse(html).summary()
    assert s["violations"] == 37 and s["complete"] is True
    assert s["copper_violations"] == 2 and s["noise_violations"] == 35
    assert len(s["copper"]) == 2 and "noise" not in s
    assert s["copper"][0]["location_mm"][0] == pytest.approx(25.4)
    assert s["filename"].endswith("Board.PcbDoc")
    assert s["hide_while_routing"] == ["MinimumSolderMaskSliver", "SilkToSilkClearance"]


def test_a_report_at_the_limit_is_incomplete():
    s = dr.parse(_report({SLIVER: 420, SILK: 80}, {})).summary()
    assert s["violations"] == 500 and s["complete"] is False
    assert "INCOMPLETE" in s["limit_note"] and "delete" in s["limit_note"]


def test_dead_copper_is_named():
    rule = "Un-Routed Net Constraint ( (All) )"
    s = dr.parse(_report({rule: 1}, {rule: [(
        "Un-Routed Net Constraint: Polygon (NONET_L06_P008) on Bottom Dead Copper - Net Not Assigned.",
        (0, 0, 10, 10))]})).summary()
    assert s["dead_copper"] and "NONET_L06_P008" in s["dead_copper"][0]["message"]


def test_noise_is_listed_only_when_asked():
    html = _report({SILK: 1}, {SILK: [("Silk To Silk Clearance Constraint", (0, 0, 1, 1))]})
    assert "noise" in dr.parse(html).summary(list_noise=True)


def test_the_tool_reads_a_file(tmp_path):
    from altium_agent.tools.agent import register_agent_tools

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    register_agent_tools(_Mcp())
    path = tmp_path / "drc.html"
    path.write_text(_report({CLEAR: 1}, {}), encoding="utf-8")
    out = asyncio.run(tools["analyze_drc_report"](str(path)))
    assert out["ok"] and out["copper_violations"] == 1
    missing = asyncio.run(tools["analyze_drc_report"](str(tmp_path / "nope.html")))
    assert missing["ok"] is False
