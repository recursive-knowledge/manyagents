"""M3 tests for manyagent.core — validators, .fetch() hydration (no I/O on bare
ctor), Collection accessors, session.posts(goal) filtering (manyagent.core.md
Verification)."""

from __future__ import annotations

from typing import Any

import pytest

from manyagent.bank import FakeBank
from manyagent.core import Agent, Collection, Goal, KnowledgePacket, Packet, Session, clear_packet_cache


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_packet_cache()


def _post(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "S/p", "type": "post", "agent_id": "S/agent-001-claude", "kind": "reflection"}
    base.update(kw)
    base["session_id"] = str(base["id"]).split("/")[0]  # FakeBank filters on this
    return base


# --------------------------------------------------------------------------- #
# validators
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("t", ["raw", "post", "distill"])
def test_type_validator_accepts_taxonomy(t: str) -> None:
    extra: dict[str, Any] = {"scope": "per_goal", "bundle": {}} if t == "distill" else {}
    assert Packet(id="S/x", type=t, agent_id=None, **extra).type == t


def test_type_validator_rejects_other() -> None:
    # Pydantic now enforces the Literal at field-parse time (literal_error);
    # the message no longer includes "bad packet type" from the old @field_validator.
    with pytest.raises(ValueError, match="'raw', 'post' or 'distill'"):
        Packet(id="S/x", type="self-distill", agent_id=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("r", [None, 1, 3, 5])
def test_rating_valid(r: int | None) -> None:
    assert Packet(id="S/p", type="post", agent_id=None, rating=r).rating == r


@pytest.mark.parametrize("r", [0, 6, -1, 100])
def test_rating_out_of_range_rejected(r: int) -> None:
    with pytest.raises(ValueError, match="rating must be"):
        Packet(id="S/p", type="post", agent_id=None, rating=r)


def test_reply_requires_reply_to_and_stance() -> None:
    with pytest.raises(ValueError, match="reply requires reply_to and stance"):
        Packet(id="S/r", type="post", agent_id=None, kind="reply", reply_to="S/p")  # missing stance
    ok = Packet(id="S/r", type="post", agent_id=None, kind="reply", reply_to="S/p", stance="agree")
    assert ok.stance == "agree"


def test_bad_stance_and_kind_rejected() -> None:
    # Pydantic enforces Literal fields at parse time; messages come from literal_error.
    with pytest.raises(ValueError, match="'agree', 'disagree' or 'synthesize'"):
        Packet(id="S/r", type="post", agent_id=None, kind="reply", reply_to="S/p", stance="meh")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="'reflection' or 'reply'"):
        Packet(id="S/r", type="post", agent_id=None, kind="ramble")  # type: ignore[arg-type]


def test_distill_requires_scope_and_bundle() -> None:
    with pytest.raises(ValueError, match="distill requires scope and bundle"):
        Packet(id="S/d", type="distill", agent_id="curator", scope="per_goal")  # missing bundle
    ok = Packet(id="S/d", type="distill", agent_id="curator", scope="cross_goal", bundle={"x": 1})
    assert ok.scope == "cross_goal"


def test_goal_none_valid_everywhere() -> None:
    assert Packet(id="S/p", type="post", agent_id=None, goal=None).goal is None
    assert Session(id="S", goal=None).goal is None
    assert Packet(id="S/d", type="distill", agent_id="curator", scope="per_goal", bundle={}, goal=None).goal is None


def test_packet_is_frozen() -> None:
    p = Packet(id="S/p", type="post", agent_id=None)
    with pytest.raises(Exception):  # frozen → mutation rejected
        p.rating = 5  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# derived properties (no I/O) + wire shape
# --------------------------------------------------------------------------- #


def test_session_id_and_agent_properties_no_io() -> None:
    p = Packet(id="CMA1-FJ2P/abc", type="post", agent_id="CMA1-FJ2P/agent-001-claude")
    assert p.session_id == "CMA1-FJ2P"
    assert isinstance(p.agent, Agent) and p.agent.id == "CMA1-FJ2P/agent-001-claude"
    assert Packet(id="S/p", type="post", agent_id=None).agent is None


def test_to_record_is_knowledge_packet() -> None:
    p = Packet(id="S/p", type="post", agent_id=None, rating=4, goal="speed")
    kp = p.to_record()
    assert isinstance(kp, KnowledgePacket)
    assert kp.id == "S/p" and kp.rating == 4 and kp.goal == "speed"


def test_agent_principal_id_field() -> None:
    # 00011: principal_id is an optional field, defaults None, and survives
    # from_activity + model_dump (so the web route can surface it).
    assert Agent(id="S/agent-001-claude").principal_id is None
    a = Agent(id="S/agent-001-claude", principal_id="P1")
    assert a.principal_id == "P1"
    derived = Agent.from_activity({"id": "S/agent-001-claude", "principal_id": "P1", "adapter": "claude"})
    assert derived.principal_id == "P1"
    assert derived.model_dump(mode="json")["principal_id"] == "P1"


# --------------------------------------------------------------------------- #
# .fetch() hydration: bare ctor no I/O, fetch hits the Bank, memoizes
# --------------------------------------------------------------------------- #


class _ExplodingBank:
    """A Bank whose every attribute access explodes — a tripwire that proves
    where Bank I/O does (``fetch``) and does not (bare ctor) happen."""

    def __getattr__(self, _name: str) -> Any:
        raise AssertionError("Bank touched")


async def test_bare_ctor_no_io_but_fetch_touches_bank(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_bank() is the only Bank seam fetch() falls back to — make any touch
    # of it explode, so the assertions below are load-bearing, not decorative.
    monkeypatch.setattr("manyagent.core.models.get_bank", lambda *a, **k: _ExplodingBank())

    p = Packet(id="S/p", type="post", agent_id=None)  # constructs — no Bank touch
    assert p.id == "S/p" and p.session_id == "S" and p.agent is None  # derived props: still no I/O

    with pytest.raises(AssertionError, match="Bank touched"):
        await Packet.fetch("S/p")  # fetch() reaches for the Bank → tripwire fires


async def test_fetch_hits_bank_and_memoizes(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/p", rating=3))

    p1 = await Packet.fetch("S/p", bank=fake_bank)
    assert isinstance(p1, Packet) and p1.rating == 3

    # Mutate the stored row; a cached fetch returns the memoized object.
    await fake_bank.put_packet(_post(id="S/p", rating=5))
    p2 = await Packet.fetch("S/p", bank=fake_bank)
    assert p2 is p1 and p2.rating == 3  # memoized (identity preserved)

    p3 = await Packet.fetch("S/p", bank=fake_bank, force=True)
    assert p3.rating == 5  # force bypasses the cache


async def test_fetch_missing_raises(fake_bank: FakeBank) -> None:
    with pytest.raises(LookupError, match="no packet"):
        await Packet.fetch("S/nope", bank=fake_bank)


# --------------------------------------------------------------------------- #
# Collection accessors
# --------------------------------------------------------------------------- #


def test_collection_accessors() -> None:
    a, b, c = (Agent(id="S/agent-001-claude"), Agent(id="S/agent-002-codex"), Agent(id="S/agent-003-gemini"))
    col: Collection[Agent] = Collection([a, b, c])
    assert len(col) == 3
    assert col.list() == [a, b, c]
    assert col.get("S/agent-002-codex") is b
    assert col.get("missing") is None
    assert col[0] is a
    assert [x.id for x in col] == [a.id, b.id, c.id]
    assert {x.id for x in col.search("codex|gemini")} == {b.id, c.id}
    assert col.remove("S/agent-001-claude") is True
    assert col.remove("S/agent-001-claude") is False
    assert len(col) == 2


# --------------------------------------------------------------------------- #
# integration vs the in-memory Bank: round-trip + posts(goal) filtering
# --------------------------------------------------------------------------- #


async def test_round_trip_and_posts_goal_filtering(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S", goal=None)
    await fake_bank.put_packet(_post(id="S/r1", kind="reflection", goal="speed"))
    await fake_bank.put_packet(_post(id="S/r2", kind="reply", reply_to="S/r1", stance="disagree", goal="speed"))
    await fake_bank.put_packet(_post(id="S/r3", kind="reflection", goal="memory"))
    await fake_bank.put_packet(_post(id="S/r4", kind="reflection", goal=None))  # ungoaled
    await fake_bank.put_packet({
        "id": "S/d1",
        "session_id": "S",
        "type": "distill",
        "agent_id": "curator",
        "scope": "per_goal",
        "bundle": {"transferable_insights": []},
        "parents": ["S/r1"],
        "goal": "speed",
    })

    sess = Session(id="S")
    speed_posts = await sess.posts(goal="speed", bank=fake_bank)
    assert {p.id for p in speed_posts} == {"S/r1", "S/r2"}

    all_posts = await sess.posts(bank=fake_bank)  # goal=None → all, ungoaled included
    assert {p.id for p in all_posts} == {"S/r1", "S/r2", "S/r3", "S/r4"}

    dists = await sess.distills(goal="speed", bank=fake_bank)
    assert [d.id for d in dists] == ["S/d1"]
    assert dists[0].parents == ["S/r1"] and dists[0].scope == "per_goal"

    reply = speed_posts.get("S/r2")
    assert reply is not None and reply.reply_to == "S/r1" and reply.stance == "disagree"


async def test_quarantined_packet_remains_in_session_packets(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S")
    await fake_bank.put_packet(_post(id="S/p1"))
    await fake_bank.put_packet(_post(id="S/p2"))
    await fake_bank.quarantine("S/p2", "suspect")

    pkts = await Session(id="S").packets(bank=fake_bank)
    assert {p.id for p in pkts} == {"S/p1", "S/p2"}  # non-hiding: still present
    flagged = pkts.get("S/p2")
    assert flagged is not None and flagged.quarantined is True


def test_goal_noun_is_frozen_value_object() -> None:
    g = Goal(label="speed up the parser")
    assert g.label == "speed up the parser"
    with pytest.raises(Exception):
        g.label = "x"  # type: ignore[misc]
