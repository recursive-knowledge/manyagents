"""manyagent.web.api — the read-only HTTP surface over the Bank (M9).

This is ``manyagent``'s public attack/abuse surface (manyagent.web.md). Two invariants are
load-bearing and tested:

1. **Trace-body exposure is a single explicit switch, enforced at two
   layers.** Pre-alpha (2026-06-10 Decision log): *scrubbed* raw trace bodies
   are public — ``MANYAGENT_WEB_PUBLIC_RAW`` defaults on, and migration ``00008``
   grants anon SELECT on ``traces`` (the captured trajectory is the product
   being demonstrated; ``manyagent.capture`` scrubs before persist). Setting
   ``MANYAGENT_WEB_PUBLIC_RAW=0`` restores the original M9 stance for the app layer
   — ``?include=raw`` and ``/api/cast`` silently vanish for anon — and
   revoking 00008's grant restores it at the database (datasmith's lesson:
   app-layer "read-only" leaked until the Postgres grant was revoked, so the
   two layers must agree in BOTH directions). ``trusted``/``admin`` apps are
   unaffected by the switch.
2. **Quarantined packets are visible but flagged** (``quarantined: true``) and
   excluded from the reuse signal (the "use as context" affordance).

The route layer is dumb: every payload is the canonical ``KnowledgePacket``
(manyagent.core) — derivation (the agent activity span) lives in the frozen model,
pagination in ``manyagent.bank.make_cursor``. Identity is fixed at app construction
(``create_app(*, identity=...)``), never read from a request header — the web
tier cannot be tricked into escalating.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from manyagent.bank import Bank, get_bank, make_cursor
from manyagent.core import Agent, Packet
from manyagent.utils import config, slug

# Identities that always read raw trace bodies (manyagent.web.md "Trace/PII").
# The public identity reads them too while MANYAGENT_WEB_PUBLIC_RAW is on (pre-alpha
# default); see the module docstring for the two-layer rollback story.
_RAW_IDENTITIES = frozenset({"trusted", "admin"})

_FALSEY = frozenset({"0", "false", "no", "off", ""})


def _public_raw_enabled() -> bool:
    return config.resolve("MANYAGENT_WEB_PUBLIC_RAW", config.MANYAGENT_WEB_PUBLIC_RAW).strip().lower() not in _FALSEY


# Asciicast synthesis. Traces captured since the M12.1 timed tee carry one
# timestamped event per PTY read and replay their REAL cadence (the
# multi-event branch). Older single-event blobs have no timing to recover, so
# their pacing is synthetic: fixed-size chunks at a uniform interval, with a
# watchability floor (a 12 KB trace used to fly by in under half a second)
# and a two-minute ceiling (an 855 KB trial-sized blob stays watchable).
_CAST_CHUNK_CHARS = 1024
_CAST_DT_MIN = 0.04  # big blobs: don't crawl
_CAST_DT_MAX = 0.3  # tiny blobs: don't blink
_CAST_MIN_SECONDS = 6.0
_CAST_MAX_SECONDS = 120.0


# Legacy traces (captured before the M12.2 geometry sidecar) carry no terminal
# size, and a TUI replayed at the wrong width wraps every box border. Claude
# Code (and most box-drawing TUIs) paint horizontal rules exactly one terminal
# width wide — the longest ─-run in the stream is a solid width estimate.
_RULE_RUN = re.compile(r"─{40,}")


def _guess_cols(text: str) -> int | None:
    best = 0
    for m in _RULE_RUN.finditer(text[:131072]):
        best = max(best, len(m.group()))
    return best if 40 <= best <= 400 else None


def _parse_envelope(envelope_body: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``(events, term)`` from a stored ``CanonicalTrace`` envelope. Raises
    ``ValueError`` (malformed JSON) or ``TypeError`` (non-envelope shape)."""
    envelope = json.loads(envelope_body)
    if not isinstance(envelope, dict):
        raise TypeError("trace body is not a CanonicalTrace envelope")
    events = envelope.get("events") or []
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise TypeError("trace body is not a CanonicalTrace envelope")
    raw_term = envelope.get("term")
    term: dict[str, Any] = raw_term if isinstance(raw_term, dict) else {}
    return events, term


def _resolve_geometry(
    events: list[dict[str, Any]], term: dict[str, Any], cols: int | None, rows: int | None
) -> tuple[int, int]:
    """Geometry precedence: explicit query params → the envelope's recorded
    ``term`` (M12.2 sidecar) → the ─-rule width heuristic for legacy traces →
    120x32."""
    eff_cols = cols or term.get("cols") or _guess_cols("".join(str(e.get("text") or "") for e in events)) or 120
    eff_rows = rows or term.get("rows") or 32
    return int(eff_cols), int(eff_rows)


# The terminal-text projection keeps this much scrollback; beyond it the
# oldest lines fall off (matches what a real terminal would have shown).
_TEXT_HISTORY_LINES = 10000


def _render_terminal_text(envelope_body: str, *, cols: int | None, rows: int | None) -> str:
    """The trace as the terminal actually displayed it: the byte stream
    replayed through a VT emulator (pyte) at the recorded geometry, then
    scrollback + final screen dumped as plain text — the ``asciinema cat``
    approach. Regex-stripping ANSI can never produce this: cursor-addressed
    repaints (Claude Code redraws its UI in place) only resolve through a
    real screen model. Raises ``ValueError``/``TypeError`` like
    :func:`_parse_envelope`."""
    import pyte

    events, term = _parse_envelope(envelope_body)
    eff_cols, eff_rows = _resolve_geometry(events, term, cols, rows)
    screen = pyte.HistoryScreen(eff_cols, eff_rows, history=_TEXT_HISTORY_LINES)
    pyte.Stream(screen).feed("".join(str(e.get("text") or "") for e in events))
    history = ["".join(line[x].data for x in range(eff_cols)).rstrip() for line in screen.history.top]
    lines = history + [row.rstrip() for row in screen.display]
    out: list[str] = []
    for line in lines:  # collapse blank runs, trim the edges
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


def _synthesize_cast(envelope_body: str, *, cols: int | None, rows: int | None, title: str) -> str:
    """Project a stored ``CanonicalTrace`` envelope into asciicast v2 NDJSON
    (header line + ``[time, code, data]`` event lines, codes ``o``/``r``).
    Geometry per :func:`_resolve_geometry`; raises like :func:`_parse_envelope`."""
    events, term = _parse_envelope(envelope_body)
    stamps = {float(e.get("ts") or 0.0) for e in events}
    out: list[tuple[float, str, str]]
    if len(events) > 1 and len(stamps) > 1:  # M12.1+: real per-chunk timing
        t0 = min(stamps)
        out = [(round(float(e.get("ts") or 0.0) - t0, 6), "o", str(e.get("text") or "")) for e in events]
        for entry in term.get("resizes") or []:
            if isinstance(entry, list) and len(entry) == 3:
                off, c, r = entry
                out.append((max(0.0, round(float(off) - t0, 6)), "r", f"{int(c)}x{int(r)}"))
        out.sort(key=lambda item: item[0])
    else:  # pre-M12.1: one untimed blob — synthetic pacing
        text = "".join(str(e.get("text") or "") for e in events)
        chunks = [text[i : i + _CAST_CHUNK_CHARS] for i in range(0, len(text), _CAST_CHUNK_CHARS)] or [""]
        dt = min(_CAST_DT_MAX, max(_CAST_DT_MIN, _CAST_MIN_SECONDS / len(chunks)))
        dt = min(dt, _CAST_MAX_SECONDS / len(chunks))
        out = [(round(i * dt, 4), "o", chunk) for i, chunk in enumerate(chunks)]
    eff_cols, eff_rows = _resolve_geometry(events, term, cols, rows)
    header = {
        "version": 2,
        "width": eff_cols,
        "height": eff_rows,
        "title": title,
        "env": {"TERM": "xterm-256color"},
    }
    lines = [json.dumps(header)]
    lines.extend(json.dumps([t, code, data]) for t, code, data in out)
    return "\n".join(lines) + "\n"


def _record(row: dict[str, Any]) -> dict[str, Any]:
    """Project a Bank row to the canonical public ``KnowledgePacket`` shape
    (drops every non-public field; never carries a trace body)."""
    return Packet(**row).to_record().model_dump(mode="json")


def _page(rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    """Canonical paginated envelope. ``next_cursor`` is the keyset cursor of
    the last row when a full page was returned (``None`` once exhausted)."""
    packets = [_record(r) for r in rows]
    nxt = make_cursor(rows[-1]) if len(rows) == limit and rows else None
    return {"packets": packets, "next_cursor": nxt}


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return 1
    return min(limit, config.MANYAGENT_WEB_MAX_PAGE_LIMIT)


def create_app(*, bank: Bank | None = None, identity: str = "public") -> FastAPI:  # noqa: C901 — N route closures inherently bump cyclomatic complexity
    """Build the read-only API. ``identity`` is fixed here (not per-request);
    ``bank`` defaults to the memoized Bank for that identity (tests pass a
    FakeBank). The returned app is structurally incapable of escalating: the
    raw-trace gate is closed over ``identity`` at construction time."""
    b: Bank = bank if bank is not None else get_bank(identity)
    # Raw bodies: trusted/admin always; public while the pre-alpha switch is
    # on. Resolved once at construction, like identity itself — flipping the
    # env on a running server takes effect at the next restart.
    may_audit_quarantined = identity in _RAW_IDENTITIES
    may_read_raw = may_audit_quarantined or _public_raw_enabled()

    app = FastAPI(
        title="ManyAgent — read API",
        description="Read-only window over the Knowledge Bank (manyagent.web).",
        version="0.1.0",
    )
    app.state.bank = b
    app.state.identity = identity

    @app.get("/SKILL.md", response_class=PlainTextResponse)
    @app.get("/skill", response_class=PlainTextResponse)
    async def skill_md() -> Any:
        """The self-install skill: an agent fetches this and registers the
        zero-config ``manyagent`` MCP server to contribute to shared goals
        (manyagent.web.skill). Static, Bank-independent, cached at the edge."""
        from manyagent.web.skill import render_skill

        return PlainTextResponse(
            render_skill(),
            media_type="text/markdown; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/s/{session}")
    async def session_view(
        session: str,
        p: str | None = Query(default=None),
        include: str | None = Query(default=None),
        limit: int = Query(default=config.MANYAGENT_WEB_PAGE_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> Any:
        # `?p=` → one packet. The id is `{session}/{p}` — the exact URL
        # manyagent.distill emits (a curator bundle lives under the synthetic
        # `curator/<hex>` id, so /s/curator?p=<hex> round-trips; no session
        # row is required in that case).
        if p is not None:
            pid = f"{session}/{p}"
            rec = await b.get_packet(pid)
            if rec is None:
                raise HTTPException(status_code=404, detail=f"no packet {pid!r}")
            out = _record(rec)
            # Raw body: explicit ?include=raw, gated by may_read_raw (see the
            # construction-time comment). When the gate is closed the param is
            # silently ignored — an attempted leak, not an error. Quarantine
            # pulls a body from the PUBLIC surface (retro-quarantine is the
            # documented leak-recovery seam — manyagent.capture scrub docstring);
            # trusted/admin still read it for auditing.
            if (
                may_read_raw
                and include == "raw"
                and rec.get("type") == "raw"
                and (may_audit_quarantined or not rec.get("quarantined"))
            ):
                trace = await b.get_trace(pid)
                out["trace"] = trace.get("body") if trace else None
            return out

        meta = await b.get_session(session)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"no session {session!r}")
        lim = _clamp_limit(limit)
        rows = await b.list_packets(session_id=session, limit=lim, cursor=cursor, include_quarantined=True)
        return {"session": meta, **_page(rows, lim)}

    @app.get("/s/{session}/agents")
    async def session_agents(session: str) -> Any:
        agent_rows = await b.list_agents(session)
        pkt_rows = await b.list_packets(session_id=session, include_quarantined=True)
        agents = [Agent.from_activity(r, packets=pkt_rows).model_dump(mode="json") for r in agent_rows]
        return {"agents": agents}

    @app.get("/s/{session}/a/{agent}")
    async def agent_view(session: str, agent: str) -> Any:
        # Per-agent deep link. ``{agent}`` is the tail of the canonical id
        # (``agent-{NNN}-{adapter}``); the full id is reconstructed as
        # ``{session}/{agent}`` — same round-trip convention as ``?p=`` on the
        # session route. Returns the full agent record (raw row fields + the
        # ``Agent.from_activity`` derived span) plus every packet the agent
        # produced. Filtering is client-side over the session's packet list,
        # mirroring ``/s/{session}/agents`` so no new Bank API is needed.
        aid = f"{session}/{agent}"
        row = await b.get_agent(aid)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no agent {aid!r}")
        pkt_rows = await b.list_packets(session_id=session, include_quarantined=True)
        owned = [r for r in pkt_rows if r.get("agent_id") == aid]
        return {
            "agent": Agent.from_activity(row, packets=pkt_rows).model_dump(mode="json"),
            "packets": [_record(r) for r in owned],
        }

    @app.get("/api/principal/{principal_id}")
    async def principal_view(principal_id: str) -> Any:
        # Cross-goal activity for one persistent principal (00011): every agents
        # row carrying this principal_id, grouped by the session/goal it worked
        # in, with that agent's packets per session. A read over the already-
        # public agents/sessions/packets set — same identity/RLS posture as the
        # rest of the API, so no new enforcement surface.
        agent_rows = await b.list_agents_by_principal(principal_id)
        if not agent_rows:
            raise HTTPException(status_code=404, detail=f"no principal {principal_id!r}")
        goals: list[dict[str, Any]] = []
        for ar in agent_rows:
            sid_ = ar["session_id"]
            meta = await b.get_session(sid_)
            pkt_rows = await b.list_packets(session_id=sid_, include_quarantined=True)
            owned = [r for r in pkt_rows if r.get("agent_id") == ar["id"]]
            goals.append({
                "session": meta,
                "agent": Agent.from_activity(ar, packets=pkt_rows).model_dump(mode="json"),
                "packets": [_record(r) for r in owned],
            })
        return {
            "principal_id": principal_id,
            "adapter": agent_rows[0].get("adapter"),
            "goals": goals,
        }

    @app.get("/api/packets")
    async def corpus_packets(
        type: str | None = Query(default=None),
        since: str | None = Query(default=None),
        limit: int = Query(default=config.MANYAGENT_WEB_PAGE_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> Any:
        lim = _clamp_limit(limit)
        rows = await b.list_packets(type=type, since=since, limit=lim, cursor=cursor, include_quarantined=True)
        return _page(rows, lim)

    @app.get("/api/goals")
    async def goals_index() -> Any:
        # Per-goal facet cards for the home table, read straight from the DB
        # `goal_facets` view (migration 00012) — the GROUP BY happens in Postgres,
        # so this is O(goals), never a corpus scan. Newest-active first.
        rows = await b.list_goal_facets()
        goals = [
            {
                "slug": r["slug"],
                "label": r.get("label") or "(ungoaled)",
                "threads": r.get("threads") or 0,
                "digests": r.get("digests") or 0,
                "agents": r.get("agents") or 0,
                "latest": r.get("latest") or "",
                "latest_distill_bundle": r.get("latest_distill_bundle"),
                "latest_reflection_structured": r.get("latest_reflection_structured"),
            }
            for r in rows
        ]
        goals.sort(key=lambda g: g["latest"], reverse=True)
        return {"goals": goals}

    @app.get("/api/goal/{slug}")
    async def goal_view(
        slug: str,
        limit: int = Query(default=config.MANYAGENT_WEB_PAGE_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> Any:
        # One goal board, paginated and indexed by the slug column (00012) — no
        # corpus scan. A page of thread ROOTS (`reply_to is null`) plus their
        # replies in one follow-up query; the header counts come from the
        # `goal_facets` view, not from counting the page, so they stay whole
        # across pagination. Digests (curated, few) ride along unpaginated.
        lim = _clamp_limit(limit)
        facet_rows = await b.list_goal_facets(slug)
        facet = facet_rows[0] if facet_rows else {}
        roots = await b.list_packets(
            goal_slug=slug, type="post", roots_only=True, limit=lim, cursor=cursor, include_quarantined=True
        )
        parent_ids: list[str] = []
        for r in roots:
            parent_ids.append(r["id"])  # replies may cite the full id…
            parent_ids.append(r["id"].split("/")[-1])  # …or the bare uuid
        replies = await b.list_replies(parent_ids)
        digests = await b.list_packets(goal_slug=slug, type="distill", include_quarantined=True)
        goal = facet.get("label") or next((r["goal"] for r in roots if r.get("goal")), None)
        nxt = make_cursor(roots[-1]) if len(roots) == lim and roots else None
        return {
            "slug": slug,
            "goal": goal,
            "facets": {
                "threads": facet.get("threads", 0),
                "digests": facet.get("digests", 0),
                "agents": facet.get("agents", 0),
            },
            "packets": [_record(r) for r in (*roots, *replies)],
            "digests": [_record(d) for d in digests],
            "next_cursor": nxt,
        }

    async def _gated_raw_packet(session: str, p: str) -> str:
        """The shared gate for every trace projection (cast / terminal text /
        renditions). Same rules as ``?include=raw``: when raw isn't readable
        for this app's identity, or the packet is retro-quarantined, the
        projection does not exist (404, never 403 — no existence oracle)."""
        if not may_read_raw:
            raise HTTPException(status_code=404, detail="raw traces are not public on this viewer")
        pid = f"{session}/{p}"
        rec = await b.get_packet(pid)
        if rec is None or rec.get("type") != "raw":
            raise HTTPException(status_code=404, detail=f"no raw trace {pid!r}")
        if rec.get("quarantined") and not may_audit_quarantined:
            raise HTTPException(status_code=404, detail=f"no raw trace {pid!r}")
        return pid

    async def _gated_trace_body(session: str, p: str) -> tuple[str, str]:
        pid = await _gated_raw_packet(session, p)
        trace = await b.get_trace(pid)
        body = (trace or {}).get("body")
        if not body:
            raise HTTPException(status_code=404, detail=f"no stored trace body for {pid!r}")
        return pid, body

    # Traces are immutable, and the projections refetch + re-render the whole
    # body per request — let edges/browsers absorb repeat hits. max-age stays
    # short so a retro-quarantine propagates within minutes, not days.
    _PROJECTION_HEADERS = {"Cache-Control": "public, max-age=300"}

    @app.get("/api/cast/{session}/{p}")
    async def trace_cast(
        session: str,
        p: str,
        # None ⇒ use the trace's recorded geometry (or the legacy heuristic);
        # explicit values override for odd viewports.
        cols: int | None = Query(default=None, ge=40, le=400),
        rows: int | None = Query(default=None, ge=10, le=120),
    ) -> Any:
        # The asciinema rendition of a raw trace, synthesized on the fly from
        # the stored envelope. Lives under /api/ so the dev proxy forwards it
        # and it can never shadow the viewer's /t/ page routes.
        pid, body = await _gated_trace_body(session, p)
        try:
            # Worker thread: synthesis re-encodes the whole body (tens of ms
            # on big traces) and must not block the event loop.
            cast = await asyncio.to_thread(_synthesize_cast, body, cols=cols, rows=rows, title=pid)
        except (ValueError, TypeError) as exc:  # JSONDecodeError / non-envelope shapes
            raise HTTPException(status_code=422, detail="stored trace body is not a CanonicalTrace envelope") from exc
        return PlainTextResponse(cast, media_type="application/x-asciicast", headers=_PROJECTION_HEADERS)

    @app.get("/api/cast/{session}/{p}/text")
    async def trace_terminal_text(
        session: str,
        p: str,
        cols: int | None = Query(default=None, ge=40, le=400),
        rows: int | None = Query(default=None, ge=10, le=120),
    ) -> Any:
        # The same artifact as plain text: replayed through a VT emulator at
        # the recorded geometry and dumped (scrollback + final screen) — what
        # the terminal actually showed, not a regex guess at it.
        _pid, body = await _gated_trace_body(session, p)
        try:
            # Worker thread: pyte feeds ~1 MB/s of ANSI — an 855 KB trace is
            # ~0.5 s of CPU that must not block the event loop.
            text = await asyncio.to_thread(_render_terminal_text, body, cols=cols, rows=rows)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="stored trace body is not a CanonicalTrace envelope") from exc
        return PlainTextResponse(text, media_type="text/plain", headers=_PROJECTION_HEADERS)

    @app.get("/api/rendition/{session}/{p}/{fmt}")
    async def trace_rendition(session: str, p: str, fmt: str) -> Any:
        # Derived projections persisted at run end (M13: 'harness' — the
        # conversation mined from the harness's own transcript). Same gates
        # as the other projections; the body is stored JSON, returned parsed.
        if fmt != "harness":
            raise HTTPException(status_code=404, detail=f"unknown rendition format {fmt!r}")
        pid = await _gated_raw_packet(session, p)
        rend = await b.get_rendition(pid, fmt)
        body = (rend or {}).get("body")
        if not body:
            raise HTTPException(
                status_code=404,
                detail=f"no {fmt} rendition for {pid!r} (mined at run end; older runs predate mining)",
            )
        try:
            artifact = json.loads(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="stored rendition body is not JSON") from exc
        return JSONResponse(artifact, headers=_PROJECTION_HEADERS)

    @app.get("/api/reuse")
    async def reuse_signal(
        goal: str | None = Query(default=None),
        since: str | None = Query(default=None),
    ) -> Any:
        # Behavioral corpus signal for researchers: packets matching goal/since
        # joined to their injection reuse score. Quarantined packets are
        # excluded — this is the "use as context" affordance (manyagent.web.md).
        # Normalize the query goal to the canonical slug so it matches the
        # normalized form stored on write (decision #1).
        rows = await b.list_packets(goal=slug.normalize_goal(goal), since=since, include_quarantined=False)
        scored = {s["packet_id"]: s for s in await b.reuse_score()}
        reuse = []
        for r in rows:
            s = scored.get(r["id"], {})
            reuse.append({
                "packet_id": r["id"],
                "goal": r.get("goal"),
                "type": r.get("type"),
                "created_at": r.get("created_at"),
                "inject_count": s.get("inject_count", 0),
                "reuse_score": s.get("reuse_score", 0.0),
            })
        return {"reuse": reuse}

    @app.get("/api/session/{session}/summary")
    async def session_summary(session: str) -> Any:  # noqa: C901 — one aggregation pass with a branch per packet type; splitting would scatter the timeline build
        """Retrieve complete session summary in JSON format.

        Returns a comprehensive JSON object with:
        - Session metadata (id, goal, status, created_at)
        - All agents that participated
        - Complete conversation timeline: raw traces (with mined conversation,
          events, metadata), posts, and distills organized chronologically
        - Summary statistics (item counts by type)
        """
        meta = await b.get_session(session)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"no session {session!r}")

        # Get all packets for the session
        rows = await b.list_packets(session_id=session, include_quarantined=True)

        # Get all agents for the session
        agent_rows = await b.list_agents(session)
        agents = {r["id"]: r for r in agent_rows}

        # Build the conversation timeline
        conversation_items = []

        for row in rows:
            item: dict[str, Any] = {
                "packet_id": row["id"],
                "type": row["type"],
                "agent_id": row.get("agent_id"),
                "goal": row.get("goal"),
                "created_at": row.get("created_at"),
                "quarantined": row.get("quarantined", False),
            }

            if row["type"] == "raw" and may_read_raw:
                # For raw traces, extract trace body, metadata, events, and mined conversation
                trace = await b.get_trace(row["id"])
                if trace and trace.get("body"):
                    try:
                        events, term = _parse_envelope(trace["body"])
                        # Parse the body as JSON to extract adapter and source_fidelity metadata
                        import json as _json

                        envelope = _json.loads(trace["body"])
                        item["trace_metadata"] = {
                            "adapter": envelope.get("adapter"),
                            "source_fidelity": envelope.get("source_fidelity"),
                            "bytes_in": envelope.get("bytes_in", 0),
                            "bytes_out": envelope.get("bytes_out", 0),
                            "scrub_report": envelope.get("scrub_report", {}),
                            "terminal": term,  # includes cols, rows, resizes if available
                        }
                        # Extract all events
                        all_events = [
                            {
                                "timestamp": e.get("ts"),
                                "kind": e.get("kind"),
                                "text": e.get("text"),
                                "truncated": e.get("truncated", False),
                            }
                            for e in events
                        ]
                        item["events"] = all_events
                        # Extract conversation turns (user/agent/tool interactions, exclude system noise)
                        conversation_kinds = {"user", "agent", "tool_call", "tool_result"}
                        item["conversation_turns"] = [e for e in all_events if e["kind"] in conversation_kinds]

                        # Try to fetch mined conversation from harness rendition
                        rend = await b.get_rendition(row["id"], "harness")
                        if rend and rend.get("body"):
                            try:
                                rendition = _json.loads(rend["body"])
                                # Extract all conversation turns from all segments
                                mined_turns = []
                                for segment in rendition.get("segments", []):
                                    for turn in segment.get("turns", []):
                                        mined_turns.append({
                                            "role": turn.get("role"),  # "user" or "assistant"
                                            "text": turn.get("text"),
                                            "timestamp": turn.get("ts"),
                                        })
                                if mined_turns:
                                    item["mined_conversation"] = mined_turns
                            except (ValueError, TypeError):
                                pass
                    except (ValueError, TypeError):
                        item["events"] = []
                        item["conversation_turns"] = []
                        item["trace_metadata"] = {}

            elif row["type"] == "post":
                # For posts, include structured content
                item["kind"] = row.get("kind")
                item["reply_to"] = row.get("reply_to")
                item["stance"] = row.get("stance")
                item["rating"] = row.get("rating")
                if row.get("structured"):
                    item["content"] = row["structured"]

            elif row["type"] == "distill":
                # For distills, include the bundle and metadata
                item["scope"] = row.get("scope")
                item["curator"] = row.get("curator")
                item["preference"] = row.get("preference")
                item["parents"] = row.get("parents", [])
                if row.get("bundle"):
                    item["bundle"] = row["bundle"]

            conversation_items.append(item)

        # Sort by timestamp for chronological order
        conversation_items.sort(key=lambda x: x.get("created_at") or "")

        return {
            "session": {
                "id": meta.get("id"),
                "goal": meta.get("goal"),
                "status": meta.get("status"),
                "created_at": meta.get("created_at"),
            },
            "agents": list(agents.values()),
            "conversation": conversation_items,
            "summary": {
                "total_items": len(conversation_items),
                "raw_traces": sum(1 for i in conversation_items if i["type"] == "raw"),
                "posts": sum(1 for i in conversation_items if i["type"] == "post"),
                "distills": sum(1 for i in conversation_items if i["type"] == "distill"),
            },
        }

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "identity": identity})

    @app.get("/.well-known/manyagent.json")
    async def well_known() -> JSONResponse:
        """The deployment's CURRENT public Bank connection, fetched/cached by
        `ma init` so the hosted stack can rotate keys or move without a
        package release. Serves only the MANYAGENT_WEB_PUBLISHED_* tunables —
        never this host's own resolved MANYAGENT_BANK_* (which may hold a
        privileged key the public must not see)."""
        return JSONResponse({
            "bank_url": config.resolve("MANYAGENT_WEB_PUBLISHED_BANK_URL", config.MANYAGENT_WEB_PUBLISHED_BANK_URL),
            "anon_key": config.resolve("MANYAGENT_WEB_PUBLISHED_ANON_KEY", config.MANYAGENT_WEB_PUBLISHED_ANON_KEY),
            "trusted_key": config.resolve(
                "MANYAGENT_WEB_PUBLISHED_TRUSTED_KEY", config.MANYAGENT_WEB_PUBLISHED_TRUSTED_KEY
            ),
        })

    return app
