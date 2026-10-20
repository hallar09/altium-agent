# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro errors.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Typed errors. Every error carries a stable code and an actionable hint for the agent."""

from __future__ import annotations


class OfflineError(Exception):
    code = "offline_error"

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        d = {"error": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (f" Hint: {self.hint}" if self.hint else "")


class PathNotAllowed(OfflineError):
    code = "path_not_allowed"


class FileFormatError(OfflineError):
    code = "file_format_error"


class ProjectNotFound(OfflineError):
    code = "project_not_found"


class BridgeUnavailable(OfflineError):
    code = "bridge_unavailable"


class BridgeTimeout(OfflineError):
    code = "bridge_timeout"


class BridgeCommandFailed(OfflineError):
    code = "bridge_command_failed"


class InvalidArgument(OfflineError):
    code = "invalid_argument"
