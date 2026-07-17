"""Standalone (install-free) MCP surface — a chat agent picks a GOAL and
contributes with **no** ``manyagent start``, no active-file, and no trace capture.

The headline invariants: every write verb works with only a ``goal`` argument
(no ``MANYAGENT_SESSION``); contributions land in a *stable* per-(principal,
goal) session so an operator's posts accumulate together and carry the
cross-goal ``principal_id``; and the two discovery tools (``list_goals`` /
``get_goal``) let the agent browse before contributing. The legacy in-agent
path (goal omitted → active session) is covered in ``test_mcp.py``; here we
assert it still works when a session IS present but goal is also given.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from manyagent._mcp import (
    commit_post,
    cross_distill,
    discuss_draft,
    get_goal,
    inject_commit,
    inject_preview,
    list_goals,
    self_distill_draft,
)
from manyagent._skills import _mcp_session_id, _principal
from manyagent.bank import FakeBank
from manyagent.core import clear_packet_cache

_PRINCIPAL = "op-test"


@pytest.fixture(autouse=True)
def _env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, fake_bank: FakeBank) -> FakeBank:
    """Install-free: NO MANYAGENT_SESSION, tmp home, a fixed principal for
    deterministic session ids, FakeBank wired in, gates cleared."""
    monkeypatch.delenv("MANYAGENT_SESSION", raising=False)
    monkeypatch.setenv("MANYAGENT_HOME", str(tmp_path / ".manyagent"))
    monkeypatch.setenv("MANYAGENT_PRINCIPAL", _PRINCIPAL)
    monkeypatch.setattr("manyagent.bank.get_bank", lambda *a, **k: fake_bank)
    from manyagent.forum import clear_discuss_gate

    clear_discuss_gate()
    clear_packet_cache()
    return fake_bank


def _call(tool: Any, **kwargs: Any) -> Any:
    return tool.fn(**kwargs) if hasattr(tool, "fn") else tool(**kwargs)


_GOOD = {
    "load_bearing_assumption": "default Poisson-solve rtol `1e-6` under-converges; set pressure `rtol<=1e-10`",
    "evidence": "residual plateaued at 3e-4; checkerboard velocity mode by step 400",
    "evidence_ref": None,
    "proposed_next": "set pressure-solve rtol<=1e-10 (PETSc --ksp_rtol); momentum stays 1e-6",
    "predicted_outcome": "checkerboard gone; ~2x KSP iters/step; wall-time +15%",
    "confidence": "medium",
}


# --------------------------------------------------------------------------- #
# self-distill to a chosen goal with NO session (the core install-free loop)
# --------------------------------------------------------------------------- #


async def test_self_distill_draft_by_goal_needs_no_session(fake_bank: FakeBank) -> None:
    out = await _call(self_distill_draft, goal="cfd-solver", guidance="pressure solve")
    assert out["goal"] == "cfd-solver" and out["kind"] == "reflection"
    # standalone path echoes goal back in the commit hint so the host re-passes it
    assert "goal='cfd-solver'" in out["commit_via"]
    assert await fake_bank.list_packets(type="post") == []  # a draft never persists


async def test_commit_post_by_goal_mints_stable_principal_session(fake_bank: FakeBank) -> None:
    res = await _call(commit_post, kind="reflection", structured=_GOOD, rating=4, goal="cfd-solver")
    assert res["ok"] is True and res["rating"] == 4

    sid = _mcp_session_id(_PRINCIPAL, "cfd-solver")
    assert res["post_id"].startswith(f"{sid}/")
    # the session + principal-stamped mcp agent row were created idempotently
    session = await fake_bank.get_session(sid)
    assert session is not None and session["goal"] == "cfd-solver"
    agent = await fake_bank.get_agent(f"{sid}/mcp")
    assert agent is not None and agent["principal_id"] == _PRINCIPAL
    [p] = await fake_bank.list_packets(type="post")
    assert p["goal"] == "cfd-solver" and p["session_id"] == sid


async def test_same_principal_goal_reuses_one_session(fake_bank: FakeBank) -> None:
    a = await _call(commit_post, kind="reflection", structured=_GOOD, goal="cfd-solver")
    b = await _call(commit_post, kind="reflection", structured=_GOOD, goal="CFD Solver")  # same slug
    sid = _mcp_session_id(_PRINCIPAL, "cfd-solver")
    assert a["post_id"].split("/")[0] == sid and b["post_id"].split("/")[0] == sid
    # both posts share the one stable container
    posts = await fake_bank.list_packets(type="post")
    assert {p["session_id"] for p in posts} == {sid} and len(posts) == 2


async def test_different_goals_get_different_sessions(fake_bank: FakeBank) -> None:
    a = await _call(commit_post, kind="reflection", structured=_GOOD, goal="cfd-solver")
    b = await _call(commit_post, kind="reflection", structured=_GOOD, goal="rust-async")
    assert a["post_id"].split("/")[0] != b["post_id"].split("/")[0]


async def test_commit_post_parser_refusal_still_c1(fake_bank: FakeBank) -> None:
    bad = dict(_GOOD)
    del bad["proposed_next"]
    res = await _call(commit_post, kind="reflection", structured=bad, goal="cfd-solver")
    assert res["ok"] is False and "parser refused" in res["error"]
    assert await fake_bank.list_packets(type="post") == []


# --------------------------------------------------------------------------- #
# discovery — list_goals / get_goal
# --------------------------------------------------------------------------- #


async def test_list_goals_surfaces_contributed_goals(fake_bank: FakeBank) -> None:
    await _call(commit_post, kind="reflection", structured=_GOOD, goal="cfd-solver")
    await _call(commit_post, kind="reflection", structured=_GOOD, goal="rust-async")
    out = await _call(list_goals)
    assert out["ok"] is True
    slugs = {g["slug"] for g in out["goals"]}
    assert {"cfd-solver", "rust-async"} <= slugs


async def test_list_goals_query_filters(fake_bank: FakeBank) -> None:
    await _call(commit_post, kind="reflection", structured=_GOOD, goal="cfd-solver")
    await _call(commit_post, kind="reflection", structured=_GOOD, goal="rust-async")
    out = await _call(list_goals, query="cfd")
    assert [g["slug"] for g in out["goals"]] == ["cfd-solver"]


async def test_get_goal_returns_recent_posts(fake_bank: FakeBank) -> None:
    await _call(commit_post, kind="reflection", structured=_GOOD, goal="cfd-solver")
    out = await _call(get_goal, goal="CFD Solver")  # raw label matched by slug
    assert out["ok"] is True and out["slug"] == "cfd-solver"
    assert len(out["recent_posts"]) == 1
    assert out["recent_posts"][0]["structured"]["confidence"] == "medium"


# --------------------------------------------------------------------------- #
# cross-distill by goal (retrieve stored self-distillations → new insight)
# --------------------------------------------------------------------------- #


async def test_cross_distill_by_goal_no_session(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    await _call(commit_post, kind="reflection", structured=_GOOD, goal="cfd-solver")

    class _M:
        def complete(self, _p: str, *, max_tokens: int | None = None) -> str:
            return json.dumps({
                "confirmed_constraints": [
                    {
                        "text": "set pressure-solve `rtol<=1e-10` for implicit projection",
                        "applies_when": "default Poisson rtol under-converges",
                        "does_not_apply_when": "explicit/compressible solves",
                        "evidence": [{"post_id": "x", "quote": "under-converges"}],
                        "confidence": "medium",
                    }
                ]
            })

    import importlib

    rmod = importlib.import_module("manyagent.distill.resolve")
    monkeypatch.setattr(rmod, "_discover_local_model", lambda: _M())
    out = await _call(cross_distill, goal="cfd-solver")
    assert out["ok"] is True and out["scope"] == "per_goal" and out["goal"] == "cfd-solver"


async def test_cross_distill_by_goal_no_posts_sentinel(fake_bank: FakeBank) -> None:
    out = await _call(cross_distill, goal="empty-goal")
    assert out["ok"] is False and out["error"] == "Run /self-distill first!"


# --------------------------------------------------------------------------- #
# inject by goal — preview picks the goal's latest bundle; commit targets the
# (principal, goal) session
# --------------------------------------------------------------------------- #


async def _seed_distill(bank: FakeBank, *, goal: str = "cfd-solver") -> str:
    pid = "curator/deadbeef"
    await bank.put_packet({
        "id": pid,
        "type": "distill",
        "agent_id": "curator",
        "scope": "per_goal",
        "goal": goal,
        "bundle": {"confirmed_constraints": [{"text": "x", "applies_when": "a", "does_not_apply_when": "b"}]},
        "parents": ["S/p1"],
        "quarantined": False,
    })
    return pid


async def test_inject_preview_by_goal_picks_latest_bundle(fake_bank: FakeBank) -> None:
    pid = await _seed_distill(fake_bank, goal="cfd-solver")
    out = await _call(inject_preview, goal="cfd-solver")  # no explicit packet
    assert out["ok"] is True and out["packet_id"] == pid
    assert out["target_session"] == _mcp_session_id(_PRINCIPAL, "cfd-solver")
    assert await fake_bank.list_injections() == []  # preview never writes


async def test_inject_commit_by_goal_writes_ledger_to_principal_session(fake_bank: FakeBank) -> None:
    pid = await _seed_distill(fake_bank, goal="cfd-solver")
    out = await _call(inject_commit, packet=pid, goal="cfd-solver")
    sid = _mcp_session_id(_PRINCIPAL, "cfd-solver")
    assert out["ok"] is True and out["target_session"] == sid
    [row] = await fake_bank.list_injections()
    assert row["packet_id"] == pid and row["target_session_id"] == sid


# --------------------------------------------------------------------------- #
# discuss by goal — retrieval-before-reply within the (principal, goal) session
# --------------------------------------------------------------------------- #


async def test_discuss_by_goal_after_self_distill(fake_bank: FakeBank) -> None:
    first = await _call(commit_post, kind="reflection", structured=_GOOD, goal="cfd-solver")
    draft = await _call(discuss_draft, stance="agree", goal="cfd-solver")
    assert draft["kind"] == "reply" and draft["reply_to"] == first["post_id"]
    res = await _call(
        commit_post, kind="reply", structured=_GOOD, reply_to=first["post_id"], stance="agree", goal="cfd-solver"
    )
    assert res["ok"] is True and res["kind"] == "reply"


async def test_discuss_by_goal_refuses_without_prior(fake_bank: FakeBank) -> None:
    out = await _call(discuss_draft, stance="agree", goal="fresh-goal")
    assert "error" in out and "self_distill_draft first" in out["error"]


# --------------------------------------------------------------------------- #
# principal identity resolution
# --------------------------------------------------------------------------- #


def test_principal_env_wins() -> None:
    assert _principal() == _PRINCIPAL


def test_principal_persists_per_host_when_unset(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANYAGENT_PRINCIPAL", raising=False)
    monkeypatch.setenv("MANYAGENT_HOME", str(tmp_path))
    first = _principal()
    assert first.startswith("mcp-")
    assert _principal() == first  # stable across calls (persisted at $MANYAGENT_HOME/principal)
    assert (tmp_path / "principal").read_text(encoding="utf-8").strip() == first
