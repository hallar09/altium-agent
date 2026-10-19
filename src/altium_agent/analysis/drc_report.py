# SPDX-License-Identifier: Apache-2.0
"""Read Altium's DRC HTML report and say what matters.

Altium's batch DRC stops counting at 500 violations. On a dense board the
silkscreen and solder-mask-sliver rules fill all 500 and the copper
problems (clearance, short, unrouted, width) never make the list, so a
raw report is both too long to read and silently incomplete. This module:

- counts violations per rule from the report's own summary table;
- flags a report that hit the 500 limit as INCOMPLETE;
- separates silk/mask noise from copper problems, and lists the copper
  problems in full with their location in millimetres;
- names the rules to hide while routing (and re-enable for the final
  report), which on AD26 means deleting them, not disabling them.

Pure parsing over the report file; no Altium. Written for Altium Agent
from the report format; BoardKit's drc_summary.py was the motivation.
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..units import MM_PER_MIL

#: Altium's batch DRC default "stop after" count.
DRC_LIMIT = 500

#: Rules whose findings are silkscreen or solder-mask cosmetics while a
#: board is being routed. Matched against the rule's display name.
NOISE_PATTERNS = (
    re.compile(r"^silk\s*to\s*silk", re.I),
    re.compile(r"^silk\s*to\s*solder\s*mask", re.I),
    re.compile(r"^minimum\s*solder\s*mask\s*sliver", re.I),
)

#: Rule-name stems -> the rule KIND to delete/recreate while routing.
NOISE_RULE_KINDS = {
    "silk to silk": "SilkToSilkClearance",
    "silk to solder mask": "SilkToSolderMaskClearance",
    "minimum solder mask sliver": "MinimumSolderMaskSliver",
}

_COPPER_PATTERNS = (
    re.compile(r"^clearance constraint", re.I),
    re.compile(r"^short-?circuit", re.I),
    re.compile(r"^un-?routed net", re.I),
    re.compile(r"^width constraint", re.I),
    re.compile(r"^differential pairs", re.I),
    re.compile(r"^hole to hole", re.I),
    re.compile(r"^minimum annular ring", re.I),
    re.compile(r"^modified polygon", re.I),
    re.compile(r"^net antennae", re.I),
    re.compile(r"^board clearance", re.I),
    re.compile(r"^power plane connect", re.I),
    re.compile(r"^hole size", re.I),
)

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(
    r'<td class="column1"><a href="#[^"]*">(?P<rule>.*?)</a></td>\s*'
    r'<td class="column2">(?P<count>\d+)</td>', re.S)
_SECTION = re.compile(
    r'<th[^>]*class="rule"[^>]*>(?P<rule>.*?)</th>(?P<body>.*?)</table>', re.S)
_ITEM = re.compile(r'<acronym title="(?P<link>[^"]*)">(?P<text>.*?)</acronym>', re.S)
_LOC = re.compile(r"Location(?P<n>[12])\.(?P<axis>[XY])=(?P<v>-?[\d.]+)(?P<u>mil|mm)")
_FRONT = re.compile(
    r'class="(?:front_matter_column1|DRC_summary_header_col1)">(?P<k>[^<]*?):?</td>\s*'
    r'<td[^>]*></td>\s*<td[^>]*class="(?:front_matter_column3|DRC_summary_header_col3)"'
    r'[^>]*>(?P<v>.*?)</td>', re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", fragment))).strip()


def is_noise(rule: str) -> bool:
    return any(p.search(rule) for p in NOISE_PATTERNS)


def is_copper(rule: str) -> bool:
    return any(p.search(rule) for p in _COPPER_PATTERNS)


@dataclass
class Violation:
    rule: str
    message: str
    location_mm: Optional[tuple] = None     # (x1, y1, x2, y2)


@dataclass
class DrcReport:
    filename: str = ""
    warnings: int = 0
    violations: int = 0
    by_rule: dict = field(default_factory=dict)       # rule -> count
    details: list = field(default_factory=list)       # [Violation]

    @property
    def hit_limit(self) -> bool:
        return self.violations >= DRC_LIMIT

    def noise_rules(self) -> dict:
        return {r: n for r, n in self.by_rule.items() if n and is_noise(r)}

    def copper_rules(self) -> dict:
        return {r: n for r, n in self.by_rule.items() if n and is_copper(r)}

    def other_rules(self) -> dict:
        return {r: n for r, n in self.by_rule.items()
                if n and not is_noise(r) and not is_copper(r)}

    def summary(self, list_noise: bool = False, max_items: int = 200) -> dict[str, Any]:
        copper = [asdict(v) for v in self.details if is_copper(v.rule)]
        others = [asdict(v) for v in self.details
                  if not is_copper(v.rule) and not is_noise(v.rule)]
        out: dict[str, Any] = {
            "ok": True,
            "filename": self.filename,
            "violations": self.violations,
            "warnings": self.warnings,
            "complete": not self.hit_limit,
            "copper_violations": sum(self.copper_rules().values()),
            "noise_violations": sum(self.noise_rules().values()),
            "by_rule": {k: v for k, v in self.by_rule.items() if v},
            "copper": copper[:max_items],
            "other": others[:max_items],
            "dead_copper": [asdict(v) for v in self.details
                            if re.search(r"dead copper|net not assigned",
                                         v.message, re.I)],
        }
        if list_noise:
            out["noise"] = [asdict(v) for v in self.details
                            if is_noise(v.rule)][:max_items]
        if self.hit_limit:
            out["limit_note"] = (
                f"The report stopped at {DRC_LIMIT} violations, so it is "
                "INCOMPLETE: copper problems can be missing from it. Hide "
                "the silk/mask rules while routing (delete them; scripted "
                "disable does not stick in AD26) and run DRC again, then "
                "restore them for the final report.")
        noisy = self.noise_rules()
        if noisy:
            out["hide_while_routing"] = sorted(
                {kind for stem, kind in NOISE_RULE_KINDS.items()
                 for rule in noisy if rule.lower().startswith(stem)})
        return out


def parse(text: str) -> DrcReport:
    report = DrcReport()
    for m in _FRONT.finditer(text):
        key, value = _text(m.group("k")).lower(), _text(m.group("v"))
        if key.startswith("filename"):
            report.filename = value
        elif key.startswith("warnings"):
            report.warnings = int(value or 0) if value.isdigit() else report.warnings
        elif key.startswith("rule violations"):
            report.violations = int(value) if value.isdigit() else report.violations

    # The summary has two tables, Warnings then Rule Violations; only rows
    # after the "Rule Violations" header are rule-violation counts.
    head = text.find(">Rule Violations</th>")
    region = text[head:] if head >= 0 else text
    end = region.find("</table>")
    for m in _ROW.finditer(region[:end if end > 0 else len(region)]):
        report.by_rule[_text(m.group("rule"))] = int(m.group("count"))

    for sec in _SECTION.finditer(text):
        rule = _text(sec.group("rule"))
        for item in _ITEM.finditer(sec.group("body")):
            loc = _location_mm(item.group("link"))
            report.details.append(Violation(rule, _text(item.group("text")), loc))
    if not report.violations:
        report.violations = sum(report.by_rule.values())
    return report


def _location_mm(link: str) -> Optional[tuple]:
    vals: dict[str, float] = {}
    for m in _LOC.finditer(html.unescape(link)):
        v = float(m.group("v"))
        if m.group("u") == "mil":
            v *= MM_PER_MIL
        vals[f"{m.group('axis')}{m.group('n')}"] = round(v, 4)
    if {"X1", "Y1", "X2", "Y2"} <= vals.keys():
        return (vals["X1"], vals["Y1"], vals["X2"], vals["Y2"])
    return None


def summarize_file(path: str | Path, list_noise: bool = False) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "reason": f"no DRC report at {p}"}
    return parse(p.read_text(encoding="utf-8", errors="replace")).summary(list_noise)
