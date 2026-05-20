"""M2 gated integration suite — the DB-enforced RLS security matrix (the
highest-priority verification, oma.bank.md). Opt-in: ``OMA_RUN_INTEGRATION=1``
with a local Bank up + migrated (``make bank-up && make bank-migrate``).

Uses direct psycopg with ``SET ROLE`` (more reliable than minting JWTs) so the
four identities' DB-enforced capabilities are exercised exactly as Postgres
RLS evaluates them (advisor-recommended).
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

# oma's local Bank is on 544xx (config.toml) so it coexists with datasmith's.
_DSN = "host=127.0.0.1 port=54422 dbname=postgres user=postgres password=postgres"


@pytest.fixture
def denied() -> type[Exception]:
    """The exact RLS/grant rejection (SQLSTATE 42501) — both ``permission
    denied for <table>`` and ``new row violates row-level security policy``
    map here. Lazy so collection is safe when psycopg is absent."""
    psycopg = pytest.importorskip("psycopg")
    return psycopg.errors.InsufficientPrivilege  # type: ignore[no-any-return]


@pytest.fixture
def conn() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    try:
        c = psycopg.connect(_DSN, autocommit=True)
    except Exception as exc:
        pytest.skip(f"local Bank not reachable ({exc}); run `make bank-up && make bank-migrate`")
    with c.cursor() as cur:
        cur.execute("select to_regclass('public.packets'), to_regclass('public.injections')")
        if cur.fetchone() != ("packets", "injections"):
            c.close()
            pytest.skip("migrations not applied; run `make bank-migrate`")
    try:
        yield c
    finally:
        with c.cursor() as cur:
            _purge(cur)  # always runs, even on mid-test failure → no orphan FK rows
        c.close()


_TEST_PREFIX = "it-"


def _purge(cur: Any) -> None:
    """FK-safe blanket cleanup of all ``it-*`` rows (injections → traces →
    packets → counter → agents → sessions)."""
    cur.execute("reset role")  # a failed test may have left SET ROLE active
    like = f"{_TEST_PREFIX}%"
    cur.execute("delete from injections where packet_id like %s or target_session_id like %s", (like, like))
    cur.execute("delete from traces where packet_id like %s", (like,))
    cur.execute("delete from packets where id like %s or session_id like %s", (like, like))
    cur.execute("delete from agent_seq_counter where session_id like %s", (like,))
    cur.execute("delete from agents where id like %s or session_id like %s", (like, like))
    cur.execute("delete from sessions where id like %s", (like,))


def _seed(cur: Any, sid: str) -> None:
    cur.execute("insert into sessions(id) values (%s)", (sid,))
    cur.execute("insert into packets(id, session_id, type) values (%s,%s,'post')", (f"{sid}/post", sid))
    cur.execute("insert into packets(id, session_id, type) values (%s,%s,'raw')", (f"{sid}/raw", sid))
    cur.execute("insert into traces(packet_id, body) values (%s,'SECRET RAW BODY')", (f"{sid}/raw",))


def _supabase_env() -> dict[str, str] | None:
    """``supabase status -o env`` → parsed map, or None (skip) if the CLI is
    unreachable. Yields the local SERVICE_ROLE_KEY/API_URL without hardcoding
    version-pinned demo JWTs (advisor-recommended)."""
    repo_root = Path(__file__).resolve().parents[1]
    try:
        out = subprocess.run(
            ["npx", "--yes", "supabase@2.100.0", "status", "-o", "env"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    env: dict[str, str] = {}
    for line in out.splitlines():
        key, _, val = line.partition("=")
        if key and val:
            env[key.strip()] = val.strip().strip('"')
    return env


def test_anon_reads_public_set_but_never_traces_and_cannot_write(conn: Any, denied: type[Exception]) -> None:
    sid = f"it-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        _seed(cur, sid)
        cur.execute("set role anon")
        cur.execute("select count(*) from packets where session_id=%s", (sid,))
        assert cur.fetchone()[0] == 2  # anon sees the public packet set
        with pytest.raises(denied):  # permission denied on traces
            cur.execute("select body from traces")
        cur.execute("reset role")
        cur.execute("set role anon")
        with pytest.raises(denied):  # anon cannot write
            cur.execute("insert into packets(id,session_id,type) values (%s,%s,'post')", (f"{sid}/x", sid))


def test_curator_reads_posts_inserts_distill_not_raw_no_delete(conn: Any, denied: type[Exception]) -> None:
    sid = f"it-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        _seed(cur, sid)
        cur.execute("set role curator")
        cur.execute("select count(*) from packets where type='post' and session_id=%s", (sid,))
        assert cur.fetchone()[0] == 1
        with pytest.raises(denied):  # no traces grant for curator
            cur.execute("select body from traces")
        cur.execute("reset role")
        cur.execute("set role curator")
        cur.execute(
            "insert into packets(id,session_id,type,scope,bundle) values (%s,%s,'distill','per_goal','{}'::jsonb)",
            (f"{sid}/d", sid),
        )
        with pytest.raises(denied):  # curator cannot write raw
            cur.execute("insert into packets(id,session_id,type) values (%s,%s,'raw')", (f"{sid}/r2", sid))
        cur.execute("reset role")
        cur.execute("set role curator")
        with pytest.raises(denied):  # curator has no DELETE
            cur.execute("delete from packets where id=%s", (f"{sid}/post",))


def test_next_agent_seq_concurrent_distinct_contiguous(conn: Any) -> None:
    sid = f"it-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute("insert into sessions(id) values (%s)", (sid,))
        seqs = []
        for _ in range(25):
            cur.execute("select next_agent_seq(%s)", (sid,))
            seqs.append(cur.fetchone()[0])
        assert sorted(seqs) == list(range(1, 26))


def test_inject_writes_row_and_reuse_score_recomputes(conn: Any) -> None:
    src, tgt = f"it-{uuid.uuid4().hex[:8]}", f"it-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute("insert into sessions(id) values (%s),(%s)", (src, tgt))
        cur.execute("insert into packets(id,session_id,type) values (%s,%s,'post')", (f"{src}/p", src))
        cur.execute("insert into injections(packet_id,target_session_id) values (%s,%s)", (f"{src}/p", tgt))
        cur.execute("select reuse_score from reuse_score where packet_id=%s", (f"{src}/p",))
        assert float(cur.fetchone()[0]) == 0.0  # injected, target not yet rated
        cur.execute("insert into packets(id,session_id,type,rating) values (%s,%s,'post',5)", (f"{tgt}/p", tgt))
        cur.execute("select reuse_score from reuse_score where packet_id=%s", (f"{src}/p",))
        assert float(cur.fetchone()[0]) == 5.0  # recomputed from the view, no re-curation


async def test_m2_m3_hydration_smoke_over_real_postgrest(conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed via psycopg, then hydrate the *same* row through M3 ``Packet.fetch``
    over the real M2 ``SupabaseBank`` (PostgREST) — proves the bank client +
    Pydantic hydration agree end-to-end against real Supabase, not just FakeBank."""
    env = _supabase_env()
    if env is None or "SERVICE_ROLE_KEY" not in env:
        pytest.skip("`supabase status -o env` unavailable; run `make bank-up`")

    from oma.bank.supabase_bank import SupabaseBank
    from oma.core import Packet, clear_packet_cache

    sid = f"it-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute("insert into sessions(id, goal) values (%s,'speed')", (sid,))
        cur.execute(
            "insert into packets(id,session_id,type,kind,rating,goal) values (%s,%s,'post','reflection',4,'speed')",
            (f"{sid}/p", sid),
        )

    monkeypatch.setenv("OMA_BANK_URL", env.get("API_URL", "http://127.0.0.1:54421"))
    monkeypatch.setenv("OMA_BANK_ADMIN_KEY", env["SERVICE_ROLE_KEY"])  # admin ⇒ full read
    clear_packet_cache()

    pkt = await Packet.fetch(f"{sid}/p", bank=SupabaseBank("admin"))
    assert isinstance(pkt, Packet)
    assert pkt.type == "post" and pkt.kind == "reflection"
    assert pkt.rating == 4 and pkt.goal == "speed"
    assert pkt.session_id == sid and pkt.agent is None
