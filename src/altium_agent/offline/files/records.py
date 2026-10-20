# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/records.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Low-level helpers shared by the Altium binary readers.

Altium's native documents (SchDoc, SchLib, PcbDoc, PcbLib) are OLE compound files. Inside, most
streams are sequences of length-prefixed records:

    uint32 header   low 24 bits = payload length, high 8 bits = payload kind (0 = text, 1 = binary)
    payload         text records are "|KEY=VALUE|KEY=VALUE..." terminated by a NUL byte

PCB primitives (tracks, vias, pads, arcs) are binary records with their own framing, handled in
``pcbdoc.py``. Text is cp1252 unless a key is prefixed with ``%UTF8%`` (the UTF-8 copy wins).
"""

from __future__ import annotations

from ...units import MM_PER_MIL

import struct
from collections.abc import Iterator
from dataclasses import dataclass

# PCB internal unit: 1/10000 mil.
PCB_UNITS_PER_MIL = 10_000.0


@dataclass(frozen=True, slots=True)
class RawRecord:
    kind: int  # 0 = text properties, 1 = binary
    payload: bytes


def iter_records(raw: bytes, *, prefix_bytes: int = 0) -> Iterator[RawRecord]:
    """Yield length-prefixed records.

    ``prefix_bytes`` skips a per-record leading field that some PCB streams carry (Rules6 has a
    2-byte record type before the length).
    """
    off = 0
    end = len(raw)
    while off + prefix_bytes + 4 <= end:
        off += prefix_bytes
        (header,) = struct.unpack_from("<I", raw, off)
        length, kind = header & 0x00FFFFFF, header >> 24
        off += 4
        if off + length > end:
            break
        yield RawRecord(kind, raw[off : off + length])
        off += length


def decode_text(payload: bytes) -> str:
    nul = payload.find(b"\x00")
    if nul >= 0:
        payload = payload[:nul]
    return payload.decode("cp1252", errors="replace")


def parse_properties(text: str) -> dict[str, str]:
    """Parse ``|KEY=VALUE|...`` into a dict. Keys are upper-cased; the first occurrence wins,
    except that ``%UTF8%KEY`` overrides ``KEY`` (it carries the lossless Unicode value)."""
    props: dict[str, str] = {}
    utf8: dict[str, str] = {}
    for part in text.split("|"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().upper()
        if not key:
            continue
        if key.startswith("%UTF8%"):
            try:
                value = value.encode("cp1252", errors="strict").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            utf8.setdefault(key[6:], value)
        else:
            props.setdefault(key, value)
    props.update(utf8)
    return props


def text_records(raw: bytes, *, prefix_bytes: int = 0) -> Iterator[dict[str, str]]:
    for rec in iter_records(raw, prefix_bytes=prefix_bytes):
        if rec.kind == 0:
            yield parse_properties(decode_text(rec.payload))


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().upper() in {"T", "TRUE", "1", "YES"}


def parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def parse_int(value: str | None, default: int = 0) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value.strip())
    except ValueError:
        try:
            return int(float(value.strip()))
        except ValueError:
            return default


def parse_length_mil(value: str | None, default: float = 0.0) -> float:
    """Parse PCB text lengths such as ``'60384.8426mil'`` or ``'0.2mm'`` into mils."""
    if value is None:
        return default
    v = value.strip().lower()
    if not v:
        return default
    try:
        if v.endswith("mil"):
            return float(v[:-3])
        if v.endswith("mm"):
            return float(v[:-2]) / MM_PER_MIL
        if v.endswith("in"):
            return float(v[:-2]) * 1000.0
        return float(v)
    except ValueError:
        return default


def mil_to_mm(mil: float) -> float:
    return mil * MM_PER_MIL


def pcb_units_to_mm(units: int | float) -> float:
    return units / PCB_UNITS_PER_MIL * MM_PER_MIL


def sch_coord_to_mil(whole: str | None, frac: str | None = None) -> float:
    """Schematic coordinates are stored in units of 10 mil, with an optional ``_FRAC`` part in
    1/100000 of a unit."""
    return (parse_int(whole) + parse_int(frac) / 100_000.0) * 10.0


class BinaryReader:
    """Little-endian cursor over a bytes payload."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def skip(self, n: int) -> None:
        self.pos += n

    def _unpack(self, fmt: str) -> tuple:
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise EOFError("record truncated")
        values = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return values

    def u8(self) -> int:
        return self._unpack("<B")[0]

    def u16(self) -> int:
        return self._unpack("<H")[0]

    def u32(self) -> int:
        return self._unpack("<I")[0]

    def i32(self) -> int:
        return self._unpack("<i")[0]

    def f64(self) -> float:
        return self._unpack("<d")[0]

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise EOFError("record truncated")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk
