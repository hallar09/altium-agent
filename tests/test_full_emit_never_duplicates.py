# SPDX-License-Identifier: Apache-2.0
"""A full emit onto a sheet that already exists redraws it, never doubles it.

Found in the MacroStick acceptance run: design_execute_plan ran a full emit
twice onto MacroStick__main.SchDoc and the sheet ended with all 52
designators twice (104 components), each copy laid out differently.
"""

from __future__ import annotations

from pathlib import Path

from altium_agent.design.canvas import SchematicCanvas, Sheet, SymbolInstance
from altium_agent.design.emitter import emit_canvas
from altium_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


class _Bridge:
    def __init__(self, fail_on: str = ""):
        self.sent: list[tuple] = []
        self.fail_on = fail_on

    def send_command(self, command, params=None, **kw):
        params = params or {}
        self.sent.append((command, params))
        if self.fail_on and params.get("object_type") == self.fail_on:
            raise RuntimeError("bridge said no")
        return {"success": True, "placed": 1}


def _canvas() -> SchematicCanvas:
    sym = SymbolModel(
        lib_path="LIB.SchLib", lib_ref="R",
        pins=[SymbolPin(designator="1", name="A", x=0, y=100, orientation=1,
                        length=100, electrical_type="passive")],
        body_bbox=SymbolBBox(x_min=-50, y_min=-50, x_max=50, y_max=50))
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    cv.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=1000, y=1000,
                                   rotation=0, sheet="main"))
    return cv


def _project_with_sheet(tmp_path: Path) -> Path:
    prj = tmp_path / "p.PrjPcb"
    prj.write_text("")
    from altium_agent.design.emitter import _sheet_path
    _sheet_path(prj, "main").write_bytes(b"existing")
    return prj


def _index(sent, command, object_type=None):
    for i, (c, p) in enumerate(sent):
        if c == command and (object_type is None or p.get("object_type") == object_type):
            return i
    return -1


def test_reused_sheet_is_cleared_before_parts_are_placed(tmp_path):
    b = _Bridge()
    emit_canvas(_canvas(), str(_project_with_sheet(tmp_path)), b)
    clear = _index(b.sent, "generic.delete_objects", "eSchComponent")
    place = _index(b.sent, "generic.place_sch_components_from_library")
    assert place != -1, "the emit placed nothing, so this test checked nothing"
    assert clear != -1, "a full emit onto an existing sheet must clear its parts"
    assert clear < place
    for t in ("eWire", "eNetLabel", "ePowerObject"):
        assert _index(b.sent, "generic.delete_objects", t) != -1


def test_new_sheet_is_not_cleared(tmp_path):
    b = _Bridge()
    emit_canvas(_canvas(), str(tmp_path / "p.PrjPcb"), b)
    assert _index(b.sent, "generic.delete_objects") == -1


def test_failed_clear_draws_nothing(tmp_path):
    b = _Bridge(fail_on="eSchComponent")
    r = emit_canvas(_canvas(), str(_project_with_sheet(tmp_path)), b)
    assert not r.ok
    assert any(f.code == "CLEAR_FAILED" for f in r.failures)
    assert not any("place" in c for c, _ in b.sent if c != "generic.delete_objects")


def test_execute_plan_refuses_a_project_that_does_not_exist(tmp_path):
    """A mistyped project path must not make a second project and sheet."""
    import asyncio
    import json as _json

    from mcp.server.fastmcp import FastMCP

    from altium_agent.tools.design import register_design_tools

    m = FastMCP("t")
    register_design_tools(m)
    missing = tmp_path / "nowhere" / "MacroStick.PrjPcb"
    out = asyncio.run(m.call_tool("design_execute_plan", {
        "plan_json": _json.dumps({"name": "x"}), "project_path": str(missing)}))
    text = _json.dumps([getattr(c, "text", str(c)) for c in (out[0] if isinstance(out, tuple) else out)])
    assert "does not exist" in text
    assert not missing.parent.exists()
