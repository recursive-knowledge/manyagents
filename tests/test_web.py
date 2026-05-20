"""M9 tests for oma.web — the read-only public surface (oma.web.md Verification).

Load-bearing invariants:

* The anon (``public``) API **never** returns a trace body, even with
  ``?include=raw`` (the datasmith lesson, encoded as a test); a
  ``trusted``/``admin`` app + explicit ``?include=raw`` does.
* Quarantined packets are **visible but flagged** (``quarantined: true``) and
  **excluded** from the ``/api/reuse`` "use as context" signal.
* Every payload is the canonical ``KnowledgePacket`` shape; ``?p=`` resolves
  the exact ``curator/<hex>`` URL ``oma.distill`` emits (round-trip).
* Cursor pagination is stable across a mid-scan insert (no skip / no dup).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from oma.bank import FakeBank, make_cursor
from oma.core import clear_packet_cache
from oma.distill import curate
from oma.web import create_app


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_packet_cache()


def _client(bank: FakeBank, identity: str = "public") -> httpx.AsyncClient:
    app = create_app(bank=bank, identity=identity)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _raw(pid: str, *, created_at: str, quarantined: bool = False) -> dict[str, Any]:
    sid = pid.split("/")[0]
    return {
        "id": pid,
        "session_id": sid,
        "type": "raw",
        "agent_id": f"{sid}/agent-001-claude",
        "created_at": created_at,
        "quarantined": quarantined,
    }


def _post(pid: str, *, goal: str | None, created_at: str, quarantined: bool = False) -> dict[str, Any]:
    sid = pid.split("/")[0]
    return {
        "id": pid,
        "session_id": sid,
        "type": "post",
        "agent_id": f"{sid}/agent-001-claude",
        "kind": "reflection",
        "goal": goal,
        "created_at": created_at,
        "quarantined": quarantined,
        "structured": {"load_bearing_assumption": "x", "confidence": "low"},
    }


# --------------------------------------------------------------------------- #
# canonical shape
# --------------------------------------------------------------------------- #


async def test_session_view_is_canonical_and_carries_no_trace_body(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1", goal="ship it")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_packet(_post("S1/p1", goal="ship it", created_at="2026-05-19T00:00:02+00:00"))
    await fake_bank.put_trace("S1/r1", "SECRET-TRACE-BODY", scrub_version="v1")

    async with _client(fake_bank) as c:
        r = await c.get("/s/S1")
    assert r.status_code == 200
    data = r.json()
    assert data["session"]["id"] == "S1" and data["session"]["goal"] == "ship it"
    assert {"packets", "next_cursor"} <= data.keys()
    ids = {p["id"] for p in data["packets"]}
    assert ids == {"S1/r1", "S1/p1"}
    for p in data["packets"]:
        assert {"id", "type", "quarantined"} <= p.keys()
        assert "trace" not in p and "body" not in p
    assert "SECRET-TRACE-BODY" not in r.text


async def test_missing_session_landing_is_404(fake_bank: FakeBank) -> None:
    async with _client(fake_bank) as c:
        r = await c.get("/s/NOPE")
    assert r.status_code == 404


async def test_packet_by_p_and_404(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_post("S1/p1", goal=None, created_at="2026-05-19T00:00:01+00:00"))
    async with _client(fake_bank) as c:
        ok = await c.get("/s/S1", params={"p": "p1"})
        miss = await c.get("/s/S1", params={"p": "nope"})
    assert ok.status_code == 200 and ok.json()["id"] == "S1/p1"
    assert miss.status_code == 404


# --------------------------------------------------------------------------- #
# the exact URL oma.distill emits — curator/<hex> round-trip
# --------------------------------------------------------------------------- #


class _FakeModel:
    """A canned sync curator LLM (the ``_HeadlessModel`` shape)."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.calls += 1
        return self.payload


async def test_curate_url_roundtrips_without_a_session_row(fake_bank: FakeBank) -> None:
    import json

    sentence = "the retry_backoff() loop slept 30s per attempt under load before the fix"
    await fake_bank.put_packet({
        "id": "S1/p1",
        "session_id": "S1",
        "type": "post",
        "agent_id": "S1/agent-001-claude",
        "kind": "reflection",
        "goal": "g",
        "structured": {
            "load_bearing_assumption": sentence,
            "evidence": "verbatim from trace: 'cumtime 4.2s in retry_backoff()'",
            "evidence_ref": None,
            "proposed_next": "hoist the sleep out of retry_backoff() and cap attempts at 3",
            "predicted_outcome": "p99 latency drops ~4x; test_retry_budget passes",
            "confidence": "medium",
        },
    })
    bundle_json = json.dumps({
        "transferable_insights": [
            {
                "text": "when `retry_backoff` recompiles per call, hoist it to module scope",
                "applies_when": "a hot loop calls retry_backoff() on every attempt",
                "does_not_apply_when": "single-shot scripts that call retry_backoff() once",
                "evidence": [{"post_id": "S1/p1", "quote": "the retry_backoff() loop slept 30s per attempt"}],
                "confidence": "low",
            }
        ]
    })
    model = _FakeModel(bundle_json)
    pkt = await curate(scope="per_goal", goal="g", bank=fake_bank, model=model, mode="local")

    seg, _, rest = pkt.id.partition("/")
    assert seg == "curator"  # there is NO 'curator' session row
    async with _client(fake_bank) as c:
        r = await c.get(f"/s/{seg}", params={"p": rest})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == pkt.id and body["type"] == "distill"
    assert body["bundle"] == pkt.bundle and body["scope"] == "per_goal"


# --------------------------------------------------------------------------- #
# the load-bearing invariant: anon never gets a raw body, even ?include=raw
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("identity", "include", "expect_body"),
    [
        ("public", "raw", False),  # anon + explicit ask → still nothing (silently ignored)
        ("public", None, False),
        ("trusted", "raw", True),  # trusted + explicit ask → body
        ("trusted", None, False),  # trusted but didn't ask → no body
        ("admin", "raw", True),
    ],
)
async def test_raw_body_gate(fake_bank: FakeBank, identity: str, include: str | None, expect_body: bool) -> None:
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", "SECRET-TRACE-BODY", scrub_version="v1")

    params = {"p": "r1"}
    if include is not None:
        params["include"] = include
    async with _client(fake_bank, identity=identity) as c:
        r = await c.get("/s/S1", params=params)
    assert r.status_code == 200
    payload = r.json()
    if expect_body:
        assert payload["trace"] == "SECRET-TRACE-BODY"
    else:
        assert "trace" not in payload
        assert "SECRET-TRACE-BODY" not in r.text


# --------------------------------------------------------------------------- #
# quarantine: visible-but-flagged, excluded from the reuse signal
# --------------------------------------------------------------------------- #


async def test_quarantine_visible_in_session_but_excluded_from_reuse(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1", goal="g")
    await fake_bank.put_packet(_post("S1/good", goal="g", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_packet(_post("S1/bad", goal="g", created_at="2026-05-19T00:00:02+00:00"))
    await fake_bank.quarantine("S1/bad", "auditor: poisoned")

    async with _client(fake_bank) as c:
        sv = (await c.get("/s/S1")).json()
        reuse = (await c.get("/api/reuse", params={"goal": "g"})).json()

    by_id = {p["id"]: p for p in sv["packets"]}
    assert by_id["S1/bad"]["quarantined"] is True  # still visible (audit record)
    assert by_id["S1/good"]["quarantined"] is False
    reuse_ids = {row["packet_id"] for row in reuse["reuse"]}
    assert "S1/good" in reuse_ids and "S1/bad" not in reuse_ids  # excluded from reuse


async def test_reuse_signal_joins_injection_score(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1", goal="g")
    await fake_bank.put_packet(_post("S1/p1", goal="g", created_at="2026-05-19T00:00:01+00:00"))
    # A downstream session reuses it and rates well → reuse_score rises.
    await fake_bank.record_injection("S1/p1", "S2")
    await fake_bank.put_packet({
        "id": "S2/r1",
        "session_id": "S2",
        "type": "post",
        "agent_id": "S2/agent-001-claude",
        "kind": "reflection",
        "rating": 5,
        "created_at": "2026-05-19T01:00:00+00:00",
        "structured": {"load_bearing_assumption": "x", "confidence": "low"},
    })
    async with _client(fake_bank) as c:
        reuse = (await c.get("/api/reuse", params={"goal": "g"})).json()["reuse"]
    row = next(r for r in reuse if r["packet_id"] == "S1/p1")
    assert row["inject_count"] == 1 and row["reuse_score"] == 5.0


# --------------------------------------------------------------------------- #
# cursor pagination — stable across a mid-scan insert (no skip / no dup)
# --------------------------------------------------------------------------- #


async def test_cursor_stable_across_midscan_insert(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_raw("S1/A", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_packet(_raw("S1/B", created_at="2026-05-19T00:00:02+00:00"))
    await fake_bank.put_packet(_raw("S1/D", created_at="2026-05-19T00:00:04+00:00"))

    async with _client(fake_bank) as c:
        page1 = (await c.get("/api/packets", params={"limit": 2})).json()
        assert [p["id"] for p in page1["packets"]] == ["S1/A", "S1/B"]
        cur = page1["next_cursor"]
        assert cur == make_cursor({"created_at": "2026-05-19T00:00:02+00:00", "id": "S1/B"})

        # New row lands strictly between the cursor (B) and the next unseen (D).
        await fake_bank.put_packet(_raw("S1/C", created_at="2026-05-19T00:00:03+00:00"))

        page2 = (await c.get("/api/packets", params={"limit": 2, "cursor": cur})).json()
    ids2 = [p["id"] for p in page2["packets"]]
    assert "S1/D" in ids2  # no skip — D still surfaces
    assert "S1/A" not in ids2 and "S1/B" not in ids2  # no dup — cursor advanced past B
    # C is new data after the cursor; keyset correctly includes it (acceptable).


# --------------------------------------------------------------------------- #
# /s/{session}/agents — derived activity span (frozen-model helper, dumb route)
# --------------------------------------------------------------------------- #


async def test_session_agents_derived_span(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1")
    seq = await fake_bank.next_agent_seq("S1")
    await fake_bank.put_agent("S1/agent-001-claude", session_id="S1", adapter="claude", seq=seq)
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:05+00:00"))
    await fake_bank.put_packet(_raw("S1/r2", created_at="2026-05-19T00:00:09+00:00"))

    async with _client(fake_bank) as c:
        agents = (await c.get("/s/S1/agents")).json()["agents"]
    assert len(agents) == 1
    a = agents[0]
    assert a["id"] == "S1/agent-001-claude" and a["adapter"] == "claude"
    assert a["start_date"] is not None and a["end_date"] is not None
    assert a["start_date"] <= a["end_date"]
    # end_date is the last packet (final activity).
    assert a["end_date"].startswith("2026-05-19T00:00:09")


# --------------------------------------------------------------------------- #
# /s/{session}/a/{agent} — per-agent deep link (full metadata + owned packets)
# --------------------------------------------------------------------------- #


async def test_agent_view_returns_full_metadata_and_owned_packets(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1")
    seq = await fake_bank.next_agent_seq("S1")
    await fake_bank.put_agent("S1/agent-001-claude", session_id="S1", adapter="claude", seq=seq)
    # Two packets owned by the agent and one orphan (agent_id=None) that must NOT appear.
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:05+00:00"))
    await fake_bank.put_packet(_raw("S1/r2", created_at="2026-05-19T00:00:09+00:00"))
    orphan = _raw("S1/r3", created_at="2026-05-19T00:00:10+00:00")
    orphan["agent_id"] = None
    await fake_bank.put_packet(orphan)

    async with _client(fake_bank) as c:
        r = await c.get("/s/S1/a/agent-001-claude")
    assert r.status_code == 200
    data = r.json()
    a = data["agent"]
    # Every collected agent field is surfaced (raw row + derived span).
    assert a["id"] == "S1/agent-001-claude"
    assert a["session_id"] == "S1"
    assert a["adapter"] == "claude"
    assert a["seq"] == 1
    assert a["created_at"] is not None  # DB registration timestamp
    assert a["start_date"] is not None and a["end_date"] is not None
    assert a["end_date"].startswith("2026-05-19T00:00:09")
    # Only this agent's packets — the orphan is filtered out.
    ids = {p["id"] for p in data["packets"]}
    assert ids == {"S1/r1", "S1/r2"}


async def test_agent_view_404s_unknown_agent(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1")
    async with _client(fake_bank) as c:
        r = await c.get("/s/S1/a/agent-999-nope")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# RLS DB-enforced pairing (gated): the read-only key cannot write at the DB,
# even when a handler attempts it (the datasmith lesson, paired with oma.bank).
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_public_bank_cannot_write_at_the_db() -> None:
    from oma.bank import get_bank

    pub = get_bank("public")
    with pytest.raises(Exception):
        await pub.put_packet({"id": "ATTACK/x", "type": "post", "agent_id": None})
