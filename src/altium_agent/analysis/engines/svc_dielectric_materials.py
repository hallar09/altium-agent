# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Osman Aslan (kicad-mcp-pro)
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro engines/svc_dielectric_materials.py, adapted there from kicad-mcp-pro. Modified for
# Altium Agent (2026): see docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""FastMCP-independent dielectric-material catalog service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .impedance import list_dielectric_materials

MaterialsProvider = Callable[[], list[dict[str, object]]]


@dataclass(frozen=True)
class SignalIntegrityDielectricMaterialsService:
    """Render the built-in dielectric material catalog."""

    materials_provider: MaterialsProvider = list_dielectric_materials

    def list_materials(self) -> str:
        materials = self.materials_provider()
        lines = [f"Available dielectric materials ({len(materials)} total):", ""]
        for material in materials:
            lines.append(
                f"  [{material['key']}] {material['name']}  "
                f"Er={material['er']}  tan_d={material['loss_tangent']}"
            )
            lines.append(f"    {material['description']}")
            lines.append("")
        lines.append("Use key string with si_synthesize_stackup_for_interfaces().")
        return "\n".join(lines)
