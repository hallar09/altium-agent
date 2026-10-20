# SPDX-License-Identifier: MIT
# Copyright (c) 2026 altium-mcp-pro contributors
# From altium-mcp-pro formats/sch_netlist.py. Modified for Altium Agent (2026): see
# docs/PROVENANCE.md and THIRD_PARTY_NOTICES.md.
"""Offline schematic connectivity: derive nets from SchDoc geometry without Altium.

This mirrors Altium's compiler closely enough for review work: wires, junctions, pin hot-spots,
net labels, power ports, ports and sheet entries are connected geometrically per sheet, then
sheets are joined according to the project's net-identifier scope (HierarchyMode).

It is an approximation. Buses, harnesses, multi-channel (repeat) sheets and compiler directives are
not expanded. For sign-off use the live bridge (`project_compile` + `live_project_netlist`) or the
PcbDoc pad netlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .schdoc import SchDocument, read_schdoc

EPS = 0.05  # mil


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _key(p: tuple[float, float]) -> tuple[int, int]:
    return (round(p[0] * 10), round(p[1] * 10))


def _on_segment(p, a, b) -> bool:
    (px, py), (ax, ay), (bx, by) = p, a, b
    if min(ax, bx) - EPS <= px <= max(ax, bx) + EPS and min(ay, by) - EPS <= py <= max(ay, by) + EPS:
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        length = max(abs(bx - ax), abs(by - ay), 1e-9)
        return abs(cross) / length <= EPS
    return False


@dataclass
class NetMember:
    designator: str
    pin: str
    pin_name: str
    pin_type: str
    sheet: str


@dataclass
class Net:
    name: str
    members: list[NetMember] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)
    sheets: set[str] = field(default_factory=set)
    has_power_port: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pins": [f"{m.designator}-{m.pin}" for m in self.members],
            "pin_details": [
                {"designator": m.designator, "pin": m.pin, "name": m.pin_name, "type": m.pin_type}
                for m in self.members
            ],
            "aliases": sorted(self.aliases - {self.name}),
            "sheets": sorted(self.sheets),
        }


@dataclass
class Netlist:
    mode: str
    nets: list[Net]
    unconnected_pins: list[NetMember]
    floating_labels: list[dict]
    warnings: list[str]

    def by_name(self) -> dict[str, Net]:
        return {n.name: n for n in self.nets}

    def to_dict(self, *, include_details: bool = False) -> dict:
        nets = []
        for n in self.nets:
            d = n.to_dict()
            if not include_details:
                d.pop("pin_details")
            nets.append(d)
        return {
            "scope_mode": self.mode,
            "net_count": len(self.nets),
            "nets": nets,
            "unconnected_pins": [
                {"designator": m.designator, "pin": m.pin, "name": m.pin_name, "type": m.pin_type, "sheet": m.sheet}
                for m in self.unconnected_pins
            ],
            "floating_labels": self.floating_labels,
            "warnings": self.warnings,
            "method": "offline-geometry (approximate; verify with live compile for sign-off)",
        }


HIERARCHY_MODES = {"0": "automatic", "1": "flat", "2": "global", "3": "hierarchical", "4": "strict_hierarchical"}


def _sheet_groups(doc: SchDocument, uf: UnionFind, sheet_id: int):
    """Union geometric connectivity on one sheet. Returns list of (node, payload) attachments."""
    # Segments are bucketed on a coarse grid so point-on-wire tests stay near-linear.
    cell = 500.0
    grid: dict[tuple[int, int], list[tuple]] = {}
    for wire in doc.wires:
        for a, b in zip(wire, wire[1:]):
            uf.union((sheet_id, _key(a)), (sheet_id, _key(b)))
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            for gx in range(int((x0 - EPS) // cell), int((x1 + EPS) // cell) + 1):
                for gy in range(int((y0 - EPS) // cell), int((y1 + EPS) // cell) + 1):
                    grid.setdefault((gx, gy), []).append((a, b))

    def attach(point) -> tuple:
        node = (sheet_id, _key(point))
        uf.find(node)
        for a, b in grid.get((int(point[0] // cell), int(point[1] // cell)), ()):
            if _on_segment(point, a, b):
                uf.union(node, (sheet_id, _key(a)))
        return node

    # Wire endpoints landing on another wire's interior form implicit junctions.
    for wire in doc.wires:
        if wire:
            attach(wire[0])
            attach(wire[-1])
    for j in doc.junctions:
        attach(j)

    attachments: list[tuple[tuple, str, object]] = []
    for comp in doc.components:
        for pin in comp.pins:
            attachments.append((attach(pin.hotspot), "pin", (comp, pin)))
    for lbl in doc.labels:
        nodes = [attach(p) for p in lbl.points]
        if lbl.kind == "port" and len(nodes) == 2:
            uf.union(nodes[0], nodes[1])
        attachments.append((nodes[0], lbl.kind, lbl))
    for sym in doc.sheet_symbols:
        for entry in sym.entries:
            attachments.append((attach(entry.points[0]), "sheet_entry", (sym, entry)))
    return attachments


def _find_sheet(name: str, search_dirs: list[Path]) -> Path | None:
    for d in search_dirs:
        if not d.is_dir():
            continue
        for hit in d.rglob(name):
            if hit.is_file():
                return hit
    return None


def build_netlist(sheet_paths: list[Path], hierarchy_mode: str = "0",
                  search_dirs: list[Path] | None = None, power_names_first: bool = False) -> Netlist:
    """Build nets for the given sheets.

    ``search_dirs`` is where to look for child sheets referenced by sheet symbols but not listed
    in the project (device sheets, sheets outside the project folder)."""
    docs: list[SchDocument] = []
    warnings: list[str] = []
    for p in sheet_paths:
        try:
            docs.append(read_schdoc(p))
        except Exception as exc:  # noqa: BLE001 - report and continue with the other sheets
            warnings.append(f"could not read {p}: {exc}")

    loaded = {d.path.name.lower() for d in docs}
    pending = True
    while pending and search_dirs:
        pending = False
        for d in list(docs):
            for sym in d.sheet_symbols:
                child = Path(sym.file_name.replace("\\", "/")).name
                if not child or child.lower() in loaded:
                    continue
                loaded.add(child.lower())
                found = _find_sheet(child, search_dirs)
                if found is None:
                    continue
                try:
                    docs.append(read_schdoc(found))
                    warnings.append(f"child sheet '{child}' is not in the project; loaded from {found}")
                    pending = True
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"could not read {found}: {exc}")
    refs: dict[str, int] = {}
    for d in docs:
        for sym in d.sheet_symbols:
            child = Path(sym.file_name.replace("\\", "/")).name.lower()
            refs[child] = refs.get(child, 0) + 1
    multi = sorted(n for n, c in refs.items() if c > 1)
    if multi or any(sym.name.lower().startswith("repeat(") for d in docs for sym in d.sheet_symbols):
        warnings.append("multi-channel / multi-instance sheets are not expanded offline (" + ", ".join(multi[:8]) +
                        "); channel designators and nets will differ from the compiled design - use the live "
                        "bridge (live_project_netlist) for this project")
    return netlist_from_docs(docs, hierarchy_mode, warnings, power_names_first)


def netlist_from_docs(docs: list[SchDocument], hierarchy_mode: str = "0",
                      warnings: list[str] | None = None, power_names_first: bool = False) -> Netlist:
    """Connectivity for already-parsed sheets (see build_netlist)."""
    warnings = list(warnings or [])
    mode = HIERARCHY_MODES.get(str(hierarchy_mode), "automatic")
    if mode == "automatic":
        has_entries = any(s.entries for d in docs for s in d.sheet_symbols)
        has_ports = any(d.labels_of("port") for d in docs)
        mode = "hierarchical" if has_entries else ("flat" if has_ports else "global")
    labels_global = mode == "global"
    ports_global = mode in ("flat", "global")
    power_global = mode != "strict_hierarchical"

    uf = UnionFind()
    all_attachments: list[tuple[int, tuple, str, object]] = []
    for sid, doc in enumerate(docs):
        for node, kind, payload in _sheet_groups(doc, uf, sid):
            all_attachments.append((sid, node, kind, payload))

    # Cross-sheet joins by name.
    name_anchor: dict[tuple, tuple] = {}

    def join(key: tuple, node: tuple) -> None:
        if key in name_anchor:
            uf.union(name_anchor[key], node)
        else:
            name_anchor[key] = node

    file_to_sheets: dict[str, list[int]] = {}
    for sid, d in enumerate(docs):
        file_to_sheets.setdefault(d.path.name.lower(), []).append(sid)

    for sid, node, kind, payload in all_attachments:
        if kind == "power_port":
            join(("power", payload.text) if power_global else ("power", sid, payload.text), node)
        elif kind == "net_label":
            join(("label", payload.text) if labels_global else ("label", sid, payload.text), node)
            # A net label also binds same-named ports/power ports on its own sheet.
            join(("local", sid, payload.text), node)
        elif kind == "port":
            if ports_global:
                join(("port", payload.text), node)
            else:
                join(("port", sid, payload.text), node)
        elif kind == "pin":
            comp, pin = payload
            if pin.hidden and pin.hidden_net:
                join(("power", pin.hidden_net) if power_global else ("power", sid, pin.hidden_net), node)

    # Bus-named objects on one sheet are tied together by bus wires; treat same-name as connected.
    for sid, node, kind, payload in all_attachments:
        text = payload[1].text if kind == "sheet_entry" else (payload.text if kind != "pin" else "")
        if text and expand_bus(text):
            join(("bus", sid, text), node)

    if mode in ("hierarchical", "strict_hierarchical"):
        for sid, node, kind, payload in all_attachments:
            if kind != "sheet_entry":
                continue
            sym, entry = payload
            child_name = Path(sym.file_name.replace("\\", "/")).name.lower()
            children = file_to_sheets.get(child_name, [])
            if not children:
                warnings.append(f"sheet symbol '{sym.name}' references missing sheet '{sym.file_name}'")
                continue
            for child in children:
                key = ("port", child, entry.text)
                if key in name_anchor:
                    uf.union(name_anchor[key], node)

    # Expand buses: every sheet a bus reaches shares that bus's member nets (D[7..0] -> D0..D7).
    if not labels_global:
        buses: dict[tuple, tuple[set[int], set[str]]] = {}
        for sid, node, kind, payload in all_attachments:
            text = payload[1].text if kind == "sheet_entry" else (payload.text if kind != "pin" else "")
            if text and expand_bus(text):
                sheets, names = buses.setdefault(uf.find(node), (set(), set()))
                sheets.add(sid)
                names.add(text)
        for sheets, names in buses.values():
            for name in names:
                for bit in expand_bus(name):
                    anchors = [name_anchor[("label", s, bit)] for s in sheets if ("label", s, bit) in name_anchor]
                    for a in anchors[1:]:
                        uf.union(anchors[0], a)

    # Net naming: Altium prefers net labels over power ports unless PowerPortNamesTakePriority is set.
    if power_names_first:
        name_priority = {"power_port": 3, "net_label": 2, "port": 1}
    else:
        name_priority = {"net_label": 3, "power_port": 2, "port": 1}

    # Group attachments by root.
    groups: dict[tuple, dict] = {}
    for sid, node, kind, payload in all_attachments:
        g = groups.setdefault(uf.find(node), {"pins": [], "names": {}, "sheets": set(), "power": False})
        g["sheets"].add(docs[sid].path.name)
        if kind == "pin":
            comp, pin = payload
            g["pins"].append(NetMember(comp.designator, pin.designator, pin.name, pin.electrical, docs[sid].path.name))
            if pin.hidden and pin.hidden_net:
                g["names"].setdefault(pin.hidden_net, 0)
                g["names"][pin.hidden_net] = max(g["names"][pin.hidden_net], name_priority["power_port"])
        elif kind == "sheet_entry":
            if not expand_bus(payload[1].text):
                g["names"].setdefault(payload[1].text, 0)
        elif expand_bus(payload.text):
            continue  # bus names never name a net
        else:
            priority = name_priority[kind]
            g["names"][payload.text] = max(g["names"].get(payload.text, 0), priority)
            if kind == "power_port":
                g["power"] = True

    nets: list[Net] = []
    unconnected: list[NetMember] = []
    floating: list[dict] = []
    used_names: set[str] = set()
    for root, g in groups.items():
        pins = g["pins"]
        names = {n: p for n, p in g["names"].items() if n}
        if not pins:
            for n in names:
                floating.append({"name": n, "sheets": sorted(g["sheets"])})
            continue
        if not names and len(pins) == 1:
            unconnected.append(pins[0])
            continue
        if names:
            best = max(names.values())
            name = sorted(n for n, p in names.items() if p == best)[0]
        else:
            first = sorted(pins, key=lambda m: (m.designator, _natural(m.pin)))[0]
            name = f"Net{first.designator}_{first.pin}"
        base, k = name, 1
        while name in used_names:
            k += 1
            name = f"{base}_{k}"
        used_names.add(name)
        pins.sort(key=lambda m: (_natural(m.designator), _natural(m.pin)))
        nets.append(Net(name, pins, set(names), g["sheets"], g["power"]))

    nets.sort(key=lambda n: _natural(n.name))
    return Netlist(mode, nets, unconnected, floating, list(dict.fromkeys(warnings)))


_BUS_RE = re.compile(r"^(.*?)\[(\d+)\.\.(\d+)\]$")


def expand_bus(name: str) -> list[str]:
    """``D[7..0]`` -> ['D7', ..., 'D0']; non-bus names -> []."""
    m = _BUS_RE.match(name.strip())
    if not m:
        return []
    base, a, b = m.group(1), int(m.group(2)), int(m.group(3))
    step = 1 if b >= a else -1
    if abs(b - a) > 1024:
        return []
    return [f"{base}{i}" for i in range(a, b + step, step)]


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
