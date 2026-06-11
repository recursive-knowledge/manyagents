"""Tests for ``manyagent.testing`` — the simulated-conversation scaffolding.

Each test here is a *conversation*, not a function call: the trial story (a
real captured session — see the ``manyagent.testing`` docstring) replayed through
the REAL lifecycle verbs and knowledge-loop handlers, with only the Bank, the
LLM, and the wrapped agent CLI as scripted doubles. The assertions target the
system's actual utility — evidence survives capture, accepted lessons persist
with their ★, rejected ones leave no trace (C1), bundles stay verbatim-
grounded in their parent posts, curation is idempotent, and knowledge flows
across sessions through the injection ledger.
"""

from __future__ import annotations

from manyagent import cli
from manyagent.bank import FakeBank
from manyagent.testing import (
    Simulation,
    trial_bundle,
    trial_reflection,
    trial_reply,
    trial_transcript,
)

_CORRECTION_FRAGMENT = "-91 degrees fahrenheit was the lowest (wind-chill) temp. in alaska"


# --------------------------------------------------------------------------- #
# the full story, replayed
# --------------------------------------------------------------------------- #


async def test_trial_story_full_loop(sim: Simulation) -> None:
    """start → run the agent → /self-distill (★2) → /discuss (disagree) →
    /cross-distill → /inject → end: the complete knowledge loop on the trial
    conversation, every guard live."""
    assert (await sim.start("trial")).ok
    assert (await sim.register()).ok

    # The user's correction must survive the real capture pipeline
    # (tee → validate → scrub → bound → persist) into the stored trace.
    r = await sim.run_agent(transcript=trial_transcript())
    assert r.ok and r.saw("trace:")  # the run prints the viewer link; capture is checked via the Bank below
    [raw] = sim.packets("raw")
    trace = await sim.bank.get_trace(raw["id"])
    assert trace is not None and _CORRECTION_FRAGMENT in trace["body"]

    # The reflection lands with the human's ★2 override and never carries
    # `preference` (C1: that key is distill-only).
    r = await sim.self_distill(trial_reflection(), rating=2)
    assert r.ok and r.saw("stored post")
    [post] = sim.packets("post")
    assert post["rating"] == 2 and "preference" not in post
    assert post["structured"]["evidence"].endswith("temp. in alaska")

    # A disagree reply engages the reflection (retrieval-before-reply is live).
    r = await sim.discuss(trial_reply(), stance="disagree")
    assert r.ok
    reply = next(p for p in sim.packets("post") if p.get("kind") == "reply")
    assert reply["reply_to"] == post["id"] and reply["stance"] == "disagree"

    # The curated bundle cites the reflection. The discriminating check: the
    # SHIPPED grounding parser passed every item through unchanged (its quotes
    # ground verbatim in the post) and the story's central lesson survived —
    # an emptied bundle stores fine and injects fine, so rc alone proves
    # nothing.
    r = await sim.cross_distill(trial_bundle(post["id"]))
    assert r.ok and r.saw("curated cross_goal bundle")
    [distill] = sim.packets("distill")
    assert post["id"] in distill["parents"]
    assert distill["bundle"]["pitfalls"], "curation silently dropped the story's pitfall"
    assert distill["bundle"] == trial_bundle(post["id"])

    # Injection writes the ledger row that downstream-reuse weighting reads.
    r = await sim.inject()
    assert r.ok and r.saw("injections row written")
    [row] = await sim.bank.list_injections()
    assert row["packet_id"] == distill["id"] and row["target_session_id"] == "trial"

    assert (await sim.end()).ok
    assert sim.bank._sessions["trial"]["status"] == "ended"
    assert not cli.active_session_path().exists()


async def test_cross_distill_is_idempotent_over_the_same_posts(sim: Simulation) -> None:
    """Re-curating an unchanged corpus lands the SAME content-addressed bundle
    — no duplicate packet, no double spend."""
    await sim.start("trial")
    await sim.self_distill(trial_reflection(), rating=2)
    [post] = sim.packets("post")

    first = await sim.cross_distill(trial_bundle(post["id"]))
    second = await sim.cross_distill(trial_bundle(post["id"]))
    assert first.ok and second.ok
    [distill] = sim.packets("distill")  # one packet — necessary but not sufficient
    assert any(distill["id"] in line for line in first.out)
    assert any(distill["id"] in line for line in second.out)
    # The discriminating check: the curator MODEL ran exactly once — the
    # second call short-circuited on the content-addressed id. (The packet
    # count alone can't tell: put_packet upserts, so a re-spend re-storing
    # under the same id would still leave one packet.)
    assert len(sim.curator_model.prompts) == 1


# --------------------------------------------------------------------------- #
# the gates, exercised conversationally
# --------------------------------------------------------------------------- #


async def test_rejected_self_distill_persists_nothing(sim: Simulation) -> None:
    """C1: a human reject means the Bank never sees the post — no record, no
    `preference=reject` tombstone, nothing."""
    await sim.start("trial")
    r = await sim.self_distill(trial_reflection(), accept=False)
    assert r.rc == 1 and r.saw("not stored")
    assert sim.packets("post") == []


async def test_unparseable_agent_output_is_not_stored(sim: Simulation) -> None:
    """An agent that emits non-JSON gets re-prompted, not persisted."""
    await sim.start("trial")
    r = await sim.self_distill("the model rambled instead of emitting JSON")
    assert r.rc == 1 and r.saw("no parseable JSON")
    assert sim.packets("post") == []


# --------------------------------------------------------------------------- #
# the seeded story — read-side tests start from existing knowledge
# --------------------------------------------------------------------------- #


async def test_seeded_bundle_injects_into_a_new_session(trial_bank: FakeBank, tmp_path: object) -> None:
    """Cross-session reuse, the system's point: a NEW session inherits the
    trial story's curated bundle via /inject, and the ledger records it."""
    from pathlib import Path

    with Simulation(bank=trial_bank, home=Path(str(tmp_path)) / ".manyagent") as sim:
        await sim.start("S2")
        r = await sim.inject()  # defaults to the latest distill = the seeded bundle
        assert r.ok and r.saw("--- inject preview ---")
        assert r.saw("Treating 'mathematical fact' literally")  # the pitfall reached the new session
        [row] = await trial_bank.list_injections()
        assert row["packet_id"] == "curator/3df0178cd6811aee23579272"
        assert row["target_session_id"] == "S2"


async def test_seeded_bundle_quarantine_refuses_inject(trial_bank: FakeBank, tmp_path: object) -> None:
    """A quarantined bundle is refused BEFORE the preview — poisoned knowledge
    never reaches a new session."""
    from pathlib import Path

    await trial_bank.quarantine("curator/3df0178cd6811aee23579272", "auditor flagged")
    with Simulation(bank=trial_bank, home=Path(str(tmp_path)) / ".manyagent") as sim:
        await sim.start("S2")
        r = await sim.inject(packet="curator/3df0178cd6811aee23579272")
        assert r.rc == 1 and r.saw("quarantined")
        assert not r.saw("--- inject preview ---")
        assert await trial_bank.list_injections() == []


# --------------------------------------------------------------------------- #
# fixture-fidelity canaries
# --------------------------------------------------------------------------- #


async def test_trial_reflection_still_passes_the_live_discipline(trial_bank: FakeBank) -> None:
    """The seeded reflection passed the production parser when it was
    captured; it must keep passing the CURRENT one — if the discipline
    tightens past the fixture, this canary (not some downstream simulation)
    is what fails."""
    from manyagent.forum import parse_post

    record = {
        "id": "trial/canary01",
        "session_id": "trial",
        "type": "post",
        "agent_id": "trial/mcp",
        "kind": "reflection",
        "goal": None,
        "structured": trial_reflection(),
    }
    ok, res = await parse_post(record, bank=trial_bank)
    assert ok is True, f"trial reflection no longer passes the discipline: {res}"


def test_trial_bundle_survives_the_shipped_grounding_parser_unchanged() -> None:
    """Fixture-drift canary, judged by the SHIPPED parser — not a hand-rolled
    substring check, whose predicate (raw JSON haystack) diverges from the
    real one (whitespace-normalized quote vs. searchable field values). If the
    discipline tightens past the fixture, this fails with the dropped item
    visible in the diff."""
    from manyagent.distill.parse import validate_bundle

    post = {
        "id": "trial/kds77s64",
        "session_id": "trial",
        "type": "post",
        "kind": "reflection",
        "structured": trial_reflection(),
    }
    bundle = trial_bundle(post["id"])
    assert bundle["pitfalls"], "the trial bundle lost its evidence"
    assert validate_bundle(bundle, posts=[post]) == bundle
