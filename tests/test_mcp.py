"""M11 tests for ``oms._mcp`` — the in-agent MCP server.

The load-bearing case is **C1** at the MCP layer: ``commit_post`` is the only
tool that persists a post, and it refuses a parser-failed payload — so a host
LLM that drafts but does not commit (because the user rejected) persists
nothing. The draft tools (``self_distill_draft`` / ``discuss_draft``) are
pure provisioners; they never persist, so an MCP server never tickles the
Bank unless the host LLM explicitly calls a commit tool.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from oms._mcp import (
    commit_post,
    cross_distill,
    discuss_draft,
    inject_commit,
    inject_preview,
    self_distill_draft,
)
from oms.bank import FakeBank
from oms.core import clear_packet_cache


@pytest.fixture(autouse=True)
def _env(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_bank: FakeBank,
) -> FakeBank:
    """Per-test: tmp OMS_HOME, explicit OMS_SESSION='S1', the FakeBank wired
    in as the Bank singleton, the discuss gate and packet cache cleared."""
    monkeypatch.setenv("OMS_HOME", str(tmp_path / ".oms"))
    monkeypatch.setenv("OMS_SESSION", "S1")
    monkeypatch.setattr("oms.bank.get_bank", lambda *a, **k: fake_bank)
    from oms.forum import clear_discuss_gate

    clear_discuss_gate()
    clear_packet_cache()
    return fake_bank


# tools are FastMCP-wrapped; ``.fn`` is the underlying async callable.
def _call(tool: Any, **kwargs: Any) -> Any:
    return tool.fn(**kwargs) if hasattr(tool, "fn") else tool(**kwargs)


_GOOD = {
    "load_bearing_assumption": "the `tokenize()` hot loop recompiled the regex per call; precompiling fixed it",
    "evidence": "verbatim from trace: 'cumtime 4.2s in tokenize()'",
    "evidence_ref": None,
    "proposed_next": "hoist the compiled pattern to scanner.py module scope",
    "predicted_outcome": "parse throughput ~1.8x; test_parse_speed passes",
    "confidence": "medium",
}


async def _seed_session(bank: FakeBank, *, sid: str = "S1", goal: str | None = "speed-things-up") -> None:
    await bank.put_session(sid, goal=goal)


# --------------------------------------------------------------------------- #
# session id resolution: OMS_SESSION env wins; ~/.oms/active fallback; else raise
# --------------------------------------------------------------------------- #


def test_session_id_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from oms._mcp import _session_id

    monkeypatch.setenv("OMS_SESSION", "ENV-WINS")
    assert _session_id() == "ENV-WINS"


def test_session_id_falls_back_to_active_file(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from oms._mcp import _session_id

    monkeypatch.delenv("OMS_SESSION", raising=False)
    monkeypatch.setenv("OMS_HOME", str(tmp_path))
    (tmp_path / "active").write_text("FROM-FILE", encoding="utf-8")
    assert _session_id() == "FROM-FILE"


def test_session_id_raises_when_no_source(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from oms._mcp import _session_id

    monkeypatch.delenv("OMS_SESSION", raising=False)
    monkeypatch.setenv("OMS_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="no active oms session"):
        _session_id()


# --------------------------------------------------------------------------- #
# draft tools never persist
# --------------------------------------------------------------------------- #


async def test_self_distill_draft_returns_substrate_no_persist(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    out = await _call(self_distill_draft, guidance="focus on the hot loop")
    assert out["session"] == "S1" and out["goal"] == "g" and out["kind"] == "reflection"
    assert "instruction_for_host_llm" in out and "commit_post" in out["commit_via"]
    assert await fake_bank.list_packets(type="post") == []  # NEVER persisted by a draft


async def test_discuss_draft_refuses_without_prior_posts(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    out = await _call(discuss_draft, stance="agree")
    assert "error" in out and "self_distill_draft first" in out["error"]
    assert await fake_bank.list_packets(type="post") == []


async def test_discuss_draft_registers_gate_when_prior_exists(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    prior_id = "S1/p1"
    await fake_bank.put_packet({
        "id": prior_id,
        "session_id": "S1",
        "type": "post",
        "agent_id": "S1/mcp",
        "kind": "reflection",
        "goal": "g",
        "structured": _GOOD,
    })
    out = await _call(discuss_draft, stance="agree")
    assert out["kind"] == "reply" and out["stance"] == "agree" and out["reply_to"] == prior_id
    assert prior_id in out["ranked_post_ids"]
    # The retrieval gate is now registered for this session/agent: a subsequent
    # commit_post(reply, reply_to=prior_id) is permitted by oms.forum.
    res = await _call(
        commit_post,
        kind="reply",
        structured=_GOOD,
        reply_to=prior_id,
        stance="agree",
    )
    assert res["ok"] is True and res["kind"] == "reply"


async def test_discuss_draft_rejects_bad_stance(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    with pytest.raises(ValueError, match="bad stance"):
        await _call(discuss_draft, stance="bogus")


# --------------------------------------------------------------------------- #
# commit_post — the gate; C1 is the headline
# --------------------------------------------------------------------------- #


async def test_commit_post_reflection_persists_with_rating_no_preference(
    fake_bank: FakeBank,
) -> None:
    await _seed_session(fake_bank, goal="g")
    res = await _call(commit_post, kind="reflection", structured=_GOOD, rating=4)
    assert res["ok"] is True and res["rating"] == 4 and res["post_id"].startswith("S1/")
    [p] = await fake_bank.list_packets(type="post")
    assert p["rating"] == 4 and p["kind"] == "reflection" and p["goal"] == "g"
    assert "preference" not in p or p.get("preference") is None  # C1: never on a post


async def test_commit_post_parser_refused_not_persisted_c1(fake_bank: FakeBank) -> None:
    """The headline: a draft whose structured payload fails the mechanical
    parser is rejected by ``commit_post`` and the post is NOT persisted —
    exactly the C1 invariant the bash flow guaranteed via ``_emit_post``."""
    await _seed_session(fake_bank, goal="g")
    bad = dict(_GOOD)
    del bad["proposed_next"]  # missing required field → parser refuses
    res = await _call(commit_post, kind="reflection", structured=bad, rating=4)
    assert res["ok"] is False and "parser refused" in res["error"]
    assert await fake_bank.list_packets(type="post") == []  # C1


async def test_commit_post_reply_requires_reply_to_and_stance(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    with pytest.raises(ValueError, match="reply requires reply_to and stance"):
        await _call(commit_post, kind="reply", structured=_GOOD)


async def test_commit_post_rating_bounds(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    for bad in (0, 6, -1, 99):
        with pytest.raises(ValueError, match="rating must be None or 1..5"):
            await _call(commit_post, kind="reflection", structured=_GOOD, rating=bad)


async def test_draft_then_no_commit_persists_nothing(fake_bank: FakeBank) -> None:
    """The whole point of the split: a draft tool call followed by user
    rejection (the host LLM simply doesn't call commit_post) leaves the
    Bank untouched — no need to retroactively delete anything."""
    await _seed_session(fake_bank, goal="g")
    await _call(self_distill_draft, guidance="anything")
    # … host LLM shows the draft, user says no, host LLM does NOT call commit.
    assert await fake_bank.list_packets() == []


# --------------------------------------------------------------------------- #
# cross_distill
# --------------------------------------------------------------------------- #


async def test_cross_distill_no_posts_returns_sentinel(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    out = await _call(cross_distill)
    assert out["ok"] is False and out["error"] == "Run /self-distill first!"


async def test_cross_distill_happy_path(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_session(fake_bank, goal="g")
    # Seed a goal-scoped post the curator can cluster.
    await fake_bank.put_packet({
        "id": "S1/p1",
        "session_id": "S1",
        "type": "post",
        "agent_id": "S1/mcp",
        "kind": "reflection",
        "goal": "g",
        "structured": _GOOD,
    })

    class _M:
        def complete(self, _p: str, *, max_tokens: int | None = None) -> str:
            return json.dumps({
                "confirmed_constraints": [
                    {
                        "text": "precompile a regex used in a hot `tokenize()` loop",
                        "applies_when": "the parser recompiles the same pattern per call",
                        "does_not_apply_when": "patterns used once at startup",
                        "evidence": [{"post_id": "S1/p1", "quote": "recompiled the regex per call"}],
                        "confidence": "medium",
                    }
                ]
            })

    rmod = importlib.import_module("oms.distill.resolve")
    monkeypatch.setattr(rmod, "_discover_local_model", lambda: _M())
    out = await _call(cross_distill)
    assert out["ok"] is True and out["scope"] == "per_goal" and out["goal"] == "g"
    assert out["bundle_id"].startswith("curator/")
    assert out["bucket_counts"]["confirmed_constraints"] == 1


# --------------------------------------------------------------------------- #
# inject_preview is non-destructive; inject_commit is the gate
# --------------------------------------------------------------------------- #


async def _seed_distill(bank: FakeBank, *, quarantined: bool = False) -> str:
    pid = "curator/abc123"
    await bank.put_packet({
        "id": pid,
        "type": "distill",
        "agent_id": "curator",
        "scope": "per_goal",
        "goal": "g",
        "bundle": {"confirmed_constraints": [{"text": "x", "applies_when": "a", "does_not_apply_when": "b"}]},
        "parents": ["S1/p1"],
        "quarantined": quarantined,
    })
    return pid


async def test_inject_preview_returns_preview_no_ledger(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    pid = await _seed_distill(fake_bank)
    out = await _call(inject_preview, packet=f"@{pid}")
    assert out["ok"] is True and out["packet_id"] == pid and "preview" in out
    assert await fake_bank.list_injections() == []  # preview never writes


async def test_inject_preview_quarantined_refused(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    pid = await _seed_distill(fake_bank, quarantined=True)
    out = await _call(inject_preview, packet=pid)
    assert out["ok"] is False and "quarantined" in out["error"]


async def test_inject_commit_writes_ledger_row(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    pid = await _seed_distill(fake_bank)
    out = await _call(inject_commit, packet=pid)
    assert out["ok"] is True and out["target_session"] == "S1"
    [row] = await fake_bank.list_injections()
    assert row["packet_id"] == pid and row["target_session_id"] == "S1"


async def test_inject_commit_quarantined_refused_no_ledger(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank, goal="g")
    pid = await _seed_distill(fake_bank, quarantined=True)
    out = await _call(inject_commit, packet=pid)
    assert out["ok"] is False and "quarantined" in out["error"]
    assert await fake_bank.list_injections() == []
