"""M4 tests for manyagent.capture — conformance, the security-critical scrub
(highest-priority test in the project), fidelity-aware bounding, and the
validate→scrub→bound→persist round-trip (manyagent.capture.md Verification)."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from manyagent.bank import FakeBank
from manyagent.capture import (
    SCRUB_VERSION,
    CanonicalTrace,
    ConformanceError,
    ScrubReport,
    TraceEvent,
    bound,
    persist,
    scrub,
    validate,
)
from manyagent.capture.bound import _MARKER_RESERVE


def _trace(events: list[TraceEvent], *, fidelity: str = "structured", **kw: object) -> CanonicalTrace:
    base: dict[str, object] = {
        "session_id": "S",
        "agent_id": "S/agent-001-claude",
        "adapter": "claude",
        "events": events,
        "source_fidelity": fidelity,
        "bytes_in": sum(len(e.text.encode()) for e in events),
    }
    base.update(kw)
    return CanonicalTrace(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# conformance — the adapter author's contract, enforced at runtime
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fidelity", ["structured", "pty"])
def test_validate_accepts_each_fidelity_unchanged(fidelity: str) -> None:
    t = _trace([TraceEvent(0.0, "agent", "hi")], fidelity=fidelity)
    assert validate(t) is t
    assert t.source_fidelity == fidelity  # first-class, preserved


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"session_id": ""}, "session_id is required"),
        ({"session_id": "a/b"}, "must not contain"),
        ({"agent_id": ""}, "agent_id is required"),
        ({"adapter": ""}, "adapter is required"),
        ({"source_fidelity": "telepathy"}, "source_fidelity"),
    ],
)
def test_validate_rejects_nonconformant(kw: dict[str, object], match: str) -> None:
    with pytest.raises(ConformanceError, match=match):
        validate(_trace([TraceEvent(0.0, "agent", "x")], **kw))


def test_validate_rejects_bad_event_kind() -> None:
    with pytest.raises(ConformanceError, match=r"events\[1\].kind"):
        validate(_trace([TraceEvent(0.0, "agent", "ok"), TraceEvent(1.0, "ramble", "bad")]))


# --------------------------------------------------------------------------- #
# scrub — SECURITY-CRITICAL (public corpus): secrets gone from body AND report
# --------------------------------------------------------------------------- #

# Credential-shaped values are assembled from fragments so this source file
# contains no contiguous secret literal (GitHub secret-scanning push protection
# blocks those). The runtime values still have the exact real shapes the
# scrubber patterns match.
_JWT = ".".join([
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0",
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
])

_SECRETS: list[tuple[str, str]] = [
    ("sk-ant-api03-AbCdEf0123456789ghIJklMNop", "anthropic"),
    ("sk-proj-AbCdEf0123456789ghIJklMNopQR", "openai"),
    ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
    ("Authorization: Bearer xyz-abc-def-7788", "bearer"),
    ("MY_API_KEY=supersecret-value-12345", "env_kv"),
    # realistic provider shapes, fragment-assembled (see note above)
    ("ghp" + "_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "github"),
    ("AI" + "zaSyD-AbCdEfGhIjKlMnOpQrStUvWxYz01234", "google_api"),
    ("xox" + "b-123456789012-123456789012-AbCdEfGhIjKlMnOpQrStUvWx", "slack"),
    (_JWT, "jwt"),
]


def test_injected_credentials_fully_redacted_and_report_is_leak_free() -> None:
    t = _trace([TraceEvent(float(i), "agent", f"step using {s}") for i, (s, _) in enumerate(_SECRETS)])
    scrubbed, report = scrub(t)

    body = json.dumps([asdict(e) for e in scrubbed.events])
    leak_surface = repr(report) + json.dumps(asdict(report), default=str) + json.dumps(asdict(scrubbed.scrub_report))
    for secret, _kind in _SECRETS:
        assert secret not in body, f"{secret!r} survived in the trace body"
        assert secret not in leak_surface, f"{secret!r} leaked into the ScrubReport"
        # the *secret value* must be gone even where a prefix is preserved
        value = secret.split("=", 1)[1] if "=" in secret else secret.split()[-1]
        assert value not in body and value not in leak_surface

    assert report.total() >= len(_SECRETS)
    assert scrubbed.scrub_report is report  # stamped onto the trace
    # report carries only kind→count, never matched text
    assert set(report.counts) <= {k for _, k in _SECRETS}
    assert all(isinstance(v, int) for v in report.counts.values())


def test_scrub_is_pure_input_untouched() -> None:
    original = TraceEvent(0.0, "agent", "token sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA done")
    t = _trace([original])
    scrub(t)
    assert original.text == "token sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA done"  # not mutated in place


def test_scrub_clean_trace_is_noop() -> None:
    t = _trace([TraceEvent(0.0, "user", "please optimize the parser")])
    scrubbed, report = scrub(t)
    assert scrubbed.events[0].text == "please optimize the parser"
    assert report.total() == 0


def test_json_form_secret_value_redacted() -> None:
    # A secret whose value matches no provider shape — only the JSON
    # ``"KEY": "value"`` env_kv form catches it (structured tool output, jq, json.dumps).
    raw = '{"DATABASE_PASSWORD": "hunter2-s3cr3t-not-a-known-shape", "note": "kept"}'
    scrubbed, report = scrub(_trace([TraceEvent(0.0, "agent", raw)]))
    body = scrubbed.events[0].text
    assert "hunter2-s3cr3t-not-a-known-shape" not in body
    assert "kept" in body  # non-secret JSON content is preserved
    assert report.counts.get("env_kv", 0) >= 1


def test_jwt_in_authorization_header_redacted() -> None:
    jwt = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJhYmMifQ", "s5H8Qc0d5mE6mZ7nQ0wL3aB2cD4eF6gH8iJ0kL2mN4o"])
    scrubbed, report = scrub(_trace([TraceEvent(0.0, "agent", f"curl -H 'Authorization: Bearer {jwt}'")]))
    assert jwt not in scrubbed.events[0].text
    assert report.total() >= 1


# --------------------------------------------------------------------------- #
# bounding — the remaining hard problem (size budget enforced HERE)
# --------------------------------------------------------------------------- #


def test_within_budget_trace_passes_through_only_stamping_bytes_out() -> None:
    t = _trace([TraceEvent(0.0, "agent", "small")])
    out = bound(t, max_bytes=1024)
    assert [e.text for e in out.events] == ["small"]
    assert out.bytes_out == len("small") and out.bytes_in == t.bytes_in
    assert not any(e.truncated for e in out.events)


def test_50mb_pty_trace_bounded_within_budget_and_truncated() -> None:
    big = "x" * 50_000_000  # synthetic 50 MB PTY tee (manyagent.capture.md Verification)
    t = _trace([TraceEvent(0.0, "system", big)], fidelity="pty", bytes_in=len(big))
    budget = 2 * 1024 * 1024
    out = bound(t, max_bytes=budget)

    assert out.bytes_out <= budget
    assert out.bytes_out > 0  # map-reduce result is non-empty
    assert out.events and all(e.truncated for e in out.events)
    assert out.bytes_in == 50_000_000  # adapter-set, preserved
    assert any("elided" in e.text for e in out.events)  # the gap is explicit, not silent


def test_structured_bounding_keeps_head_and_tail_with_marker() -> None:
    events = [TraceEvent(float(i), "agent", f"event-{i}-" + "y" * 400) for i in range(200)]
    t = _trace(events)
    budget = 4096
    out = bound(t, max_bytes=budget)

    assert out.bytes_out <= budget
    texts = [e.text for e in out.events]
    assert any("event-0-" in x for x in texts)  # head retained
    assert any("event-199-" in x for x in texts)  # tail retained
    markers = [e for e in out.events if "elided" in e.text]
    assert len(markers) == 1 and markers[0].truncated and markers[0].kind == "system"


def test_pty_trace_without_tool_structure_does_not_crash() -> None:
    # No tool_call/tool_result events at all — the degradation path must hold.
    t = _trace([TraceEvent(0.0, "system", "raw terminal bytes\n" * 5)], fidelity="pty")
    out = bound(t, max_bytes=64)
    assert out.bytes_out <= 64 and out.events  # bounded, non-empty, no exception
    scrubbed, _ = scrub(t)  # scrubbing a pty blob is also fine
    assert scrubbed.events


# --------------------------------------------------------------------------- #
# end-to-end persist round-trip through the Bank
# --------------------------------------------------------------------------- #


async def test_persist_writes_raw_packet_and_scrubbed_trace(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    t = _trace([
        TraceEvent(0.0, "user", "optimize"),
        TraceEvent(1.0, "agent", "leaked sk-proj-ZZZZZZZZZZZZZZZZZZZZZZZZ here"),
    ])
    pid = await persist(t, bank=fake_bank)

    assert pid.startswith("S/")
    pkt = await fake_bank.get_packet(pid)
    assert pkt is not None and pkt["type"] == "raw" and pkt["agent_id"] == "S/agent-001-claude"

    tr = await fake_bank.get_trace(pid)
    assert tr is not None
    assert tr["scrub_version"] == SCRUB_VERSION and tr["complete"] is True
    assert "sk-proj-ZZZZZZZZZZZZZZZZZZZZZZZZ" not in tr["body"]  # scrubbed before put_trace
    payload = json.loads(tr["body"])
    assert payload["source_fidelity"] == "structured"
    assert payload["scrub_report"]["counts"].get("openai") == 1


async def test_persist_incomplete_capture_is_flagged(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    pid = await persist(_trace([TraceEvent(0.0, "agent", "killed mid-run")]), bank=fake_bank, complete=False)
    tr = await fake_bank.get_trace(pid)
    assert tr is not None and tr["complete"] is False  # distillable but flagged


async def test_persist_round_trips_via_core_packet(fake_bank: FakeBank) -> None:
    from manyagent.core import Packet, clear_packet_cache

    clear_packet_cache()
    await fake_bank.put_session("S")
    pid = await persist(_trace([TraceEvent(0.0, "agent", "hi")], fidelity="pty"), bank=fake_bank)
    pkt = await Packet.fetch(pid, bank=fake_bank)
    assert pkt.type == "raw" and pkt.session_id == "S" and pkt.agent is not None


async def test_traces_body_roundtrips_to_canonicaltrace(fake_bank: FakeBank) -> None:
    """M7 replays distillation from ``traces.body``; the serialization must be
    loss-free. Reconstruct the dataclasses from the stored JSON and assert
    re-serializing yields byte-identical output (bijective)."""
    from manyagent.capture import _serialize

    await fake_bank.put_session("S")
    src = _trace(
        [
            TraceEvent(0.0, "user", "optimize"),
            TraceEvent(1.5, "tool_call", "grep -n foo", truncated=True),
            TraceEvent(2.0, "agent", "leaked sk-proj-QQQQQQQQQQQQQQQQQQQQQQQQ done"),
        ],
        fidelity="structured",
    )
    pid = await persist(src, bank=fake_bank)
    tr = await fake_bank.get_trace(pid)
    assert tr is not None
    body = json.loads(tr["body"])

    rebuilt = CanonicalTrace(
        session_id=body["session_id"],
        agent_id=body["agent_id"],
        adapter=body["adapter"],
        events=[TraceEvent(**e) for e in body["events"]],
        source_fidelity=body["source_fidelity"],
        scrub_report=ScrubReport(counts=body["scrub_report"]["counts"]),
        bytes_in=body["bytes_in"],
        bytes_out=body["bytes_out"],
    )
    assert json.loads(_serialize(rebuilt)) == body  # bijective: no field lost
    assert rebuilt.events[1].truncated is True  # per-event flags survive
    assert rebuilt.scrub_report.counts.get("openai") == 1  # scrub metadata survives
    assert "sk-proj-QQQQQQQQQQQQQQQQQQQQQQQQ" not in tr["body"]  # still scrubbed


def test_marker_reserve_is_sane() -> None:
    assert 0 < _MARKER_RESERVE < 2 * 1024 * 1024  # leaves room for the elision marker


async def test_persist_serializes_terminal_geometry(fake_bank: FakeBank) -> None:
    """M12.2: the envelope carries `term` (cols/rows/resizes) end-to-end —
    the cast rendition needs the real geometry the TUI laid itself out for.
    Legacy traces (term=None) keep the old shape."""
    import json

    await fake_bank.put_session("S")
    term = {"cols": 159, "rows": 37, "resizes": [[3.5, 100, 37]]}
    pid = await persist(_trace([TraceEvent(0.0, "system", "x")], fidelity="pty", term=term), bank=fake_bank)
    tr = await fake_bank.get_trace(pid)
    assert tr is not None
    body = json.loads(tr["body"])
    assert body["term"] == term

    pid2 = await persist(_trace([TraceEvent(0.0, "system", "y")], fidelity="pty"), bank=fake_bank)
    tr2 = await fake_bank.get_trace(pid2)
    assert tr2 is not None
    assert json.loads(tr2["body"])["term"] is None
