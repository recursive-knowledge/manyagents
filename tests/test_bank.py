"""M2 offline tests for manyagent.bank — retry, idempotent put, FakeBank round-trip,
atomic next_agent_seq, reuse_score, quarantine, migration integrity
(manyagent.bank.md Verification; security/RLS matrix is the gated integration suite).
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from manyagent.bank import FakeBank, make_cursor, with_backoff

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "supabase" / "migrations"

# --------------------------------------------------------------------------- #
# retry wrapper
# --------------------------------------------------------------------------- #


async def test_with_backoff_retries_then_succeeds() -> None:
    calls = {"n": 0}

    @with_backoff(max_retries=3, base_delay=0.0)
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3


async def test_with_backoff_exhausts_and_raises() -> None:
    @with_backoff(max_retries=2, base_delay=0.0)
    async def always_fails() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await always_fails()


async def test_with_backoff_nonretryable_fails_fast() -> None:
    """A config error (missing Bank key) must surface on the FIRST attempt —
    backoff in front of an error a retry cannot fix only adds dead seconds
    (the uv-tool-install onboarding report)."""
    from manyagent.bank import NonRetryableError
    from manyagent.bank.supabase_bank import BankConfigError

    calls = {"n": 0}

    @with_backoff(max_retries=3, base_delay=0.0)
    async def misconfigured() -> None:
        calls["n"] += 1
        raise BankConfigError("Bank identity 'trusted' has no key (MANYAGENT_BANK_TRUSTED_KEY unset)")

    with pytest.raises(BankConfigError, match="no key"):
        await misconfigured()
    assert calls["n"] == 1  # no retries
    # BankConfigError stays a RuntimeError (callers matching the old type keep
    # working) AND is the retry shim's fail-fast marker.
    assert issubclass(BankConfigError, RuntimeError) and issubclass(BankConfigError, NonRetryableError)


async def test_supabase_bank_missing_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real Bank's missing-key path raises the non-retryable type. With
    the derived demo defaults, the only way to have NO key is to set the env
    var explicitly empty (unset falls back to the baked default)."""
    from manyagent.bank.supabase_bank import BankConfigError, SupabaseBank

    monkeypatch.setenv("MANYAGENT_BANK_TRUSTED_KEY", "")
    bank = SupabaseBank("trusted")
    with pytest.raises(BankConfigError, match="MANYAGENT_BANK_TRUSTED_KEY unset"):
        await bank.get_session("S-NOKEY")


# --------------------------------------------------------------------------- #
# FakeBank round-trip + idempotency
# --------------------------------------------------------------------------- #


async def test_round_trip_session_agent_packet_trace(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("CMA1-FJ2P", goal="speed up parser")
    s = await fake_bank.get_session("CMA1-FJ2P")
    assert s is not None and s["goal"] == "speed up parser" and s["status"] == "active"

    seq = await fake_bank.next_agent_seq("CMA1-FJ2P")
    await fake_bank.put_agent(f"CMA1-FJ2P/agent-{seq:03d}-claude", session_id="CMA1-FJ2P", adapter="claude", seq=seq)
    agents = await fake_bank.list_agents("CMA1-FJ2P")
    assert len(agents) == 1 and agents[0]["adapter"] == "claude"

    await fake_bank.put_packet({"id": "CMA1-FJ2P/p1", "session_id": "CMA1-FJ2P", "type": "raw"})
    await fake_bank.put_trace("CMA1-FJ2P/p1", "scrubbed body", scrub_version="v1")
    assert (await fake_bank.get_packet("CMA1-FJ2P/p1"))["type"] == "raw"
    tr = await fake_bank.get_trace("CMA1-FJ2P/p1")
    assert tr is not None and tr["body"] == "scrubbed body" and tr["scrub_version"] == "v1"


async def test_put_packet_is_idempotent(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet({"id": "S/p", "session_id": "S", "type": "post", "rating": None})
    await fake_bank.put_packet({"id": "S/p", "session_id": "S", "type": "post", "rating": 5})
    rows = await fake_bank.list_packets(session_id="S")
    assert len(rows) == 1 and rows[0]["rating"] == 5  # one row, second upserts


async def test_next_agent_seq_concurrent_distinct_contiguous(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    seqs = await asyncio.gather(*[fake_bank.next_agent_seq("S") for _ in range(50)])
    assert sorted(seqs) == list(range(1, 51))  # distinct, contiguous, no gaps/dups


async def test_put_agent_round_trips_principal_id(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_agent("S/agent-001-claude", session_id="S", adapter="claude", seq=1, principal_id="P1")
    row = await fake_bank.get_agent("S/agent-001-claude")
    assert row is not None and row["principal_id"] == "P1"
    # Default keeps legacy callers source-compatible — NULL principal.
    await fake_bank.put_agent("S/agent-002-codex", session_id="S", adapter="codex", seq=2)
    assert (await fake_bank.get_agent("S/agent-002-codex"))["principal_id"] is None


async def test_list_agents_by_principal_spans_sessions(fake_bank: FakeBank) -> None:
    # The same principal registers its adapter in two sessions/goals.
    for s, goal in (("S1", "parser"), ("S2", "solver")):
        await fake_bank.put_session(s, goal=goal)
        await fake_bank.put_agent(f"{s}/agent-001-claude", session_id=s, adapter="claude", seq=1, principal_id="P1")
    # A different principal in a third session must stay isolated.
    await fake_bank.put_session("S3")
    await fake_bank.put_agent("S3/agent-001-claude", session_id="S3", adapter="claude", seq=1, principal_id="P2")

    rows = await fake_bank.list_agents_by_principal("P1")
    assert [r["session_id"] for r in rows] == ["S1", "S2"]  # sorted, both sessions, P2 excluded
    assert await fake_bank.list_agents_by_principal("nope") == []


# --------------------------------------------------------------------------- #
# reuse_score (mirrors the 00007 SQL view)
# --------------------------------------------------------------------------- #


async def test_reuse_score_rises_for_injected_then_well_rated(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("SRC")
    await fake_bank.put_session("TGT")
    await fake_bank.put_packet({"id": "SRC/post1", "session_id": "SRC", "type": "post"})
    await fake_bank.put_packet({"id": "UNUSED", "session_id": "SRC", "type": "post"})

    flat = await fake_bank.reuse_score("UNUSED")
    assert flat[0] == {"packet_id": "UNUSED", "inject_count": 0, "reuse_score": 0.0}

    await fake_bank.record_injection("SRC/post1", "TGT")
    await fake_bank.record_injection("SRC/post1", "TGT")  # idempotent on PK
    mid = await fake_bank.reuse_score("SRC/post1")
    assert mid[0]["inject_count"] == 1 and mid[0]["reuse_score"] == 0.0  # target not yet rated

    # Backfill a good rating on the target session → view recomputes, no
    # distill packet touched.
    await fake_bank.put_packet({"id": "TGT/post1", "session_id": "TGT", "type": "post", "rating": 5})
    after = await fake_bank.reuse_score("SRC/post1")
    assert after[0]["inject_count"] == 1 and after[0]["reuse_score"] == 5.0
    assert (await fake_bank.reuse_score("UNUSED"))[0]["reuse_score"] == 0.0  # still flat


async def test_reuse_score_accept_bonus(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("SRC")
    await fake_bank.put_session("TGT")
    await fake_bank.put_packet({"id": "SRC/p", "session_id": "SRC", "type": "post"})
    await fake_bank.record_injection("SRC/p", "TGT")
    await fake_bank.put_packet({
        "id": "TGT/d",
        "session_id": "TGT",
        "type": "distill",
        "scope": "per_goal",
        "bundle": {},
        "preference": "accept",
    })
    assert (await fake_bank.reuse_score("SRC/p"))[0]["reuse_score"] == 4.0


# --------------------------------------------------------------------------- #
# quarantine + pagination
# --------------------------------------------------------------------------- #


async def test_quarantine_excludes_from_curation_parents(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet({"id": "S/good", "session_id": "S", "type": "post"})
    await fake_bank.put_packet({"id": "S/bad", "session_id": "S", "type": "post"})
    await fake_bank.quarantine("S/bad", "prompt-injection suspected", auditor_version="v1")

    visible = await fake_bank.list_packets(type="post", include_quarantined=False)
    assert {p["id"] for p in visible} == {"S/good"}
    # quarantined packet is still visible when not excluded (non-hiding state)
    allp = await fake_bank.list_packets(type="post", include_quarantined=True)
    assert {p["id"] for p in allp} == {"S/good", "S/bad"}
    bad = await fake_bank.get_packet("S/bad")
    assert bad is not None and bad["quarantined"] is True and bad["quarantine_reason"]


async def test_list_packets_cursor_pagination_stable(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    for i in range(5):
        await fake_bank.put_packet({
            "id": f"S/p{i}",
            "session_id": "S",
            "type": "post",
            "created_at": f"2026-05-19T00:00:0{i}",
        })
    page1 = await fake_bank.list_packets(session_id="S", limit=2)
    assert [p["id"] for p in page1] == ["S/p0", "S/p1"]
    page2 = await fake_bank.list_packets(session_id="S", limit=2, cursor=make_cursor(page1[-1]))
    assert [p["id"] for p in page2] == ["S/p2", "S/p3"]


# --------------------------------------------------------------------------- #
# migration integrity (offline; full apply/no-op is the gated integration suite)
# --------------------------------------------------------------------------- #


def test_migration_files_are_contiguous_00001_to_00011() -> None:
    files = sorted(p.name for p in _MIGRATIONS.glob("*.sql"))
    prefixes = [f[:5] for f in files]
    assert prefixes == [f"{i:05d}" for i in range(1, 12)], files


async def test_rendition_upsert_and_get(fake_bank: FakeBank) -> None:
    """M13.0: renditions key on (packet_id, format); put is an upsert so
    re-mining the same run is idempotent (no row pile-up); get returns the
    full row or None."""
    await fake_bank.put_session("S")
    await fake_bank.put_packet({"id": "S/r1", "session_id": "S", "type": "raw"})
    assert await fake_bank.get_rendition("S/r1", "harness") is None

    await fake_bank.put_rendition("S/r1", "harness", '{"v": 1}', miner_version="claude-v1")
    row = await fake_bank.get_rendition("S/r1", "harness")
    assert row is not None
    assert row["body"] == '{"v": 1}' and row["miner_version"] == "claude-v1" and row["complete"] is True

    await fake_bank.put_rendition("S/r1", "harness", '{"v": 2}', miner_version="claude-v2")
    row2 = await fake_bank.get_rendition("S/r1", "harness")
    assert row2 is not None and row2["body"] == '{"v": 2}'  # upsert, not append
    assert row2["created_at"] == row["created_at"]  # first-write timestamp survives


@pytest.mark.parametrize(
    ("fname", "must_contain"),
    [
        ("00001_initial_schema.sql", ["create table if not exists sessions", "next_agent_seq", "security definer"]),
        ("00002_packet_quarantine.sql", ["quarantined", "quarantine_reason", "auditor_version"]),
        ("00003_trace_scrub_meta.sql", ["scrub_version", "complete"]),
        ("00004_three_role_rls.sql", ["enable row level security", "public_read", "revoke all on all tables"]),
        ("00005_preference.sql", ["preference", "parent_attempt"]),
        ("00006_swarms_taxonomy.sql", ["type in ('raw', 'post', 'distill')", "rating", "goal"]),
        ("00007_injection_ledger.sql", ["injections", "reuse_score", "create role curator"]),
        ("00011_agent_principal.sql", ["principal_id", "agents_principal_idx", "add column if not exists"]),
    ],
)
def test_migration_content_tokens(fname: str, must_contain: list[str]) -> None:
    text = (_MIGRATIONS / fname).read_text().lower()
    for token in must_contain:
        assert token.lower() in text, f"{fname} missing {token!r}"
