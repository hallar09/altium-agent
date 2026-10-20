# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/schdoc.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Reader for Altium schematic documents (.SchDoc).

The ``FileHeader`` stream holds a header record followed by one text record per object. Records
reference their parent through ``OWNERINDEX`` (index among the records after the header).
Coordinates are converted to mils.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import olefile

from .records import parse_bool, parse_int, sch_coord_to_mil, text_records

# Record ids used here.
R_COMPONENT = 1
R_PIN = 2
R_SHEET_SYMBOL = 15
R_SHEET_ENTRY = 16
R_POWER_PORT = 17
R_PORT = 18
R_NO_ERC = 22
R_NET_LABEL = 25
R_BUS = 26
R_WIRE = 27
R_JUNCTION = 29
R_SHEET = 31
R_SHEET_NAME = 32
R_SHEET_FILE_NAME = 33
R_DESIGNATOR = 34
R_PARAMETER = 41
R_IMPL_LIST = 44
R_IMPLEMENTATION = 45

PIN_ELECTRICAL = {0: "input", 1: "io", 2: "output", 3: "open_collector", 4: "passive",
                  5: "hiz", 6: "open_emitter", 7: "power"}
POWER_STYLES = {0: "circle", 1: "arrow", 2: "bar", 3: "wave", 4: "power_ground", 5: "signal_ground",
                6: "earth", 7: "gost_arrow", 8: "gost_power_ground", 9: "gost_earth", 10: "gost_bar"}
PORT_IO = {0: "unspecified", 1: "output", 2: "input", 3: "bidirectional"}

Point = tuple[float, float]


@dataclass
class SchPin:
    designator: str
    name: str
    electrical: str
    hidden: bool
    hidden_net: str
    base: Point
    hotspot: Point
    owner_part: int


@dataclass
class SchComponent:
    index: int
    designator: str
    lib_ref: str
    design_item_id: str
    description: str
    comment: str
    footprint: str
    footprints: list[str]
    library: str
    part_id: int
    part_count: int
    location: Point
    orientation: int
    mirrored: bool
    unique_id: str
    parameters: dict[str, str]
    pins: list[SchPin] = field(default_factory=list)
    kind: int = 0  # 0 standard, 1 mechanical, 2 graphical, 3 net tie (BOM), 4 net tie, 5 standard (no BOM), 6 jumper

    @property
    def is_annotated(self) -> bool:
        return bool(self.designator) and "?" not in self.designator

    def to_dict(self, *, include_pins: bool = False) -> dict:
        d = {
            "designator": self.designator,
            "comment": self.comment,
            "lib_ref": self.lib_ref,
            "design_item_id": self.design_item_id,
            "description": self.description,
            "footprint": self.footprint,
            "library": self.library,
            "part": self.part_id,
            "part_count": self.part_count,
            "location_mil": list(self.location),
            "orientation_deg": self.orientation * 90,
            "mirrored": self.mirrored,
            "unique_id": self.unique_id,
            "parameters": self.parameters,
            "pin_count": len(self.pins),
        }
        if include_pins:
            d["pins"] = [
                {"number": p.designator, "name": p.name, "type": p.electrical,
                 "hotspot_mil": list(p.hotspot), "hidden": p.hidden}
                for p in self.pins
            ]
        return d


@dataclass
class SchLabel:
    kind: str  # net_label | power_port | port | sheet_entry
    text: str
    points: list[Point]
    extra: dict = field(default_factory=dict)


@dataclass
class SchSheetSymbol:
    index: int
    name: str
    file_name: str
    location: Point
    size: Point
    entries: list[SchLabel] = field(default_factory=list)
    unique_id: str = ""


@dataclass
class SchDocument:
    path: Path
    header: dict[str, str]
    components: list[SchComponent]
    wires: list[list[Point]]
    buses: list[list[Point]]
    junctions: list[Point]
    labels: list[SchLabel]
    sheet_symbols: list[SchSheetSymbol]
    no_erc: list[Point]
    sheet_parameters: dict[str, str]
    record_counts: dict[int, int]

    def labels_of(self, kind: str) -> list[SchLabel]:
        return [lbl for lbl in self.labels if lbl.kind == kind]

    def summary(self) -> dict:
        return {
            "path": str(self.path),
            "components": len(self.components),
            "pins": sum(len(c.pins) for c in self.components),
            "wires": len(self.wires),
            "buses": len(self.buses),
            "net_labels": len(self.labels_of("net_label")),
            "power_ports": len(self.labels_of("power_port")),
            "ports": len(self.labels_of("port")),
            "sheet_symbols": [
                {"name": s.name, "file": s.file_name, "entries": [e.text for e in s.entries]}
                for s in self.sheet_symbols
            ],
            "sheet_parameters": self.sheet_parameters,
        }


def _loc(props: dict[str, str], prefix: str = "LOCATION") -> Point:
    return (
        sch_coord_to_mil(props.get(f"{prefix}.X"), props.get(f"{prefix}.X_FRAC")),
        sch_coord_to_mil(props.get(f"{prefix}.Y"), props.get(f"{prefix}.Y_FRAC")),
    )


def _vertices(props: dict[str, str]) -> list[Point]:
    count = parse_int(props.get("LOCATIONCOUNT"))
    pts = []
    for i in range(1, count + 1):
        pts.append((sch_coord_to_mil(props.get(f"X{i}"), props.get(f"X{i}_FRAC")),
                    sch_coord_to_mil(props.get(f"Y{i}"), props.get(f"Y{i}_FRAC"))))
    return pts


def _pin(props: dict[str, str]) -> SchPin:
    base = _loc(props)
    length = sch_coord_to_mil(props.get("PINLENGTH"), props.get("PINLENGTH_FRAC"))
    cong = parse_int(props.get("PINCONGLOMERATE"))
    orient = cong & 0x03
    dx, dy = {0: (length, 0.0), 1: (0.0, length), 2: (-length, 0.0), 3: (0.0, -length)}[orient]
    return SchPin(
        designator=props.get("DESIGNATOR", ""),
        name=props.get("NAME", ""),
        electrical=PIN_ELECTRICAL.get(parse_int(props.get("ELECTRICAL")), "unknown"),
        hidden=bool(cong & 0x04),
        hidden_net=props.get("HIDDENNETNAME", ""),
        base=base,
        hotspot=(round(base[0] + dx, 4), round(base[1] + dy, 4)),
        owner_part=parse_int(props.get("OWNERPARTID"), -1),
    )


def read_records(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    with olefile.OleFileIO(str(path)) as ole:
        raw = ole.openstream("FileHeader").read()
    recs = list(text_records(raw))
    if not recs:
        raise ValueError(f"{path} has no schematic records")
    return recs[0], recs[1:]


def read_schdoc(path: Path) -> SchDocument:
    path = Path(path)
    stat = path.stat()
    return _read_schdoc_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=64)
def _read_schdoc_cached(path_str: str, _mtime: int, _size: int) -> SchDocument:
    return _parse_schdoc(Path(path_str))


def _parse_schdoc(path: Path) -> SchDocument:
    header, recs = read_records(path)

    counts: dict[int, int] = {}
    children: dict[int, list[int]] = {}
    for i, r in enumerate(recs):
        rid = parse_int(r.get("RECORD"), -1)
        counts[rid] = counts.get(rid, 0) + 1
        owner = parse_int(r.get("OWNERINDEX"), -1)
        if owner >= 0:
            children.setdefault(owner, []).append(i)

    def rid_of(i: int) -> int:
        return parse_int(recs[i].get("RECORD"), -1)

    components: list[SchComponent] = []
    wires: list[list[Point]] = []
    buses: list[list[Point]] = []
    junctions: list[Point] = []
    labels: list[SchLabel] = []
    sheet_symbols: list[SchSheetSymbol] = []
    no_erc: list[Point] = []
    sheet_parameters: dict[str, str] = {}

    for i, r in enumerate(recs):
        rid = rid_of(i)
        if rid == R_COMPONENT:
            components.append(_component(i, r, recs, children, rid_of))
        elif rid == R_WIRE:
            wires.append(_vertices(r))
        elif rid == R_BUS:
            buses.append(_vertices(r))
        elif rid == R_JUNCTION:
            junctions.append(_loc(r))
        elif rid == R_NET_LABEL:
            labels.append(SchLabel("net_label", r.get("TEXT", ""), [_loc(r)]))
        elif rid == R_POWER_PORT:
            style = parse_int(r.get("STYLE"))
            labels.append(SchLabel("power_port", r.get("TEXT", ""), [_loc(r)],
                                   {"style": POWER_STYLES.get(style, str(style))}))
        elif rid == R_PORT:
            x, y = _loc(r)
            width = sch_coord_to_mil(r.get("WIDTH"), r.get("WIDTH_FRAC"))
            style = parse_int(r.get("STYLE"))
            end = (x, y + width) if style >= 4 else (x + width, y)
            labels.append(SchLabel("port", r.get("NAME", ""), [(x, y), end],
                                   {"io": PORT_IO.get(parse_int(r.get("IOTYPE")), "unspecified"),
                                    "harness": r.get("HARNESSTYPE", "")}))
        elif rid == R_NO_ERC:
            no_erc.append(_loc(r))
        elif rid == R_SHEET_SYMBOL:
            sheet_symbols.append(_sheet_symbol(i, r, recs, children, rid_of))
        elif rid == R_PARAMETER and parse_int(r.get("OWNERINDEX"), -1) < 0:
            name = r.get("NAME", "")
            if name:
                sheet_parameters[name] = r.get("TEXT", "")

    return SchDocument(path, header, components, wires, buses, junctions, labels, sheet_symbols,
                       no_erc, sheet_parameters, counts)


def _resolve_indirect(value: str, params: dict[str, str]) -> str:
    # "=Value" means "show the value of parameter Value".
    if value.startswith("=") and len(value) > 1:
        key = value[1:].strip()
        for k, v in params.items():
            if k.lower() == key.lower():
                return v
    return value


def _component(i, r, recs, children, rid_of) -> SchComponent:
    part_id = parse_int(r.get("CURRENTPARTID"), 1)
    display_mode = parse_int(r.get("DISPLAYMODE"), 0)
    designator = ""
    params: dict[str, str] = {}
    comment_raw = ""
    footprints: list[str] = []
    current_fp = ""
    pins: list[SchPin] = []

    for ci in children.get(i, []):
        c = recs[ci]
        cid = rid_of(ci)
        if cid == R_DESIGNATOR:
            designator = c.get("TEXT", "")
        elif cid == R_PARAMETER:
            name = c.get("NAME", "")
            if name.lower() == "comment":
                comment_raw = c.get("TEXT", "")
            elif name:
                params[name] = c.get("TEXT", "")
        elif cid == R_PIN:
            owner_part = parse_int(c.get("OWNERPARTID"), -1)
            mode = parse_int(c.get("OWNERPARTDISPLAYMODE"), 0)
            if owner_part in (part_id, 0, -1) and mode == display_mode:
                pins.append(_pin(c))
        elif cid == R_IMPL_LIST:
            for ii in children.get(ci, []):
                impl = recs[ii]
                if rid_of(ii) == R_IMPLEMENTATION and impl.get("MODELTYPE", "").upper() == "PCBLIB":
                    name = impl.get("MODELNAME", "")
                    if name:
                        footprints.append(name)
                        if parse_bool(impl.get("ISCURRENT")):
                            current_fp = name

    comment = _resolve_indirect(comment_raw, params)
    return SchComponent(
        index=i,
        designator=designator,
        lib_ref=r.get("LIBREFERENCE", ""),
        design_item_id=r.get("DESIGNITEMID", ""),
        description=r.get("COMPONENTDESCRIPTION", ""),
        comment=comment,
        footprint=current_fp or (footprints[0] if footprints else ""),
        footprints=footprints,
        library=r.get("SOURCELIBRARYNAME", "") or r.get("LIBRARYPATH", ""),
        part_id=part_id,
        part_count=max(1, parse_int(r.get("PARTCOUNT"), 2) - 1),
        location=_loc(r),
        orientation=parse_int(r.get("ORIENTATION")),
        mirrored=parse_bool(r.get("ISMIRRORED")),
        unique_id=r.get("UNIQUEID", ""),
        parameters=params,
        pins=pins,
        kind=parse_int(r.get("COMPONENTKIND")),
    )


def _sheet_symbol(i, r, recs, children, rid_of) -> SchSheetSymbol:
    x, y = _loc(r)
    w = sch_coord_to_mil(r.get("XSIZE"), r.get("XSIZE_FRAC"))
    h = sch_coord_to_mil(r.get("YSIZE"), r.get("YSIZE_FRAC"))
    sym = SchSheetSymbol(i, "", "", (x, y), (w, h), unique_id=r.get("UNIQUEID", ""))
    for ci in children.get(i, []):
        c = recs[ci]
        cid = rid_of(ci)
        if cid == R_SHEET_NAME:
            sym.name = c.get("TEXT", "")
        elif cid == R_SHEET_FILE_NAME:
            sym.file_name = c.get("TEXT", "")
        elif cid == R_SHEET_ENTRY:
            # DISTANCEFROMTOP is in units of 100 mil (with a _FRAC1 refinement).
            dist = parse_int(c.get("DISTANCEFROMTOP")) * 100.0 + parse_int(c.get("DISTANCEFROMTOP_FRAC1")) / 10_000.0
            side = parse_int(c.get("SIDE"))
            if side == 0:
                pt = (x, y - dist)
            elif side == 1:
                pt = (x + w, y - dist)
            elif side == 2:
                pt = (x + dist, y)
            else:
                pt = (x + dist, y - h)
            sym.entries.append(SchLabel("sheet_entry", c.get("NAME", ""), [pt],
                                        {"io": PORT_IO.get(parse_int(c.get("IOTYPE")), "unspecified")}))
    return sym
