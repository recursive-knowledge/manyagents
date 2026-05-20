"""M7 tests for oms.distill — the curator (oms.distill.md Verification).

Covers: mechanical bundle validation **port + harden (C3)** (invented
post_id / paraphrase / unbounded `does_not_apply_when` / ≤5-per-bucket /
shared anti-meta code / Evidence string-id remap / recurrence promotion);
no-carry-forward + per/cross independence; outcome weighting; hybrid
``local|server|auto`` resolution incl. auto→local fallback; the exact
zero-posts sentinel; idempotency + CurationError; the byte-identical
``ANTI_META_BLOCK`` shared with oms.forum; the cache-split prompt.
"""

from __future__ import annotations

from typing import Any

import pytest

from oms import distill, forum
from oms.bank import FakeBank
from oms.core import Packet, clear_packet_cache
from oms.distill import (
    ANTI_META_BLOCK,
    BUCKETS,
    CurationError,
    NoPostsError,
    ServerCurator,
    ServerUnavailable,
    build_distill_prompt,
    curate,
    resolve,
    validate_bundle,
)
from oms.distill.parse import _norm
from oms.distill.prompts import assert_anti_meta_rules_present
from oms.distill.resolve import AutoCurator, LocalCurator
from oms.distill.weighting import weigh_posts


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_packet_cache()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_SENTENCE = "the retry_backoff() loop slept 30s per attempt under load before the fix"


def _post(pid: str, *, goal: str | None = "g", session: str | None = None, **extra: Any) -> dict[str, Any]:
    sid = session or pid.split("/")[0]
    rec: dict[str, Any] = {
        "id": pid,
        "session_id": sid,
        "type": "post",
        "agent_id": f"{sid}/agent-001-claude",
        "kind": "reflection",
        "goal": goal,
        "structured": {
            "load_bearing_assumption": _SENTENCE,
            "evidence": "verbatim from trace: 'cumtime 4.2s in retry_backoff()'",
            "evidence_ref": None,
            "proposed_next": "hoist the sleep out of retry_backoff() and cap attempts at 3",
            "predicted_outcome": "p99 latency drops ~4x; test_retry_budget passes",
            "confidence": "medium",
        },
    }
    rec.update(extra)
    return rec


def _insight(**kw: Any) -> dict[str, Any]:
    base = {
        "text": "when `retry_backoff` recompiles per call, hoist it to module scope",
        "applies_when": "a hot loop calls retry_backoff() on every attempt",
        "does_not_apply_when": "single-shot scripts that call retry_backoff() once",
        "evidence": [{"post_id": "S1/p1", "quote": "the retry_backoff() loop slept 30s per attempt"}],
        "confidence": "low",
    }
    base.update(kw)
    return base


class FakeModel:
    """A canned sync curator LLM (the ``_HeadlessModel`` shape)."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.calls += 1
        return self.payload


class FakeAsyncModel:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    async def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.calls += 1
        return self.payload


def _bundle_json(*insights: dict[str, Any]) -> str:
    import json

    return json.dumps({"transferable_insights": list(insights)})


# --------------------------------------------------------------------------- #
# ANTI_META_BLOCK — the same object the agent wrote against
# --------------------------------------------------------------------------- #


def test_anti_meta_block_is_the_same_object_as_forum() -> None:
    assert distill.ANTI_META_BLOCK is forum.ANTI_META_BLOCK  # identity, not equality
    assert ANTI_META_BLOCK is forum.ANTI_META_BLOCK


# --------------------------------------------------------------------------- #
# cache-split prompt
# --------------------------------------------------------------------------- #


def test_cache_split_system_has_no_posts_and_exposes_blacklist() -> None:
    posts = [{"id": "S1/p1", "agent_id": "a", "kind": "reflection", "structured": {"x": "secret-post-text"}}]
    system, user = build_distill_prompt(posts=posts, scope="per_goal", goal="g")
    assert "secret-post-text" not in system  # posts NEVER in the cache-stable prefix
    assert "S1/p1" not in system
    assert "secret-post-text" in user and "S1/p1" in user
    assert_anti_meta_rules_present(system)


def test_system_prefix_is_stable_across_post_sets_same_scope() -> None:
    s1, _ = build_distill_prompt(posts=[{"id": "A/1", "structured": {"k": "v1"}}], scope="per_goal", goal="g")
    s2, _ = build_distill_prompt(posts=[{"id": "B/2", "structured": {"k": "v2"}}], scope="per_goal", goal="h")
    assert s1 == s2  # cache-eligible: identical prefix regardless of posts/goal
    cross, _ = build_distill_prompt(posts=[], scope="cross_goal", goal=None)
    assert cross != s1  # scope changes the (still-stable) directive


# --------------------------------------------------------------------------- #
# mechanical validation — port + harden (C3)
# --------------------------------------------------------------------------- #


def _posts_for_parse() -> list[dict[str, Any]]:
    return [_post("S1/p1", session="S1")]


def test_invented_evidence_post_id_is_dropped() -> None:
    posts = _posts_for_parse()
    bad = _insight(evidence=[{"post_id": "S9/ghost", "quote": _SENTENCE}])
    out = validate_bundle({"transferable_insights": [bad]}, posts=posts)
    assert out["transferable_insights"] == []  # no real grounding → Insight dropped


def test_paraphrased_quote_is_dropped_verbatim_required() -> None:
    posts = _posts_for_parse()
    para = _insight(evidence=[{"post_id": "S1/p1", "quote": "the retry loop slept thirty seconds"}])
    assert validate_bundle({"transferable_insights": [para]}, posts=posts)["transferable_insights"] == []


def test_verbatim_quote_survives() -> None:
    posts = _posts_for_parse()
    good = _insight(evidence=[{"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s per attempt"}])
    kept = validate_bundle({"transferable_insights": [good]}, posts=posts)["transferable_insights"]
    assert len(kept) == 1 and kept[0]["evidence"][0]["post_id"] == "S1/p1"


@pytest.mark.parametrize("boundary", ["always", "never", "n/a", "N/A", "none", "", "  "])
def test_unbounded_does_not_apply_when_is_dropped(boundary: str) -> None:
    posts = _posts_for_parse()
    unb = _insight(does_not_apply_when=boundary)
    assert validate_bundle({"transferable_insights": [unb]}, posts=posts)["transferable_insights"] == []


def test_cap_five_per_bucket_regardless_of_model_output() -> None:
    posts = _posts_for_parse()
    seven = [_insight(text=f"`retry_backoff` fix variant {i} hoist to module scope") for i in range(7)]
    out = validate_bundle({"transferable_insights": seven}, posts=posts)
    assert len(out["transferable_insights"]) == 5


def test_shared_anti_meta_code_drops_banned_and_nonconcrete() -> None:
    posts = _posts_for_parse()
    banned = _insight(text="validate first, then check edge cases on retry_backoff()")
    abstract = _insight(text="approach")
    out = validate_bundle({"transferable_insights": [banned, abstract]}, posts=posts)
    assert out["transferable_insights"] == []


def test_evidence_remap_post_id_is_string_task_id_ignored() -> None:
    posts = _posts_for_parse()
    ev = _insight(
        evidence=[{"post_id": "S1/p1", "task_id": "ignored", "quote": "retry_backoff() loop slept 30s"}],
    )
    kept = validate_bundle({"transferable_insights": [ev]}, posts=posts)["transferable_insights"]
    assert kept[0]["evidence"][0] == {"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s"}


def test_recurrence_across_sessions_promotes_confidence_high() -> None:
    posts = [_post("S1/p1", session="S1"), _post("S2/p1", session="S2")]
    ins = _insight(
        confidence="low",
        evidence=[
            {"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s"},
            {"post_id": "S2/p1", "quote": "retry_backoff() loop slept 30s"},
        ],
    )
    kept = validate_bundle({"transferable_insights": [ins]}, posts=posts)["transferable_insights"]
    assert kept[0]["confidence"] == "high"  # cited by ≥2 distinct sessions → recurrence


def test_all_six_buckets_always_present_even_when_empty() -> None:
    out = validate_bundle({"garbage": 1}, posts=_posts_for_parse())
    assert set(out) == set(BUCKETS) and all(out[b] == [] for b in BUCKETS)


def test_norm_collapses_whitespace() -> None:
    assert _norm("a\n  b\t c") == "a b c"


# --------------------------------------------------------------------------- #
# weighting — reuse (load-bearing) + ★ bucket + ordering
# --------------------------------------------------------------------------- #


async def test_high_reuse_post_outranks_unrated_and_buckets_rating(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", rating=5))
    await fake_bank.put_packet(_post("S2/p1", session="S2", rating=2))
    # P1 was injected into session T which then rated well → reuse > 0.
    await fake_bank.put_packet(_post("T/q", session="T", rating=5, goal="other"))
    await fake_bank.record_injection("S1/p1", "T")

    ordered = await weigh_posts(
        [await fake_bank.get_packet("S2/p1"), await fake_bank.get_packet("S1/p1")],
        bank=fake_bank,
    )
    assert ordered[0]["id"] == "S1/p1"  # load-bearing reuse wins
    assert ordered[0]["_signal"]["reuse"] > 0 and ordered[0]["_signal"]["rating_bucket"] == "high"
    assert ordered[1]["_signal"]["reuse"] == 0 and ordered[1]["_signal"]["rating_bucket"] == "low"


async def test_unrated_post_is_still_curated_neutral(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1"))  # rating None
    ordered = await weigh_posts([await fake_bank.get_packet("S1/p1")], bank=fake_bank)
    assert ordered[0]["_signal"]["rating_bucket"] == "neutral"


# --------------------------------------------------------------------------- #
# hybrid resolution + auto fallback
# --------------------------------------------------------------------------- #


def test_resolve_selects_each_mode() -> None:
    m = FakeModel("{}")
    assert isinstance(resolve("server", server_url=""), ServerCurator)
    assert isinstance(resolve("local", model=m), LocalCurator)
    assert isinstance(resolve("auto", model=m, server_url=""), AutoCurator)
    with pytest.raises(ValueError, match="bad OMS_CURATOR_MODE"):
        resolve("bogus", model=m)


async def test_server_mode_unreachable_propagates_no_silent_local() -> None:
    sc = ServerCurator("")
    with pytest.raises(ServerUnavailable):
        await sc.complete("sys", "usr")


# --------------------------------------------------------------------------- #
# state machine — zero posts, idempotency, no-carry-forward, CurationError
# --------------------------------------------------------------------------- #


async def test_zero_posts_raises_exact_sentinel(fake_bank: FakeBank) -> None:
    with pytest.raises(NoPostsError) as ei:
        await curate(scope="per_goal", goal="g", bank=fake_bank, model=FakeModel("{}"), mode="local")
    assert str(ei.value) == "Run /self-distill first!"  # exact, byte-for-byte


async def test_curate_per_goal_then_idempotent_no_respend(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", goal="g"))
    model = FakeModel(
        _bundle_json(_insight(evidence=[{"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s"}]))
    )

    pkt = await curate(scope="per_goal", goal="g", bank=fake_bank, model=model, mode="local")
    assert isinstance(pkt, Packet)
    assert pkt.type == "distill" and pkt.scope == "per_goal" and pkt.goal == "g"
    assert pkt.curator == "local" and pkt.parents == ["S1/p1"]
    assert set(pkt.bundle or {}) == set(BUCKETS)
    assert len(pkt.bundle["transferable_insights"]) == 1
    assert model.calls == 1

    again = await curate(scope="per_goal", goal="g", bank=fake_bank, model=model, mode="local")
    assert again.id == pkt.id and model.calls == 1  # step 2: covered → no spend
    assert again.bundle == pkt.bundle  # the SAME bundle, not a stale/different-parent one


async def test_auto_falls_back_to_local_when_server_unreachable(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", goal="g"))
    model = FakeAsyncModel(
        _bundle_json(_insight(evidence=[{"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s"}]))
    )
    pkt = await curate(scope="per_goal", goal="g", bank=fake_bank, model=model, mode="auto", server_url="")
    assert pkt.curator == "local"  # server unreachable → degraded to local
    assert model.calls == 1


async def test_no_carry_forward_and_per_cross_independence(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", goal="g"))
    await fake_bank.put_packet(_post("S2/p1", session="S2", goal="other"))
    good = _bundle_json(_insight(evidence=[{"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s"}]))

    per = await curate(scope="per_goal", goal="g", bank=fake_bank, model=FakeModel(good), mode="local")
    assert per.parents == ["S1/p1"]  # per_goal: only goal g's post

    cross = await curate(scope="cross_goal", bank=fake_bank, model=FakeModel(good), mode="local")
    # cross_goal spans goals; a distill packet is NEVER an input (no carry-forward).
    assert sorted(cross.parents) == ["S1/p1", "S2/p1"]
    assert all(not p.startswith("curator/") for p in cross.parents)
    assert cross.scope == "cross_goal" and cross.goal is None
    assert per.id != cross.id  # structurally independent outputs


async def test_unparseable_curator_output_raises_and_persists_nothing(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", goal="g"))
    with pytest.raises(CurationError):
        await curate(scope="per_goal", goal="g", bank=fake_bank, model=FakeModel("sorry, no JSON here"), mode="local")
    assert await fake_bank.list_packets(type="distill") == []  # nothing partial written


async def test_per_goal_requires_a_goal(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", goal="g"))
    with pytest.raises(ValueError, match="per_goal distill requires a goal"):
        await curate(scope="per_goal", goal=None, bank=fake_bank, model=FakeModel("{}"), mode="local")


async def test_quarantined_distill_returned_flag_intact_no_respend_no_overwrite(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", goal="g"))
    good = _bundle_json(_insight(evidence=[{"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s"}]))
    model = FakeModel(good)
    pkt = await curate(scope="per_goal", goal="g", bank=fake_bank, model=model, mode="local")
    await fake_bank.quarantine(pkt.id, "auditor: poisoned bundle")

    again = await curate(scope="per_goal", goal="g", bank=fake_bank, model=model, mode="local")
    assert again.id == pkt.id
    assert again.quarantined is True  # flag intact — non-hiding; consumer (/inject gate) decides
    assert model.calls == 1  # not re-spent (input unchanged → same content-addressed id)
    row = await fake_bank.get_packet(pkt.id)
    assert row is not None and row["quarantined"] is True  # not silently overwritten (append-only)


async def test_quarantined_posts_excluded_from_curation(fake_bank: FakeBank) -> None:
    await fake_bank.put_packet(_post("S1/p1", session="S1", goal="g"))
    await fake_bank.put_packet(_post("S1/p2", session="S1", goal="g"))
    await fake_bank.quarantine("S1/p2", "poisoned")
    good = _bundle_json(_insight(evidence=[{"post_id": "S1/p1", "quote": "retry_backoff() loop slept 30s"}]))
    pkt = await curate(scope="per_goal", goal="g", bank=fake_bank, model=FakeModel(good), mode="local")
    assert pkt.parents == ["S1/p1"]  # quarantined p2 never reaches curation
