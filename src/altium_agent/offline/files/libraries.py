# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/libraries.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Readers for Altium source libraries (.SchLib symbols, .PcbLib footprints)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import olefile

from .records import BinaryReader, iter_records, decode_text, parse_int, parse_properties, parse_length_mil, mil_to_mm

_SUBRECORDS = {1: 1, 3: 1, 4: 1, 5: 2, 6: 1, 11: 1, 12: 1}  # record type -> subrecord count (pads handled apart)


@dataclass
class LibSymbol:
    lib_ref: str
    description: str
    part_count: int
    pin_count: int | None
    footprints: list[str]
    parameters: dict[str, str]

    def to_dict(self) -> dict:
        return {"lib_ref": self.lib_ref, "description": self.description, "parts": self.part_count,
                "pins": self.pin_count, "footprints": self.footprints, "parameters": self.parameters}


@dataclass
class LibFootprint:
    name: str
    description: str
    height_mm: float
    pad_count: int | None

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "height_mm": round(self.height_mm, 3),
                "pads": self.pad_count}


def _storage_name(name: str) -> str:
    # OLE storage names are limited to 31 characters; Altium also replaces '/'.
    return name.replace("/", "_")[:31]


def read_schlib(path: Path) -> list[LibSymbol]:
    st = Path(path).stat()
    return _read_schlib(str(Path(path).resolve()), st.st_mtime_ns)


@lru_cache(maxsize=32)
def _read_schlib(path_str: str, _mtime: int) -> list[LibSymbol]:
    out: list[LibSymbol] = []
    with olefile.OleFileIO(path_str) as ole:
        header = {}
        for rec in iter_records(ole.openstream("FileHeader").read()):
            if rec.kind == 0:
                header = parse_properties(decode_text(rec.payload))
                break
        storages = {e[0] for e in ole.listdir() if len(e) == 2}
        for i in range(parse_int(header.get("COMPCOUNT"))):
            ref = header.get(f"LIBREF{i}", "")
            sym = LibSymbol(ref, header.get(f"COMPDESCR{i}", ""), parse_int(header.get(f"PARTCOUNT{i}"), 2) - 1,
                            None, [], {})
            storage = _storage_name(ref)
            if storage in storages and ole.exists(f"{storage}/Data"):
                pins = 0
                for rec in iter_records(ole.openstream(f"{storage}/Data").read()):
                    if rec.kind == 1:
                        if rec.payload[:1] == b"\x02":
                            pins += 1
                        continue
                    props = parse_properties(decode_text(rec.payload))
                    rid = props.get("RECORD")
                    if rid == "2":
                        pins += 1
                    elif rid == "45" and props.get("MODELTYPE", "").upper() == "PCBLIB":
                        sym.footprints.append(props.get("MODELNAME", ""))
                    elif rid == "41" and props.get("NAME"):
                        sym.parameters[props["NAME"]] = props.get("TEXT", "")
                sym.pin_count = pins
            out.append(sym)
    return out


def _count_pads(raw: bytes) -> int | None:
    r = BinaryReader(raw)
    try:
        r.skip(r.u32())  # footprint name header
        pads = 0
        while r.remaining() > 0:
            rtype = r.u8()
            if rtype == 2:
                for _ in range(6):
                    r.skip(r.u32())
                pads += 1
            elif rtype in _SUBRECORDS:
                for _ in range(_SUBRECORDS[rtype]):
                    r.skip(r.u32())
            elif rtype == 0:
                break
            else:
                return None
        return pads
    except EOFError:
        return None


def footprint_pads(path: Path, name: str) -> list[dict]:
    """Pads of one footprint (mm, footprint origin), using the same Pads6 record format as boards."""
    from .pcbdoc import _Origin, _pads

    with olefile.OleFileIO(str(path)) as ole:
        storage = None
        for s in sorted({e[0] for e in ole.listdir() if len(e) >= 2}):
            if ole.exists(f"{s}/Parameters"):
                for rec in iter_records(ole.openstream(f"{s}/Parameters").read()):
                    if parse_properties(decode_text(rec.payload)).get("PATTERN", s).lower() == name.lower():
                        storage = s
                    break
            if storage:
                break
        if storage is None:
            raise KeyError(name)
        raw = ole.openstream(f"{storage}/Data").read()
    r = BinaryReader(raw)
    r.skip(r.u32())
    pad_bytes = bytearray()
    while r.remaining() > 0:
        start = r.pos
        rtype = r.u8()
        if rtype == 2:
            for _ in range(6):
                r.skip(r.u32())
            pad_bytes += raw[start:r.pos]
        elif rtype in _SUBRECORDS:
            for _ in range(_SUBRECORDS[rtype]):
                r.skip(r.u32())
        else:
            break
    shapes = {1: "round", 2: "rect", 3: "octagonal", 9: "rounded_rect"}
    return [{"name": p.name, "x_mm": round(p.x, 4), "y_mm": round(p.y, 4), "size_mm": [round(p.size_x, 4), round(p.size_y, 4)],
             "hole_mm": round(p.hole, 4), "plated": p.plated, "shape": shapes.get(p.shape, str(p.shape)),
             "rotation": round(p.rotation % 360, 2), "smd": p.hole == 0}
            for p in _pads(bytes(pad_bytes), _Origin(0, 0))]


def read_pcblib(path: Path) -> list[LibFootprint]:
    st = Path(path).stat()
    return _read_pcblib(str(Path(path).resolve()), st.st_mtime_ns)


@lru_cache(maxsize=32)
def _read_pcblib(path_str: str, _mtime: int) -> list[LibFootprint]:
    out: list[LibFootprint] = []
    with olefile.OleFileIO(path_str) as ole:
        storages = sorted({e[0] for e in ole.listdir() if len(e) >= 2 and e[0] != "Library"})
        for s in storages:
            # Footprints are the storages carrying both Data and Parameters streams.
            if not (ole.exists(f"{s}/Data") and ole.exists(f"{s}/Parameters")):
                continue
            props: dict[str, str] = {}
            for rec in iter_records(ole.openstream(f"{s}/Parameters").read()):
                props = parse_properties(decode_text(rec.payload))
                break
            out.append(LibFootprint(
                name=props.get("PATTERN", s),
                description=props.get("DESCRIPTION", ""),
                height_mm=mil_to_mm(parse_length_mil(props.get("HEIGHT"))),
                pad_count=_count_pads(ole.openstream(f"{s}/Data").read()),
            ))
    return out
