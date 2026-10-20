# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Osman Aslan (kicad-mcp-pro)
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro engines/config_shim.py, adapted there from kicad-mcp-pro. Modified for
# Altium Agent (2026): see docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""The small slice of kicad-mcp-pro's config that ported services use, backed by altium-mcp settings."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _ShimConfig:
    ngspice_cli: Path | None
    cli_timeout: float

    def ensure_output_dir(self, name: str) -> Path:
        from ...offline.context import output_dir_for, project_path_or_none

        return output_dir_for(project_path_or_none(), name)


def get_config() -> _ShimConfig:
    exe = os.environ.get("ALTIUM_AGENT_NGSPICE") or shutil.which("ngspice_con") or shutil.which("ngspice")
    return _ShimConfig(Path(exe) if exe else None, float(os.environ.get("ALTIUM_AGENT_CLI_TIMEOUT", "120")))
