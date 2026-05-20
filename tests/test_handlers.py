"""M11.4 tests for ``oms._handlers`` — the four knowledge-loop verbs.

These moved out of ``oms.cli`` when M11.4 ripped the bash slash subcommands;
the same C1 / retrieval / accept-reject behaviour is now tested by calling
the handlers directly with kwargs (no argparse Namespace). The verbs are
exposed to users **inside the wrapped agent** via the MCP server +
per-adapter skills — this module covers the underlying handler functions
that both the in-agent surface (via ``oms._mcp``) and any future
programmatic caller rely on.

The headline case stays **C1**: a rejected/parser-refused post is NOT
persisted and the record never carries ``preference``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from oms import _handlers as h
from oms.bank import FakeBank


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_HOME", str(tmp_path / ".oms"))
    monkeypatch.delenv("OMS_NONINTERACTIVE", raising=False)
    monkeypatch.delenv("OMS_SESSION", raising=False)
    monkeypatch.setenv("OMS_INSTALL_SKILLS", "deny")
    from oms.forum import clear_discuss_gate

    clear_discuss_gate()


class Scripted:
    def __init__(self, *responses: str) -> None:
        self._r = list(responses)
        self.out: list[str] = []

    def __call__(self, _prompt: str = "") -> str:
        return self._r.pop(0)

    def io(self) -> tuple[Any, Any]:
        return (self, self.out.append)


_GOOD = {
    "load_bearing_assumption": "the tokenize() hot loop recompiled the regex per call; precompiling fixed it",
    "evidence": "verbatim from trace: 'cumtime 4.2s in tokenize()'",
    "evidence_ref": None,
    "proposed_next": "hoist the compiled pattern to scanner.py module scope",
    "predicted_outcome": "parse throughput ~1.8x; test_parse_speed passes",
    "confidence": "medium",
}


class FakeModel:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, _prompt: str, *, max_tokens: int | None = None) -> str:
        return self.payload


class FakeAdapter:
    name = "claude"
    binary = "claude"

    def __init__(self, payload: str = "") -> None:
        self._model = FakeModel(payload) if payload else None
        self.injected: list[str] = []

    def distill_model(self) -> Any:
        return self._model

    def inject(self, context: str) -> None:
        self.injected.append(context)

    def is_available(self) -> bool:
        return True


def _patch_adapter(monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: adapter)


async def _seed_session(bank: FakeBank, *, sid: str = "S1", goal: str | None = "speed") -> None:
    await bank.put_session(sid, goal=goal)
    from oms.cli import _write_active

    _write_active(sid)


# --------------------------------------------------------------------------- #
# C1: rejected / parser-refused /self-distill post is NEVER persisted
# --------------------------------------------------------------------------- #


async def test_c1_parser_refused_post_not_persisted(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_session(fake_bank)
    bad = dict(_GOOD)
    del bad["proposed_next"]  # missing required field
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(bad)))
    s = Scripted("y", "skip")
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=s.io())
    assert rc == 1  # parser refused
    assert await fake_bank.list_packets(type="post") == []  # NOT persisted (C1)


async def test_c1_human_reject_not_persisted_no_preference(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    s = Scripted("n")  # reject
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=s.io())
    assert rc == 1
    posts = await fake_bank.list_packets(type="post")
    assert posts == []  # NOT persisted, no `preference` field anywhere (C1)


async def test_self_distill_accept_stores_post_with_star_no_preference(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    s = Scripted("y", "4")
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=s.io())
    assert rc == 0
    [p] = await fake_bank.list_packets(type="post")
    assert p["rating"] == 4 and p["kind"] == "reflection"
    assert "preference" not in p or p.get("preference") is None  # C1


async def test_noninteractive_self_distill_accepts_unrated(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMS_NONINTERACTIVE", "1")
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted().io())
    assert rc == 0  # auto-accept under noninteractive (parser already gated quality)
    [p] = await fake_bank.list_packets(type="post")
    assert p.get("rating") is None  # noninteractive ⇒ unrated


# --------------------------------------------------------------------------- #
# /cross-distill — the exact zero-posts sentinel
# --------------------------------------------------------------------------- #


async def test_cross_distill_zero_posts_exact_sentinel(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank)
    s = Scripted()
    rc = await h.do_cross_distill(bank=fake_bank, io=s.io())
    assert rc == 1
    assert s.out[-1] == "Run /self-distill first!"  # the exact text


# --------------------------------------------------------------------------- #
# /inject — quarantine refusal + preview + ledger + noninteractive deny
# --------------------------------------------------------------------------- #


async def _seed_distill(bank: FakeBank, *, quarantined: bool = False) -> str:
    pid = "curator/abc123"
    await bank.put_packet({
        "id": pid,
        "type": "distill",
        "agent_id": "curator",
        "scope": "per_goal",
        "goal": "speed",
        "bundle": {"confirmed_constraints": [{"text": "x", "applies_when": "a", "does_not_apply_when": "b"}]},
        "parents": ["S1/p1"],
        "quarantined": quarantined,
    })
    return pid


async def test_inject_quarantined_refused_before_preview(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank)
    pid = await _seed_distill(fake_bank, quarantined=True)
    s = Scripted()
    rc = await h.do_inject(packet=f"@{pid}", bank=fake_bank, io=s.io())
    assert rc == 1
    assert any("quarantined" in line for line in s.out)
    assert "--- inject preview ---" not in s.out  # refused BEFORE preview


async def test_inject_preview_then_yes_writes_ledger_row(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank)
    pid = await _seed_distill(fake_bank)
    s = Scripted("y")
    rc = await h.do_inject(packet=pid, bank=fake_bank, io=s.io())
    assert rc == 0
    [row] = await fake_bank.list_injections()
    assert row["packet_id"] == pid and row["target_session_id"] == "S1"


async def test_inject_noninteractive_denied_no_ledger(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMS_NONINTERACTIVE", "1")
    await _seed_session(fake_bank)
    pid = await _seed_distill(fake_bank)
    s = Scripted()
    rc = await h.do_inject(packet=pid, bank=fake_bank, io=s.io())
    assert rc == 1  # deny-by-default under noninteractive (Open-Q §B5)
    assert await fake_bank.list_injections() == []


# --------------------------------------------------------------------------- #
# /discuss — retrieval-before-post, then accept-store-no-preference
# --------------------------------------------------------------------------- #


async def test_discuss_happy_path_stores_reply_no_preference(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    # First a reflection (so /discuss has something to engage).
    await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("y", "4").io())
    # Then the reply.
    rc = await h.do_discuss(adapter="claude", stance="agree", bank=fake_bank, io=Scripted("y").io())
    assert rc == 0
    posts = await fake_bank.list_packets(type="post")
    kinds = {p["kind"] for p in posts}
    assert kinds == {"reflection", "reply"}
    for p in posts:
        assert "preference" not in p or p.get("preference") is None  # C1


async def test_discuss_retrieval_before_post_no_prior_refused(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    s = Scripted()
    rc = await h.do_discuss(adapter="claude", stance="agree", bank=fake_bank, io=s.io())
    assert rc == 1
    assert any("no related posts" in line for line in s.out)
    assert await fake_bank.list_packets(type="post") == []  # nothing persisted (C1)


async def test_discuss_packet_not_in_retrieved_refused(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    # Seed a reflection so retrieve is non-empty.
    await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("y", "4").io())
    # Reference a packet id NOT in the retrieved set.
    s = Scripted()
    rc = await h.do_discuss(
        adapter="claude",
        stance="agree",
        packet="S1/not-retrieved",
        bank=fake_bank,
        io=s.io(),
    )
    assert rc == 1
    assert any("not among the retrieved posts" in line for line in s.out)
