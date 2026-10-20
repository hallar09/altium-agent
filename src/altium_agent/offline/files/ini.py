# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/ini.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Tolerant reader for Altium's INI-style text files (.PrjPcb, .PrjScr, .OutJob).

These files are not strictly valid for ``configparser`` (duplicate keys, values containing ``=``
and ``%``), so they are parsed by hand. Section and key names keep their original case; lookups
are case-insensitive.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


class IniSection:
    __slots__ = ("name", "_items")

    def __init__(self, name: str) -> None:
        self.name = name
        self._items: dict[str, tuple[str, str]] = {}

    def set(self, key: str, value: str) -> None:
        self._items.setdefault(key.lower(), (key, value))

    def get(self, key: str, default: str = "") -> str:
        item = self._items.get(key.lower())
        return item[1] if item else default

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._items

    def items(self) -> Iterator[tuple[str, str]]:
        yield from self._items.values()

    def as_dict(self) -> dict[str, str]:
        return dict(self._items.values())


class IniFile:
    def __init__(self, sections: list[IniSection]) -> None:
        self.sections = sections
        self._by_name = {s.name.lower(): s for s in sections}

    @classmethod
    def read(cls, path: Path) -> IniFile:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            text = raw[3:].decode("utf-8", errors="replace")
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")
        return cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> IniFile:
        sections: list[IniSection] = []
        current: IniSection | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current = IniSection(stripped[1:-1])
                sections.append(current)
                continue
            if current is None or "=" not in line:
                continue
            key, value = line.split("=", 1)
            current.set(key.strip(), value.strip())
        return cls(sections)

    def section(self, name: str) -> IniSection | None:
        return self._by_name.get(name.lower())

    def numbered(self, prefix: str) -> list[IniSection]:
        """Sections named ``<prefix><n>`` in numeric order (e.g. Document1, Document2...)."""
        found: list[tuple[int, IniSection]] = []
        plen = len(prefix)
        for s in self.sections:
            if s.name.lower().startswith(prefix.lower()) and s.name[plen:].isdigit():
                found.append((int(s.name[plen:]), s))
        return [s for _, s in sorted(found, key=lambda t: t[0])]
