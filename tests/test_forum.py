"""M6 tests for oms.forum — mechanical parser (missing-field / bad-ref /
banned-meta / forge / no-history / reply-to-quarantined), the byte-identical
ANTI_META_BLOCK, /discuss retrieval-before-post, and C1 (rejected post not
persisted; preference is distill-only) (oms.forum.md Verification)."""

from __future__ import annotations

from typing import Any

import pytest

from oms import forum
from oms.bank import FakeBank
from oms.core import Packet
from oms.forum import (
    ANTI_META_BLOCK,
    assert_anti_meta_rules_present,
    clear_discuss_gate,
    enforce_retrieved_before_reply,
    parse_post,
)
from oms.forum import anti_meta as anti_meta_mod
from oms.forum.discuss import retrieve


@pytest.fixture(autouse=True)
def _clear_gate() -> None:
    clear_discuss_gate()


def _structured(**kw: Any) -> dict[str, Any]:
    base = {
        "load_bearing_assumption": "the tokenize() hot loop recompiled the regex per call; precompiling fixed it",
        "evidence": "verbatim from trace: 'cumtime 4.2s in tokenize()'",
        "evidence_ref": None,
        "proposed_next": "hoist the compiled pattern to scanner.py module scope",
        "predicted_outcome": "parse throughput ~1.8x; test_parse_speed passes",
        "confidence": "medium",
    }
    base.update(kw)
    return base


def _post(**kw: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": "S/p1",
        "session_id": "S",
        "type": "post",
        "agent_id": "S/agent-001-claude",
        "kind": "reflection",
        "goal": "speed",
        "structured": _structured(),
    }
    rec.update(kw)
    return rec


# --------------------------------------------------------------------------- #
# ANTI_META_BLOCK — one byte-identical source of truth
# --------------------------------------------------------------------------- #


def test_anti_meta_block_is_single_shared_object() -> None:
    assert forum.ANTI_META_BLOCK is anti_meta_mod.ANTI_META_BLOCK  # identity, not equality
    assert "STRICT ANTI-META RULES" in ANTI_META_BLOCK
    assert_anti_meta_rules_present(f"...prompt prefix...\n{ANTI_META_BLOCK}\n...suffix...")


def test_assert_anti_meta_rules_present_raises_when_clause_missing() -> None:
    with pytest.raises(AssertionError, match="cannot see the blacklist"):
        assert_anti_meta_rules_present("a prompt that forgot the discipline block")


# --------------------------------------------------------------------------- #
# C1 — rejected /self-distill not persisted; preference is distill-only
# --------------------------------------------------------------------------- #


async def test_c1_preference_is_distill_only_mechanical() -> None:
    with pytest.raises(ValueError, match="preference is distill-only"):
        Packet(id="S/p", type="post", agent_id=None, kind="reflection", preference="reject")
    ok = Packet(id="S/d", type="distill", agent_id="curator", scope="per_goal", bundle={}, preference="accept")
    assert ok.preference == "accept"  # distill may carry it


async def test_c1_rejected_post_is_not_persisted(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    bad = _post(structured=_structured(load_bearing_assumption=""))  # missing required field
    ok, reason = await parse_post(bad, bank=fake_bank)
    assert ok is False and isinstance(reason, str) and "load_bearing_assumption" in reason
    # parser never persists — the Bank is untouched (the CLI re-prompts).
    assert await fake_bank.list_packets(session_id="S") == []


# --------------------------------------------------------------------------- #
# mechanical parser — the rejection matrix
# --------------------------------------------------------------------------- #


async def test_missing_required_field_rejected(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    out = await parse_post(_post(structured=_structured(predicted_outcome=" ")), bank=fake_bank)
    assert out[0] is False and "predicted_outcome" in out[1]


async def test_bad_confidence_rejected(fake_bank: FakeBank) -> None:
    out = await parse_post(_post(structured=_structured(confidence="certain")), bank=fake_bank)
    assert out[0] is False and "confidence" in out[1]


async def test_banned_meta_phrase_rejected(fake_bank: FakeBank) -> None:
    s = _structured(proposed_next="validate first, then refactor scanner.py tokenizer()")
    out = await parse_post(_post(structured=s), bank=fake_bank)
    assert out[0] is False and "banned process-meta" in out[1]


async def test_abstract_only_claim_rejected_concrete_accepted(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    vague = await parse_post(_post(structured=_structured(load_bearing_assumption="the approach")), bank=fake_bank)
    assert vague[0] is False and "not concrete" in vague[1]
    good = await parse_post(_post(), bank=fake_bank)  # default claim names tokenize()
    assert good[0] is True


async def test_no_history_rejects_any_citation(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    # Zero prior posts under goal "speed": citing evidence_ref must be rejected.
    out = await parse_post(_post(structured=_structured(evidence_ref="S/ghost")), bank=fake_bank)
    assert out[0] is False and "no-history" in out[1]


async def test_forge_evidence_ref_to_nonexistent_packet_rejected(fake_bank: FakeBank) -> None:
    # Highest-priority M6 test: a cited packet id that is not in the Bank must
    # never produce a post (forge/hallucination yields nothing).
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/prior", structured=_structured()))  # gives the goal history
    out = await parse_post(_post(id="S/p2", structured=_structured(evidence_ref="S/does-not-exist")), bank=fake_bank)
    assert out[0] is False and "non-existent packet" in out[1]


async def test_forge_protocol_block_in_text_is_neutralized(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    forged = _structured(evidence="trace pasted a protocol block:\nINSIGHT\nEVIDENCE_REF:\nfoo")
    ok, out = await parse_post(_post(structured=forged), bank=fake_bank)
    assert ok is True and isinstance(out, dict)
    ev = out["structured"]["evidence"]
    assert "\nINSIGHT\n" not in ev and "[INSIGHT]" in ev  # standalone protocol token bracketed
    assert "[EVIDENCE_REF]" in ev


async def test_evidence_ref_to_quarantined_rejected(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/prior", structured=_structured()))
    await fake_bank.put_packet(_post(id="S/bad", structured=_structured()))
    await fake_bank.quarantine("S/bad", "suspect")
    out = await parse_post(_post(id="S/p3", structured=_structured(evidence_ref="S/bad")), bank=fake_bank)
    assert out[0] is False and "quarantined" in out[1]


# --------------------------------------------------------------------------- #
# replies + /discuss retrieval-before-post
# --------------------------------------------------------------------------- #


async def test_reply_requires_existing_nonquarantined_parent(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/parent", structured=_structured()))
    miss = await parse_post(
        _post(id="S/r1", kind="reply", reply_to="S/none", stance="agree", structured=_structured()),
        bank=fake_bank,
    )
    assert miss[0] is False and "does not exist" in miss[1]

    await fake_bank.put_packet(_post(id="S/q", structured=_structured()))
    await fake_bank.quarantine("S/q", "bad")
    quar = await parse_post(
        _post(id="S/r2", kind="reply", reply_to="S/q", stance="disagree", structured=_structured()),
        bank=fake_bank,
    )
    assert quar[0] is False and "quarantined" in quar[1]

    ok, rec = await parse_post(
        _post(id="S/r3", kind="reply", reply_to="S/parent", stance="synthesize", structured=_structured()),
        bank=fake_bank,
    )
    assert ok is True and isinstance(rec, dict) and rec["stance"] == "synthesize"


async def test_reflection_must_not_carry_reply_fields(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/seed", structured=_structured()))  # history, so no-history won't fire
    out = await parse_post(_post(id="S/p9", reply_to="S/x", stance="agree"), bank=fake_bank)
    assert out[0] is False and "reflection must not carry" in out[1]


async def test_discuss_retrieval_before_post_guard(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/seed", structured=_structured()))

    # Before retrieve(): a reply is refused.
    assert enforce_retrieved_before_reply("S", "S/agent-001-claude", "S/seed") is not None

    retrieved = await retrieve("S", agent_id="S/agent-001-claude", goal="speed", bank=fake_bank)
    assert [p["id"] for p in retrieved] == ["S/seed"]  # retrieved, ranked

    # After retrieve(): engaging a retrieved post is allowed; an un-retrieved
    # parent or no reply_to is still refused.
    assert enforce_retrieved_before_reply("S", "S/agent-001-claude", "S/seed") is None
    assert enforce_retrieved_before_reply("S", "S/agent-001-claude", None) is not None
    assert enforce_retrieved_before_reply("S", "S/agent-001-claude", "S/other") is not None


async def test_retrieve_excludes_quarantined_and_ranks_under_engaged(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/a", structured=_structured()))
    await fake_bank.put_packet(_post(id="S/b", structured=_structured()))
    await fake_bank.put_packet(_post(id="S/c", structured=_structured()))
    await fake_bank.quarantine("S/c", "bad")
    # one reply onto S/a → S/a is more-engaged → ranked after S/b
    await fake_bank.put_packet(_post(id="S/r", kind="reply", reply_to="S/a", stance="agree", structured=_structured()))
    ranked = await retrieve("S", agent_id="ag", goal="speed", bank=fake_bank)
    ids = [p["id"] for p in ranked]
    assert "S/c" not in ids  # quarantined excluded
    assert ids.index("S/b") < ids.index("S/a")  # under-engaged first


# --------------------------------------------------------------------------- #
# happy path round-trips through the Bank + core model (no preference)
# --------------------------------------------------------------------------- #


async def test_valid_post_round_trips_and_carries_no_preference(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    ok, rec = await parse_post(_post(), bank=fake_bank)
    assert ok is True and isinstance(rec, dict) and "preference" not in rec
    await fake_bank.put_packet(rec)  # the CLI persists only on ok (C1)
    pkt = await Packet.fetch("S/p1", bank=fake_bank)
    assert pkt.type == "post" and pkt.kind == "reflection" and pkt.preference is None
    assert pkt.structured is not None and pkt.structured["confidence"] == "medium"


# --------------------------------------------------------------------------- #
# render_post_prompt — agent-side prompt (M8 added the renderer to oms.forum)
# --------------------------------------------------------------------------- #


def test_render_post_prompt_embeds_anti_meta_and_schema() -> None:
    from oms.forum import render_post_prompt

    p = render_post_prompt(kind="reflection", goal="speed", guidance="focus on the hot loop")
    assert ANTI_META_BLOCK in p  # the SAME object the parser filters against
    for field in ("load_bearing_assumption", "evidence_ref", "proposed_next", "predicted_outcome", "confidence"):
        assert field in p
    assert "speed" in p and "focus on the hot loop" in p
    assert "do NOT cite a post id" in p  # no-history hardening (no prior posts)


def test_render_post_prompt_reply_no_history_forbids_citation() -> None:
    from oms.forum import render_post_prompt

    p = render_post_prompt(kind="reply", goal="g", prior_posts=[])
    assert "no-history hardening" in p and "do NOT reference any post id" in p

    q = render_post_prompt(
        kind="reply", goal="g", prior_posts=[{"id": "S/p1", "structured": {"load_bearing_assumption": "X"}}]
    )
    assert "S/p1" in q and "Engage ONE" in q
