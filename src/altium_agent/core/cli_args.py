# SPDX-License-Identifier: Apache-2.0
"""Command-line list arguments that survive names starting with '-'.

Net names such as ``-LED_R`` (an active-low signal) are common, and
argparse reads ``--nets -LED_R`` as two options: the list goes missing and
the next flag is mis-parsed. BoardKit hit it and documented the ``--nets=``
form; Altium Agent makes the CLI accept both spellings by joining a list
option with the value that follows it before argparse sees the line.

MCP tool arguments are JSON and never have this problem; this is for the
``altium-agent`` command line only.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def join_list_options(argv: Sequence[str], options: Iterable[str]) -> list[str]:
    """``["--nets", "-LED_R,A"]`` -> ``["--nets=-LED_R,A"]`` for each named
    option; everything else is passed through unchanged."""
    wanted = set(options)
    out: list[str] = []
    i = 0
    args = list(argv)
    while i < len(args):
        arg = args[i]
        if arg in wanted and i + 1 < len(args):
            out.append(f"{arg}={args[i + 1]}")
            i += 2
            continue
        out.append(arg)
        i += 1
    return out


def split_names(value: str, sep: str = ",") -> list[str]:
    """Split a list value, keeping leading '-' and dropping empty items."""
    return [part.strip() for part in str(value or "").split(sep) if part.strip()]
