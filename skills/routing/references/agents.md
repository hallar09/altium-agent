# Working within a usage limit: agents, replies and looking

Usage goes on three things: what each tool reply puts in the context, how
often the same thing is read again, and how many agents start cold. The
SODIMM job spent about 3M tokens across nine planners, several of which ran
out of context and handed back unfinished work. Most of that was avoidable.

## Agents cost more in total, not less

A subagent saves the LEAD's context, never the total. Each one starts cold
and pays to read its brief, the skill, the board and its harness before it
does anything. Split work only when all of these hold:

- the pieces are independent: no piece's result changes another's (two
  routing packets in bands that do not touch; not lane 1 next to lane 0);
- each piece is big enough to be worth a cold start (tens of nets, not three);
- the piece is offline: one writer to Altium, always the lead. Two agents
  driving the bridge collide, and Altium has one script engine.

Good splits: routing packets (`route_session_packets`), a fresh-eyes
reviewer at a gate, a datasheet or footprint check per part, applying a
finished plan. Bad splits: exploration ("read the board and tell me"),
anything that touches Altium, coupled routing, a reviewer per small change.

Before starting more than two agents, tell the user how many, for what and
roughly what it will cost, and wait for a yes.

## The packet workflow (big boards only)

1. In the lead's session: route the critical nets and the power fan-out.
2. `route_session_packets(session, count=2..4)`: bands across the board, each
   with the nets wholly inside it; nets that cross bands are SHARED.
3. Route the shared nets yourself, in the lead session, then run
   `route_session_packets` again so the packets start from that copper.
4. One planner per packet, with the reply's `brief` filled in. Its packet
   session accepts only its own nets, so planners cannot step on each other.
5. `route_session_merge(session, [packet sessions])`, then
   `route_session_check`. Fix where packets meet in the lead session.

Two to four packets of real work beat ten small ones.

## A brief that keeps a planner cheap

- At most 25 lines. Paths to files, never their contents pasted in.
- What it may read (its packet session, one brief file) and what it may not
  (other boards, earlier attempts, the whole project).
- The target as numbers, and when to stop: done, or 3 failed tries on a net,
  or context getting low (stop at a clean boundary with the files saved).
- The reply: at most 8-10 lines, numbers first. No prose report.
- Model: a mid-size model (Sonnet) for planning a packet, a small one
  (Haiku) for applying a plan or summarising a report; the lead keeps the
  decisions.
- If it comes back unfinished, send it the follow-up (SendMessage) rather
  than starting a new one; tell the user before starting another.

## Replies: summaries first

- `route_session_check` gives counts, violations by rule and the first few;
  `detail="full"` once when you need the whole list.
- `pcb_run_drc` gives the total, counts by rule and the first 10 copper
  problems; `analyze_drc_report` reads the whole report from disk.
- Route and draw replies carry 8 violation rows and the totals.

## Looking costs tokens too

A PNG costs about as much as a page of text. Look (`route_session_view`,
`pcb_render_svg`) at milestones: after the critical nets, after each packet
merge, before import, and whenever the numbers surprise you. Not after
every call.

## Do not read the same thing twice

- Keep the decisions in one short file (the brief or a decisions file) and
  re-read only that after a context reset, not the whole history.
- The session file is the state: `route_session_check` tells you where you
  are, so there is no need to re-dump the board.
- Batch: one `pcb_set_rules_enabled` with every name, one `pcb_place_vias`
  with every via, one `route_session_route` with 5-10 nets.
