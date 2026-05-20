"""oms.web.api — the read-only HTTP surface over the Bank (M9).

This is ``oms``'s public attack/abuse surface (oms.web.md). Two invariants are
load-bearing and tested:

1. **Anon never receives a trace body, even with ``?include=raw``.** Raw bodies
   are outside the ``public`` role's grant at the database (oms.bank migration
   ``00004``); the app refuses to even *render* one for an anon identity, so the
   DB grant and the app agree (datasmith's lesson: app-layer "read-only" leaked
   until the Postgres grant was revoked). ``?include=raw`` is a
   ``trusted``/``admin``-only affordance and is silently ignored for anon.
2. **Quarantined packets are visible but flagged** (``quarantined: true``) and
   excluded from the reuse signal (the "use as context" affordance).

The route layer is dumb: every payload is the canonical ``KnowledgePacket``
(oms.core) — derivation (the agent activity span) lives in the frozen model,
pagination in ``oms.bank.make_cursor``. Identity is fixed at app construction
(``create_app(*, identity=...)``), never read from a request header — the web
tier cannot be tricked into escalating.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from oms.bank import Bank, get_bank, make_cursor
from oms.core import Agent, Packet
from oms.utils import config

# Identities permitted to read a raw trace body (oms.web.md "Trace/PII"):
# public (anon) is structurally excluded.
_RAW_IDENTITIES = frozenset({"trusted", "admin"})


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
    return min(limit, config.OMS_WEB_MAX_PAGE_LIMIT)


def create_app(*, bank: Bank | None = None, identity: str = "public") -> FastAPI:  # noqa: C901 — N route closures inherently bump cyclomatic complexity
    """Build the read-only API. ``identity`` is fixed here (not per-request);
    ``bank`` defaults to the memoized Bank for that identity (tests pass a
    FakeBank). The returned app is structurally incapable of escalating: the
    raw-trace gate is closed over ``identity`` at construction time."""
    b: Bank = bank if bank is not None else get_bank(identity)
    may_read_raw = identity in _RAW_IDENTITIES

    app = FastAPI(
        title="Oh My Swarm — read API",
        description="Read-only window over the Knowledge Bank (oms.web).",
        version="0.1.0",
    )
    app.state.bank = b
    app.state.identity = identity

    @app.get("/s/{session}")
    async def session_view(
        session: str,
        p: str | None = Query(default=None),
        include: str | None = Query(default=None),
        limit: int = Query(default=config.OMS_WEB_PAGE_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> Any:
        # `?p=` → one packet. The id is `{session}/{p}` — the exact URL
        # oms.distill emits (a curator bundle lives under the synthetic
        # `curator/<hex>` id, so /s/curator?p=<hex> round-trips; no session
        # row is required in that case).
        if p is not None:
            pid = f"{session}/{p}"
            rec = await b.get_packet(pid)
            if rec is None:
                raise HTTPException(status_code=404, detail=f"no packet {pid!r}")
            out = _record(rec)
            # Raw body: trusted/admin + explicit ?include=raw only. Anon never,
            # regardless of query params (silently ignored — an attempted leak,
            # not an error).
            if may_read_raw and include == "raw" and rec.get("type") == "raw":
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

    @app.get("/api/packets")
    async def corpus_packets(
        type: str | None = Query(default=None),
        since: str | None = Query(default=None),
        limit: int = Query(default=config.OMS_WEB_PAGE_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> Any:
        lim = _clamp_limit(limit)
        rows = await b.list_packets(type=type, since=since, limit=lim, cursor=cursor, include_quarantined=True)
        return _page(rows, lim)

    @app.get("/api/reuse")
    async def reuse_signal(
        goal: str | None = Query(default=None),
        since: str | None = Query(default=None),
    ) -> Any:
        # Behavioral corpus signal for researchers: packets matching goal/since
        # joined to their injection reuse score. Quarantined packets are
        # excluded — this is the "use as context" affordance (oms.web.md).
        rows = await b.list_packets(goal=goal, since=since, include_quarantined=False)
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

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "identity": identity})

    return app
