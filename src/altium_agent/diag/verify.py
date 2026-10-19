# SPDX-License-Identifier: Apache-2.0
"""Record that a tool was verified against a live Altium.

``live_verified`` is the only maturity badge that is a RESULT rather than
a property of the code, so it is written only here, and only while a live
Altium answers: the record carries the Altium version and the deployed
script stamp the bridge reports at that moment, plus an evidence file the
person verifying wrote (a transcript, a screenshot, a DRC report).

Used by ``altium-agent verify record``; see docs/RELEASE_VERIFICATION.md.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Callable, Optional

LEDGER = Path(__file__).resolve().parents[1] / "tools" / "live_verified.json"


def _live_identity(ping: Callable[[], Optional[dict]],
                   version: Callable[[], Optional[dict]]) -> dict:
    answer = ping()
    if not answer:
        raise RuntimeError(
            "Altium did not answer a ping, so nothing can be recorded as "
            "verified live. Start Altium and the bridge, then retry.")
    info = version() or {}
    return {
        "altium_version": str(info.get("version") or info.get("altium_version")
                              or answer.get("altium_version") or "unknown"),
        "script_stamp": str(answer.get("script_version") or "unknown"),
    }


def record(tools: list[str], evidence: str, note: str = "",
           ledger: Path = LEDGER,
           ping: Optional[Callable[[], Optional[dict]]] = None,
           version: Optional[Callable[[], Optional[dict]]] = None,
           known_tools: Optional[set] = None) -> dict:
    from ..tools.metadata import OFFLINE, maturity_of

    if not tools:
        raise ValueError("name at least one tool")
    ev = Path(evidence)
    if not ev.is_file():
        raise FileNotFoundError(f"evidence file {ev} does not exist")
    if known_tools is not None:
        unknown = [t for t in tools if t not in known_tools]
        if unknown:
            raise ValueError(f"not registered tools: {unknown}")
    offline = [t for t in tools if maturity_of(t) == OFFLINE]
    if offline:
        raise ValueError(f"offline tools need no live record: {offline}")
    if ping is None or version is None:
        from ..bridge import get_bridge
        bridge = get_bridge()
        ping = ping or bridge.ping_with_version
        version = version or (lambda: bridge.send_command(
            "application.get_version", {}, timeout=10.0))
    ident = _live_identity(ping, version)
    data = json.loads(ledger.read_text(encoding="utf-8")) if ledger.is_file() \
        else {"records": []}
    records = [r for r in data.get("records", []) if r.get("tool") not in tools]
    stamp = _dt.date.today().isoformat()
    for t in tools:
        records.append({"tool": t, "date": stamp, "evidence": str(ev),
                        "note": note, **ident})
    data["records"] = sorted(records, key=lambda r: r["tool"])
    ledger.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return {"ok": True, "recorded": tools, **ident, "ledger": str(ledger)}
