"""M9 tests for manyagent.web — the read-only public surface (manyagent.web.md Verification).

Load-bearing invariants:

* The anon (``public``) API **never** returns a trace body, even with
  ``?include=raw`` (the datasmith lesson, encoded as a test); a
  ``trusted``/``admin`` app + explicit ``?include=raw`` does.
* Quarantined packets are **visible but flagged** (``quarantined: true``) and
  **excluded** from the ``/api/reuse`` "use as context" signal.
* Every payload is the canonical ``KnowledgePacket`` shape; ``?p=`` resolves
  the exact ``curator/<hex>`` URL ``manyagent.distill`` emits (round-trip).
* Cursor pagination is stable across a mid-scan insert (no skip / no dup).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from manyagent.bank import FakeBank, make_cursor
from manyagent.core import clear_packet_cache
from manyagent.distill import curate
from manyagent.web import create_app


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
# the exact URL manyagent.distill emits — curator/<hex> round-trip
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
# the trace-body gate: pre-alpha default is PUBLIC (MANYAGENT_WEB_PUBLIC_RAW=1 +
# migration 00008); MANYAGENT_WEB_PUBLIC_RAW=0 restores the original M9 invariant
# (anon never gets a raw body, even ?include=raw — the datasmith lesson).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("identity", "include", "expect_body"),
    [
        ("public", "raw", True),  # pre-alpha: anon + explicit ask → scrubbed body
        ("public", None, False),  # didn't ask → never attached
        ("trusted", "raw", True),  # trusted + explicit ask → body
        ("trusted", None, False),  # trusted but didn't ask → no body
        ("admin", "raw", True),
    ],
)
async def test_raw_body_gate(fake_bank: FakeBank, identity: str, include: str | None, expect_body: bool) -> None:
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", "SCRUBBED-TRACE-BODY", scrub_version="v1")

    params = {"p": "r1"}
    if include is not None:
        params["include"] = include
    async with _client(fake_bank, identity=identity) as c:
        r = await c.get("/s/S1", params=params)
    assert r.status_code == 200
    payload = r.json()
    if expect_body:
        assert payload["trace"] == "SCRUBBED-TRACE-BODY"
    else:
        assert "trace" not in payload
        assert "SCRUBBED-TRACE-BODY" not in r.text


@pytest.mark.parametrize("identity", ["public"])
async def test_raw_body_gate_switch_off_restores_anon_exclusion(
    fake_bank: FakeBank, identity: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MANYAGENT_WEB_PUBLIC_RAW=0 is the app-layer kill switch: anon loses the body
    AND the cast endpoint, even with the explicit ask; trusted is unaffected."""
    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_RAW", "0")
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    body = _envelope([{"ts": 0.0, "kind": "system", "text": "SECRET-TRACE-BODY"}])
    await fake_bank.put_trace("S1/r1", body, scrub_version="v1")

    async with _client(fake_bank, identity=identity) as c:
        r = await c.get("/s/S1", params={"p": "r1", "include": "raw"})
        assert r.status_code == 200
        assert "SECRET-TRACE-BODY" not in r.text
        assert (await c.get("/api/cast/S1/r1")).status_code == 404
    async with _client(fake_bank, identity="trusted") as c:
        r = await c.get("/s/S1", params={"p": "r1", "include": "raw"})
        assert r.json()["trace"] == body
        cast = await c.get("/api/cast/S1/r1")
        assert cast.status_code == 200 and "SECRET-TRACE-BODY" in cast.text


# --------------------------------------------------------------------------- #
# /api/cast — the asciinema rendition (synthesized pre-M12)
# --------------------------------------------------------------------------- #


def _envelope(events: list[dict[str, Any]]) -> str:
    import json

    return json.dumps({
        "session_id": "S1",
        "agent_id": "S1/agent-001-claude",
        "adapter": "claude",
        "source_fidelity": "pty",
        "events": events,
    })


async def test_cast_synthesizes_v2_from_untimed_envelope(fake_bank: FakeBank) -> None:
    import json

    text = "\x1b[1mhello\x1b[0m world — " * 200  # multi-chunk, with ANSI + non-ASCII
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", _envelope([{"ts": 0.0, "kind": "system", "text": text}]), scrub_version="v1")

    async with _client(fake_bank) as c:  # public identity — the pre-alpha default
        r = await c.get("/api/cast/S1/r1", params={"cols": 100, "rows": 40})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-asciicast")
    lines = r.text.strip().splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2 and header["width"] == 100 and header["height"] == 40
    events = [json.loads(line) for line in lines[1:]]
    assert all(code == "o" for _t, code, _d in events)
    assert "".join(d for _t, _c, d in events) == text  # lossless reassembly
    times = [t for t, _c, _d in events]
    assert times == sorted(times) and times[0] == 0.0
    assert len(events) > 1  # synthetic pacing actually chunked it


async def test_cast_replays_real_timing_for_timed_envelopes(fake_bank: FakeBank) -> None:
    """M12-ready: an envelope with per-chunk timestamps replays real timing
    (normalized to t0) instead of synthetic pacing."""
    import json

    events = [
        {"ts": 5.0, "kind": "system", "text": "a"},
        {"ts": 6.5, "kind": "system", "text": "b"},
        {"ts": 9.25, "kind": "system", "text": "c"},
    ]
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", _envelope(events), scrub_version="v1")

    async with _client(fake_bank) as c:
        r = await c.get("/api/cast/S1/r1")
    cast_events = [json.loads(line) for line in r.text.strip().splitlines()[1:]]
    assert [(t, d) for t, _c, d in cast_events] == [(0.0, "a"), (1.5, "b"), (4.25, "c")]


async def test_cast_header_uses_recorded_terminal_geometry(fake_bank: FakeBank) -> None:
    """M12.2: the envelope's `term` drives the header (the formatting-mess
    fix — a TUI replayed at a guessed width wraps every box border); explicit
    query params still override; mid-run resizes become `r` events."""
    import json

    body = json.dumps({
        "session_id": "S1",
        "agent_id": "S1/agent-001-claude",
        "adapter": "claude",
        "source_fidelity": "pty",
        "term": {"cols": 159, "rows": 37, "resizes": [[6.0, 100, 37]]},
        "events": [
            {"ts": 1.0, "kind": "system", "text": "a"},
            {"ts": 8.0, "kind": "system", "text": "b"},
        ],
    })
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", body, scrub_version="v1")

    async with _client(fake_bank) as c:
        lines = (await c.get("/api/cast/S1/r1")).text.strip().splitlines()
        header = json.loads(lines[0])
        assert (header["width"], header["height"]) == (159, 37)  # recorded geometry
        events = [json.loads(line) for line in lines[1:]]
        assert [e for e in events if e[1] == "r"] == [[5.0, "r", "100x37"]]  # resize, t0-normalized
        assert [e[2] for e in events if e[1] == "o"] == ["a", "b"]

        override = json.loads((await c.get("/api/cast/S1/r1", params={"cols": 80, "rows": 24})).text.splitlines()[0])
        assert (override["width"], override["height"]) == (80, 24)  # explicit wins


async def test_cast_guesses_width_from_rule_runs_for_legacy_traces(fake_bank: FakeBank) -> None:
    """Legacy envelopes (no `term`): Claude-Code-style TUIs draw horizontal
    rules exactly one terminal width wide — the longest ─-run sizes the
    header instead of a blind 120."""
    import json

    text = "some output\r\n" + "─" * 106 + "\r\nmore output\r\n" + "─" * 80 + "\r\n"
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", _envelope([{"ts": 0.0, "kind": "system", "text": text}]), scrub_version="v1")

    async with _client(fake_bank) as c:
        header = json.loads((await c.get("/api/cast/S1/r1")).text.splitlines()[0])
    assert header["width"] == 106


async def test_terminal_text_renders_through_a_real_screen_model(fake_bank: FakeBank) -> None:
    """/api/cast/{s}/{p}/text replays the stream through a VT emulator at the
    recorded geometry — colors drop, carriage-return overwrites resolve, long
    lines wrap at the recorded width. A regex strip can do none of these."""
    import json as _json

    body = _json.dumps({
        "session_id": "S1",
        "agent_id": "S1/agent-001-claude",
        "adapter": "claude",
        "source_fidelity": "pty",
        "term": {"cols": 20, "rows": 6, "resizes": []},
        "events": [
            {"ts": 0.0, "kind": "system", "text": "\x1b[1mhello\x1b[0m world\r\n"},
            {"ts": 1.0, "kind": "system", "text": "XXXX\rYY\r\n"},  # in-place overwrite
            {"ts": 2.0, "kind": "system", "text": "a" * 25 + "\r\n"},  # wraps at 20 cols
        ],
    })
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", body, scrub_version="v1")

    async with _client(fake_bank) as c:
        r = await c.get("/api/cast/S1/r1/text")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "max-age" in r.headers.get("cache-control", "")
    lines = r.text.splitlines()
    assert lines[0] == "hello world"  # bold dropped, not regex-mangled
    assert lines[1] == "YYXX"  # \r overwrite resolved by the screen model
    assert lines[2] == "a" * 20 and lines[3] == "a" * 5  # wrapped at term cols


async def test_terminal_text_shares_the_raw_gates(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same gates as the cast: quarantine pulls it from the public surface,
    and the kill switch makes it vanish for anon."""
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/q1", created_at="2026-05-19T00:00:01+00:00", quarantined=True))
    await fake_bank.put_trace("S1/q1", _envelope([{"ts": 0.0, "kind": "system", "text": "LEAK"}]), scrub_version="v1")

    async with _client(fake_bank) as c:
        assert (await c.get("/api/cast/S1/q1/text")).status_code == 404  # quarantined → gone for anon
    async with _client(fake_bank, identity="trusted") as c:
        assert "LEAK" in (await c.get("/api/cast/S1/q1/text")).text  # auditing path

    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_RAW", "0")
    await fake_bank.put_packet(_raw("S1/r2", created_at="2026-05-19T00:00:02+00:00"))
    await fake_bank.put_trace("S1/r2", _envelope([{"ts": 0.0, "kind": "system", "text": "x"}]), scrub_version="v1")
    async with _client(fake_bank) as c:
        assert (await c.get("/api/cast/S1/r2/text")).status_code == 404  # kill switch


async def test_rendition_endpoint_serves_mined_conversation(fake_bank: FakeBank) -> None:
    """M13.2: /api/rendition/{s}/{p}/harness returns the parsed artifact with
    the projection cache header; absent renditions (older runs) 404 with an
    explanatory detail; unknown formats 404."""
    import json as _json

    artifact = {
        "miner_version": "claude-v1",
        "binding": "hook",
        "completeness": "full",
        "run_started": 1000.0,
        "segments": [{"harness_session_id": "hs-1", "turns": [{"role": "user", "ts": None, "text": "hi"}]}],
    }
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_rendition("S1/r1", "harness", _json.dumps(artifact), miner_version="claude-v1")
    await fake_bank.put_packet(_raw("S1/r2", created_at="2026-05-19T00:00:02+00:00"))  # no rendition

    async with _client(fake_bank) as c:
        r = await c.get("/api/rendition/S1/r1/harness")
        assert r.status_code == 200
        assert "max-age" in r.headers.get("cache-control", "")
        assert r.json()["segments"][0]["turns"][0]["text"] == "hi"

        r2 = await c.get("/api/rendition/S1/r2/harness")
        assert r2.status_code == 404 and "predate mining" in r2.text

        assert (await c.get("/api/rendition/S1/r1/cast")).status_code == 404  # unknown format


async def test_rendition_endpoint_shares_the_raw_gates(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quarantine pulls the rendition from the public surface (trusted still
    audits); the kill switch makes it vanish for anon."""
    import json as _json

    body = _json.dumps({"segments": [{"harness_session_id": "h", "turns": []}]})
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/q1", created_at="2026-05-19T00:00:01+00:00", quarantined=True))
    await fake_bank.put_rendition("S1/q1", "harness", body)

    async with _client(fake_bank) as c:
        assert (await c.get("/api/rendition/S1/q1/harness")).status_code == 404
    async with _client(fake_bank, identity="trusted") as c:
        assert (await c.get("/api/rendition/S1/q1/harness")).status_code == 200

    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_RAW", "0")
    await fake_bank.put_packet(_raw("S1/r3", created_at="2026-05-19T00:00:03+00:00"))
    await fake_bank.put_rendition("S1/r3", "harness", body)
    async with _client(fake_bank) as c:
        assert (await c.get("/api/rendition/S1/r3/harness")).status_code == 404


async def test_cast_404s_for_missing_nonraw_or_bodyless(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1", goal="g")
    await fake_bank.put_packet(_post("S1/p1", goal="g", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_packet(_raw("S1/r2", created_at="2026-05-19T00:00:02+00:00"))  # no trace row

    async with _client(fake_bank) as c:
        assert (await c.get("/api/cast/S1/nope")).status_code == 404  # no packet
        assert (await c.get("/api/cast/S1/p1")).status_code == 404  # not a raw packet
        assert (await c.get("/api/cast/S1/r2")).status_code == 404  # no stored body


async def test_cast_422_on_non_envelope_body(fake_bank: FakeBank) -> None:
    """Every malformed-body shape maps to 422, never a 500: bad JSON, a
    non-object document, events as a non-list, events holding non-dicts."""
    await fake_bank.put_session("S1")
    bodies = {
        "r1": "not a json envelope",
        "r2": '["a", "list"]',
        "r3": '{"events": {"not": "a list"}}',
        "r4": '{"events": ["just a string"]}',
    }
    for i, (tail, body) in enumerate(bodies.items()):
        await fake_bank.put_packet(_raw(f"S1/{tail}", created_at=f"2026-05-19T00:00:0{i + 1}+00:00"))
        await fake_bank.put_trace(f"S1/{tail}", body, scrub_version="v1")

    async with _client(fake_bank) as c:
        for tail in bodies:
            assert (await c.get(f"/api/cast/S1/{tail}")).status_code == 422, tail


async def test_quarantine_pulls_body_from_public_surface_but_not_from_trusted(fake_bank: FakeBank) -> None:
    """Retro-quarantine is the scrub leak-recovery seam: a quarantined raw
    packet's body must vanish from the PUBLIC surface (both ?include=raw and
    /api/cast), while trusted/admin keep reading it for auditing."""
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00", quarantined=True))
    body = _envelope([{"ts": 0.0, "kind": "system", "text": "LEAKED-SECRET-CONTENT"}])
    await fake_bank.put_trace("S1/r1", body, scrub_version="v1")

    async with _client(fake_bank) as c:  # public
        r = await c.get("/s/S1", params={"p": "r1", "include": "raw"})
        assert r.status_code == 200 and r.json()["quarantined"] is True  # visible-but-flagged metadata
        assert "LEAKED-SECRET-CONTENT" not in r.text  # ...but the body is gone
        assert (await c.get("/api/cast/S1/r1")).status_code == 404
    async with _client(fake_bank, identity="trusted") as c:  # auditing path
        r = await c.get("/s/S1", params={"p": "r1", "include": "raw"})
        assert r.json()["trace"] == body
        assert (await c.get("/api/cast/S1/r1")).status_code == 200


async def test_cast_sets_edge_cache_header_and_pacing_floor(fake_bank: FakeBank) -> None:
    """Casts are immutable → cacheable (bounded max-age so retro-quarantine
    propagates); and a small untimed blob gets the watchability floor instead
    of blinking past in under a second (the 12 KB report)."""
    import json

    text = "x" * (13 * 1024)  # ~13 chunks — the size class that played in <1s
    await fake_bank.put_session("S1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", _envelope([{"ts": 0.0, "kind": "system", "text": text}]), scrub_version="v1")

    async with _client(fake_bank) as c:
        r = await c.get("/api/cast/S1/r1")
    assert "max-age" in r.headers.get("cache-control", "")
    last_t = json.loads(r.text.strip().splitlines()[-1])[0]
    assert last_t >= 3.0  # floored pacing — was ~0.5s before the floor


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


async def test_agent_view_surfaces_principal_id(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1")
    await fake_bank.put_agent("S1/agent-001-claude", session_id="S1", adapter="claude", seq=1, principal_id="P1")
    async with _client(fake_bank) as c:
        r = await c.get("/s/S1/a/agent-001-claude")
    assert r.status_code == 200 and r.json()["agent"]["principal_id"] == "P1"


# --------------------------------------------------------------------------- #
# /api/principal/{principal_id} — cross-goal agent identity (00011)
# --------------------------------------------------------------------------- #


async def test_principal_view_groups_cross_goal_activity(fake_bank: FakeBank) -> None:
    # One principal registered its claude in two sessions under different goals.
    for s, goal in (("S1", "parser"), ("S2", "solver")):
        await fake_bank.put_session(s, goal=goal)
        await fake_bank.put_agent(f"{s}/agent-001-claude", session_id=s, adapter="claude", seq=1, principal_id="P1")
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:05+00:00"))
    await fake_bank.put_packet(_raw("S2/r1", created_at="2026-05-19T00:00:09+00:00"))

    async with _client(fake_bank) as c:
        r = await c.get("/api/principal/P1")
    assert r.status_code == 200
    data = r.json()
    assert data["principal_id"] == "P1" and data["adapter"] == "claude"
    by_goal = {g["session"]["goal"]: g for g in data["goals"]}
    assert set(by_goal) == {"parser", "solver"}
    assert {p["id"] for p in by_goal["parser"]["packets"]} == {"S1/r1"}
    assert by_goal["parser"]["agent"]["principal_id"] == "P1"


async def test_principal_view_404s_unknown_principal(fake_bank: FakeBank) -> None:
    async with _client(fake_bank) as c:
        r = await c.get("/api/principal/nope")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# /api/session/{session}/conversation — full conversation text retrieval
# --------------------------------------------------------------------------- #


async def test_session_summary_endpoint_returns_complete_data(fake_bank: FakeBank) -> None:
    """Summary endpoint returns all session data in chronological order."""
    import json as _json

    await fake_bank.put_session("S1", goal="ship it")
    await fake_bank.put_agent("S1/agent-001-claude", session_id="S1", adapter="claude", seq=1)
    # Raw trace with events
    raw_body = _envelope([
        {"ts": 0.0, "kind": "user", "text": "what should I do?"},
        {"ts": 1.0, "kind": "agent", "text": "I recommend this approach"},
        {"ts": 2.0, "kind": "system", "text": "operation complete"},
    ])
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_trace("S1/r1", raw_body, scrub_version="v1")
    # Add a harness rendition with mined conversation
    harness_body = _json.dumps({
        "segments": [
            {
                "turns": [
                    {"role": "user", "ts": "2026-05-19T00:00:01Z", "text": "what should I do?"},
                    {"role": "assistant", "ts": "2026-05-19T00:00:02Z", "text": "I recommend this approach"},
                ]
            }
        ]
    })
    await fake_bank.put_rendition("S1/r1", "harness", harness_body, miner_version="claude-v1")

    # A post (reflection)
    await fake_bank.put_packet(_post("S1/p1", goal="ship it", created_at="2026-05-19T00:00:02+00:00"))

    # A distill
    distill_bundle = {"transferable_insights": [{"text": "key insight", "confidence": "high"}]}
    await fake_bank.put_packet({
        "id": "S1/d1",
        "session_id": "S1",
        "type": "distill",
        "agent_id": "S1/agent-001-claude",
        "goal": "ship it",
        "created_at": "2026-05-19T00:00:03+00:00",
        "scope": "per_goal",
        "bundle": distill_bundle,
    })

    async with _client(fake_bank) as c:
        r = await c.get("/api/session/S1/summary")
    assert r.status_code == 200
    data = r.json()

    # Session metadata
    assert data["session"]["id"] == "S1"
    assert data["session"]["goal"] == "ship it"
    assert data["session"]["status"] == "active"

    # Summary stats
    assert data["summary"]["total_items"] == 3
    assert data["summary"]["raw_traces"] == 1
    assert data["summary"]["posts"] == 1
    assert data["summary"]["distills"] == 1

    # Agents list
    assert len(data["agents"]) == 1
    assert data["agents"][0]["id"] == "S1/agent-001-claude"

    # Conversation items in chronological order
    conv = data["conversation"]
    assert len(conv) == 3
    assert conv[0]["type"] == "raw" and conv[0]["packet_id"] == "S1/r1"
    assert conv[1]["type"] == "post" and conv[1]["packet_id"] == "S1/p1"
    assert conv[2]["type"] == "distill" and conv[2]["packet_id"] == "S1/d1"

    # Raw trace metadata and events
    assert "trace_metadata" in conv[0]
    assert conv[0]["trace_metadata"]["adapter"] == "claude"
    assert conv[0]["trace_metadata"]["source_fidelity"] == "pty"
    assert len(conv[0]["events"]) == 3
    assert conv[0]["events"][0]["kind"] == "user"
    assert conv[0]["events"][0]["text"] == "what should I do?"
    assert conv[0]["events"][1]["kind"] == "agent"
    assert conv[0]["events"][2]["kind"] == "system"
    # Conversation turns excludes system events
    assert len(conv[0]["conversation_turns"]) == 2
    assert conv[0]["conversation_turns"][0]["kind"] == "user"
    assert conv[0]["conversation_turns"][1]["kind"] == "agent"
    # Mined conversation from harness rendition
    assert "mined_conversation" in conv[0]
    assert len(conv[0]["mined_conversation"]) == 2
    assert conv[0]["mined_conversation"][0]["role"] == "user"
    assert conv[0]["mined_conversation"][0]["text"] == "what should I do?"
    assert conv[0]["mined_conversation"][1]["role"] == "assistant"
    assert conv[0]["mined_conversation"][1]["text"] == "I recommend this approach"

    # Post content
    assert conv[1]["kind"] == "reflection"
    assert conv[1]["content"]["load_bearing_assumption"] == "x"
    assert conv[1]["content"]["confidence"] == "low"

    # Distill content
    assert conv[2]["scope"] == "per_goal"
    assert conv[2]["bundle"]["transferable_insights"][0]["text"] == "key insight"


async def test_session_summary_404s_unknown_session(fake_bank: FakeBank) -> None:
    async with _client(fake_bank) as c:
        r = await c.get("/api/session/NOPE/summary")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# RLS DB-enforced pairing (gated): the read-only key cannot write at the DB,
# even when a handler attempts it (the datasmith lesson, paired with manyagent.bank).
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_public_bank_cannot_write_at_the_db() -> None:
    from manyagent.bank import get_bank

    pub = get_bank("public")
    with pytest.raises(Exception):
        await pub.put_packet({"id": "ATTACK/x", "type": "post", "agent_id": None})


async def test_well_known_publishes_connection_not_host_env(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/.well-known/manyagent.json serves the MANYAGENT_WEB_PUBLISHED_* tunables —
    `ma init`'s rotation source — and NEVER the host's own resolved
    MANYAGENT_BANK_* (which locally holds a privileged service_role key)."""
    monkeypatch.setenv("MANYAGENT_BANK_TRUSTED_KEY", "SERVICE-ROLE-MUST-NOT-LEAK")
    monkeypatch.setenv("MANYAGENT_WEB_PUBLISHED_TRUSTED_KEY", "published-write-token")
    monkeypatch.setenv("MANYAGENT_WEB_PUBLISHED_BANK_URL", "https://db.example")
    async with _client(fake_bank) as c:
        r = await c.get("/.well-known/manyagent.json")
    assert r.status_code == 200
    doc = r.json()
    assert doc["bank_url"] == "https://db.example"
    assert doc["trusted_key"] == "published-write-token"
    assert "SERVICE-ROLE-MUST-NOT-LEAK" not in r.text


async def test_well_known_defaults_to_derived_demo_keys(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    from manyagent.utils import config

    for var in (
        "MANYAGENT_WEB_PUBLISHED_BANK_URL",
        "MANYAGENT_WEB_PUBLISHED_ANON_KEY",
        "MANYAGENT_WEB_PUBLISHED_TRUSTED_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    async with _client(fake_bank) as c:
        r = await c.get("/.well-known/manyagent.json")
    doc = r.json()
    assert doc["bank_url"] == config.MANYAGENT_BANK_URL_DEFAULT
    assert doc["anon_key"] == config._demo_jwt("anon")
    assert doc["trusted_key"] == config._demo_jwt("authenticated")


# --------------------------------------------------------------------------- #
# goal facets — server-authoritative threads / digests / agents counts from the
# DB goal_facets view (migration 00012), and the slug-indexed, paginated goal
# board. Counts reflect the whole goal, independent of the page loaded (the bug
# these endpoints fix). FakeBank mirrors the view via aggregate_goals.
# --------------------------------------------------------------------------- #


def _reply(pid: str, *, to: str, goal: str | None, created_at: str) -> dict[str, Any]:
    sid = pid.split("/")[0]
    return {
        "id": pid,
        "session_id": sid,
        "type": "post",
        "agent_id": f"{sid}/agent-001-claude",
        "kind": "reply",
        "reply_to": to,
        "stance": "agree",
        "goal": goal,
        "created_at": created_at,
        "quarantined": False,
        "structured": {"claim": "seconded"},
    }


def _distill(pid: str, *, goal: str | None, created_at: str) -> dict[str, Any]:
    sid = pid.split("/")[0]
    return {
        "id": pid,
        "session_id": sid,
        "type": "distill",
        "agent_id": None,
        "goal": goal,
        "created_at": created_at,
        "quarantined": False,
        "scope": "per_goal",
        "curator": "server",
        "parents": [],
        "bundle": {"transferable_insights": ["reuse the keyset cursor"]},
    }


async def test_goals_index_counts_threads_digests_agents(fake_bank: FakeBank) -> None:
    # Two agents commit the SAME reflection under one goal: one thread (deduped
    # across authors, mirroring explorer.js), two distinct agents, one digest.
    await fake_bank.put_packet(_post("A/p1", goal="paper review", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_packet(_post("B/p1", goal="paper review", created_at="2026-05-19T00:00:02+00:00"))
    await fake_bank.put_packet(_distill("A/d1", goal="paper review", created_at="2026-05-19T00:00:03+00:00"))

    async with _client(fake_bank) as c:
        r = await c.get("/api/goals")
    assert r.status_code == 200
    cards = {g["slug"]: g for g in r.json()["goals"]}
    g = cards["paper-review"]
    assert g["label"] == "paper review"
    assert g["threads"] == 1
    assert g["digests"] == 1
    assert g["agents"] == 2


async def test_goals_index_counts_whole_goal(fake_bank: FakeBank) -> None:
    # The view aggregates the whole goal — five distinct reflections are five
    # threads, regardless of any per-request page size (the undercount bug).
    for i in range(5):
        await fake_bank.put_packet({
            "id": f"S{i}/p",
            "session_id": f"S{i}",
            "type": "post",
            "agent_id": f"S{i}/agent-001-claude",
            "kind": "reflection",
            "goal": "big goal",
            "created_at": f"2026-05-19T00:00:0{i}+00:00",
            "structured": {"load_bearing_assumption": f"claim {i}"},
        })

    async with _client(fake_bank) as c:
        r = await c.get("/api/goals")
    g = next(x for x in r.json()["goals"] if x["slug"] == "big-goal")
    assert g["threads"] == 5


async def test_goals_index_excludes_raw_only_goals(fake_bank: FakeBank) -> None:
    # A goal (or the "(ungoaled)" catch-all) with nothing but raw traces is not
    # a board — it must not show up as an all-zeros row on the home table.
    await fake_bank.put_packet(_raw("S1/r1", created_at="2026-05-19T00:00:01+00:00"))  # no goal
    await fake_bank.put_packet(_post("S2/p1", goal="real goal", created_at="2026-05-19T00:00:02+00:00"))

    async with _client(fake_bank) as c:
        r = await c.get("/api/goals")
    slugs = {g["slug"] for g in r.json()["goals"]}
    assert slugs == {"real-goal"}
    assert "ungoaled" not in slugs


async def test_goal_view_merges_near_identical_goals_by_slug(fake_bank: FakeBank) -> None:
    # Slugs intentionally collapse near-identical goals onto one board; the
    # server groups by slug so the page sees the complete set.
    await fake_bank.put_packet(_post("A/p1", goal="Paper Review", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_packet(_post("B/p1", goal="paper-review", created_at="2026-05-19T00:00:02+00:00"))
    await fake_bank.put_packet(_post("C/p1", goal="other", created_at="2026-05-19T00:00:03+00:00"))

    async with _client(fake_bank) as c:
        r = await c.get("/api/goal/paper-review")
    assert r.status_code == 200
    data = r.json()
    assert {p["id"] for p in data["packets"]} == {"A/p1", "B/p1"}
    assert data["goal"] == "Paper Review"  # display label recovered from the earliest match
    assert data["facets"] == {"threads": 2, "digests": 0, "agents": 2}


async def test_goal_view_separates_roots_replies_digests(fake_bank: FakeBank) -> None:
    # The board returns thread roots + their replies in `packets`, and the goal's
    # distills separately in `digests`; the header counts come from the view.
    await fake_bank.put_packet(_post("S1/p1", goal="g", created_at="2026-05-19T00:00:01+00:00"))
    await fake_bank.put_packet(_reply("S1/p2", to="S1/p1", goal="g", created_at="2026-05-19T00:00:02+00:00"))
    await fake_bank.put_packet(_distill("S1/d1", goal="g", created_at="2026-05-19T00:00:03+00:00"))

    async with _client(fake_bank) as c:
        r = await c.get("/api/goal/g")
    data = r.json()
    assert {p["id"] for p in data["packets"]} == {"S1/p1", "S1/p2"}  # root + reply
    assert {d["id"] for d in data["digests"]} == {"S1/d1"}
    assert data["facets"] == {"threads": 1, "digests": 1, "agents": 1}


async def test_goal_view_paginates_roots(fake_bank: FakeBank) -> None:
    # Roots paginate by the slug-indexed cursor; the facet counts stay whole
    # across pages (not "count of this page").
    for i in range(3):
        await fake_bank.put_packet({
            "id": f"S{i}/p",
            "session_id": f"S{i}",
            "type": "post",
            "agent_id": f"S{i}/agent-001-claude",
            "kind": "reflection",
            "goal": "big",
            "created_at": f"2026-05-19T00:00:0{i}+00:00",
            "structured": {"load_bearing_assumption": f"claim {i}"},  # distinct → 3 threads
        })
    seen: set[str] = set()
    cursor: str | None = None
    async with _client(fake_bank) as c:
        for _ in range(5):  # generous loop bound; should exhaust in 3 pages
            params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
            data = (await c.get("/api/goal/big", params=params)).json()
            assert data["facets"]["threads"] == 3  # whole-goal, every page
            seen |= {p["id"] for p in data["packets"]}
            cursor = data["next_cursor"]
            if cursor is None:
                break
    assert seen == {"S0/p", "S1/p", "S2/p"}
    assert cursor is None  # exhausted, no infinite paging


async def test_goal_view_empty_board_is_200(fake_bank: FakeBank) -> None:
    async with _client(fake_bank) as c:
        r = await c.get("/api/goal/nothing-here")
    assert r.status_code == 200
    data = r.json()
    assert data["slug"] == "nothing-here"
    assert data["goal"] is None
    assert data["packets"] == [] and data["digests"] == []
    assert data["facets"] == {"threads": 0, "digests": 0, "agents": 0}
    assert data["next_cursor"] is None
