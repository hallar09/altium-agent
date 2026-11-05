# SPDX-License-Identifier: Apache-2.0
"""AI-driven placement and routing sessions (no Altium needed until import).

The agent makes every placement and routing decision; these tools carry
each one out on the saved board, check it at once and draw it. State lives
in a session file, so a session survives a new chat and every action can be
undone. place_session_export makes the plan place_apply writes;
route_session_export makes the job route_import_ses writes.
"""

from __future__ import annotations

from typing import Any

from .house_style import _offline


def register_interactive_tools(mcp) -> None:
    from ..interactive import placing as P
    from ..interactive import routing as R

    # ------------------------------------------------------------ placement

    @mcp.tool()
    @_offline
    def place_session_start(project: str | None = None, pcb: str | None = None, name: str | None = None,
                            from_plan: str | None = None) -> dict[str, Any]:
        """Open a placement session on the saved board: you place every part.

        Returns the session file (pass it to every place_session_ tool), the
        board's outline, and every part with its size, pin and net counts
        and where it is now (largest first), plus the parts still off the
        board. Positions are mm from the board origin. Start from a plan
        (from_plan) to refine one an engine made.
        """
        return P.start(project, pcb, name, from_plan)

    @mcp.tool()
    @_offline
    def place_session_part(session: str, ref: str) -> dict[str, Any]:
        """What a part connects to, and where those parts are now.

        Its pins (net, position on the board), the parts it shares signal
        nets with, nearest first with the distance between the nearest pins,
        its power and ground nets, and for a decoupling cap the IC supply
        pin it serves. Read this before placing a part next to its partners.
        """
        return P.part(session, ref)

    @mcp.tool()
    @_offline
    def place_session_move(session: str, moves: list[dict], force: bool = False) -> dict[str, Any]:
        """Place or move parts, and see at once what that did.

        Each move: {ref, x_mm, y_mm} (absolute) or {ref, dx_mm, dy_mm}
        (relative), optional rotation (degrees) and side (top/bottom). Move a
        part together with its satellites in one call. Returns the findings on
        the parts moved (overlap, off the board, near the edge, a decap far
        from its supply pin), the wirelength of their nets before and after,
        and the board's totals. Locked parts move only with force=true.
        """
        return P.move(session, moves, force)

    @mcp.tool()
    @_offline
    def place_session_check(session: str, decap_max_mm: float = 3.0) -> dict[str, Any]:
        """Every placement finding on the board, and its figures (wirelength,
        decap distances, parts on the bottom). The same rules as place_check."""
        return P.check(session, decap_max_mm)

    @mcp.tool()
    @_offline
    def place_session_view(session: str, highlight: list[str] | None = None, side: str = "top",
                           ratsnest: bool = True) -> dict[str, Any]:
        """Draw the board as the session has it: a PNG to Read (and its SVG).

        Findings are numbered markers (listed in the reply), ``highlight``
        parts are ringed, ratsnest lines show what each part connects to.
        Look after each group of moves: the picture catches what the numbers miss.
        """
        return P.view(session, highlight, side, ratsnest)

    @mcp.tool()
    @_offline
    def place_session_undo(session: str, steps: int = 1) -> dict[str, Any]:
        """Take back the last moves (one per call to place_session_move)."""
        return P.undo(session, steps)

    @mcp.tool()
    @_offline
    def place_session_export(session: str, name: str | None = None) -> dict[str, Any]:
        """The session as a placement plan: place_check checks it, place_apply
        writes it to Altium (on your copy of the project)."""
        return P.export(session, name)

    # ------------------------------------------------------------ routing

    @mcp.tool()
    @_offline
    def route_session_start(project: str | None = None, pcb: str | None = None,
                            name: str | None = None) -> dict[str, Any]:
        """Open a routing session on the saved board: you route every net.

        Returns the session file (pass it to every route_session_ tool), the
        board's rules (clearance, widths, via), layers, the nets carried by
        pours and planes, every net by tier (pair, critical, power, signal,
        poured) and how many connections are open. Choose the rules per class
        first (the routing skill's references/rules.md), then route.
        """
        return R.start(project, pcb, name)

    @mcp.tool()
    @_offline
    def route_session_route(session: str, nets: list[str], width_mm: float | None = None,
                            via_diameter_mm: float | None = None,
                            via_hole_mm: float | None = None) -> dict[str, Any]:
        """Route these nets with the in-house engine; everything else stays.

        You choose the nets and their order (critical first, a few at a time)
        and the width and via for them; the engine lays the copper to the
        board's clearances. Returns what it laid, which of the nets are done or
        still open, and the clearance violations on them. Rip up and route
        again (other order, other width) when a result is poor.
        """
        return R.route(session, nets, width_mm, via_diameter_mm, via_hole_mm)

    @mcp.tool()
    @_offline
    def route_session_draw(session: str, net: str, points: list[dict], width_mm: float | None = None,
                           via_diameter_mm: float | None = None,
                           via_hole_mm: float | None = None,
                           via_span: str = "through") -> dict[str, Any]:
        """Draw a net's path yourself: points {x_mm, y_mm, layer} in order.

        For the nets whose path you want to decide (a differential pair, an RF
        feed, a sense line, a short power run, a BGA escape). Layers:
        TopLayer, MidLayer1.., BottomLayer. To change layer, give the via's
        point twice, once on each layer. Returns what was laid and any
        clearance it breaks.

        BLIND AND BURIED VIAS: ``via_span="span"`` makes every via in the path
        go from exactly the layer it leaves to the layer it reaches (TopLayer
        to MidLayer2 is a blind L1-L3 via on an 8-layer board; MidLayer2 to
        MidLayer5 a buried one). One via can take its own span: put
        ``via_low_layer`` and ``via_high_layer`` on the second point of that
        layer change. A spanned via blocks and connects only the layers it
        passes, in the checks and on import (route_session_export,
        route_import_ses place it with its layers). The reply names any span
        with no drill pair yet: pcb_add_drill_pair, save, before importing.
        """
        return R.draw(session, net, points, width_mm, via_diameter_mm, via_hole_mm, via_span)

    @mcp.tool()
    @_offline
    def route_session_fanout(session: str, nets: list[str] | None = None, stitch_spacing_mm: float | None = None,
                             via_diameter_mm: float | None = None, via_hole_mm: float | None = None,
                             stub_width_mm: float | None = None) -> dict[str, Any]:
        """Power and ground pads to their planes, with short vias.

        Do it before the bulk of the signals. With stitch_spacing_mm (after
        the signals): stitching vias between the net's pours on different
        layers instead. Pads it cannot reach are listed, never forced.
        """
        return R.fanout(session, nets, stitch_spacing_mm, via_diameter_mm, via_hole_mm, stub_width_mm)

    @mcp.tool()
    @_offline
    def route_session_check(session: str, nets: list[str] | None = None) -> dict[str, Any]:
        """What is still open (by the copper, pours and planes counted) and what
        breaks a clearance (the in-house exact DRC), on the board or ``nets``."""
        return R.check(session, nets)

    @mcp.tool()
    @_offline
    def route_session_ripup(session: str, nets: list[str]) -> dict[str, Any]:
        """Remove everything this session laid for these nets."""
        return R.ripup(session, nets)

    @mcp.tool()
    @_offline
    def route_session_undo(session: str, steps: int = 1) -> dict[str, Any]:
        """Take back the last actions (route, draw, fanout); not across a rip-up."""
        return R.undo(session, steps)

    @mcp.tool()
    @_offline
    def route_session_view(session: str, nets: list[str] | None = None, side: str = "top") -> dict[str, Any]:
        """Draw the board with the session's copper: a PNG to Read. Open
        connections are ratsnest lines (of ``nets`` only, when given),
        violations numbered markers."""
        return R.view(session, nets, side)

    @mcp.tool()
    @_offline
    def route_session_export(session: str) -> dict[str, Any]:
        """The session's copper as a job: route_import_ses(job_id, expect_file)
        writes it to the board in Altium."""
        return R.export(session)
