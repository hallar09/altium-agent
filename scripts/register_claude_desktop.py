# SPDX-License-Identifier: Apache-2.0
"""Register Altium Agent in the Claude desktop app's config.

Backs up claude_desktop_config.json, adds the "AltiumAgent" server, and moves
any other Altium MCP server ("altium") into a side file so the two bridges do
not collide. Everything else in the file is left as it was. Run with the
checkout's .venv python (register_claude_desktop.cmd does that).
"""

import datetime
import json
import os
import sys
from pathlib import Path

OTHER_ALTIUM_SERVERS = ("altium",)


def main() -> int:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        print("APPDATA is not set; this is for Windows.")
        return 1
    cfg = Path(appdata) / "Claude" / "claude_desktop_config.json"
    exe = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "altium-agent.exe"
    if not exe.exists():
        print(f"{exe} is missing: run scripts\\install_windows.ps1 first.")
        return 1

    data = {}
    if cfg.exists():
        raw = cfg.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            print(f"{cfg} is not valid JSON ({e}).")
            print("It is left untouched. Fix it, or delete it, and run this again.")
            return 1
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = cfg.with_name(f"claude_desktop_config.json.bak-{stamp}")
        backup.write_text(raw, encoding="utf-8")
        print(f"Backed up to {backup}")
    else:
        cfg.parent.mkdir(parents=True, exist_ok=True)

    servers = data.setdefault("mcpServers", {})
    shelved = {k: servers.pop(k) for k in list(servers) if k in OTHER_ALTIUM_SERVERS}
    if shelved:
        side = cfg.with_name("altium-mcp-servers-turned-off.json")
        side.write_text(json.dumps(shelved, indent=2), encoding="utf-8")
        print(f"Turned off {', '.join(shelved)} (saved in {side} to put back later)")
    servers["AltiumAgent"] = {"command": str(exe), "args": []}
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Registered AltiumAgent in {cfg}")
    print("Now open the Claude desktop app again (quit it fully first if it is still running).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
