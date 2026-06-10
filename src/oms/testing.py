# ruff: noqa: RUF001, RUF002 — trial-story strings quote a real transcript; its unicode math signs are content
"""oms.testing — simulated-conversation scaffolding: dummy Bank, dummy model,
dummy agent harness.

The unit of test this module enables is a **conversation**, not a function
call: drive the *real* session-lifecycle verbs (``oms.cli._do_*``) and the
*real* knowledge-loop handlers (``oms._handlers.do_*``) end to end, with only
the three seams a live run would shell out to replaced by scripted doubles —

* the Bank        → :class:`oms.bank.FakeBank` (in-memory, already shipped)
* the LLM         → :class:`DummyModel` (queue of canned completions)
* the wrapped CLI → :class:`DummyAdapter` + a PTY stub that "plays" a supplied
  transcript through the real tee → capture → scrub → persist pipeline

so every guard on the path (forum discipline, anti-meta, retrieval-before-
reply, C1 reject-not-persisted, curator idempotency, quarantine refusal) is
the shipped code. ``tests/test_e2e.py`` and ``scripts/simulate_story.py``
pioneered this pattern with private copies; this module is the shared,
importable form.

The canonical dummy input is the **trial story** — a real captured session
(Bank dump, 2026-06-09): the user has a standing rule "tell a good
mathematical fact along with whatever math question I ask"; the agent offers
factorization trivia (91 = 7 × 13); the user corrects it ("No, this is a bad
fact. You could have used information like -91 degrees fahrenheit …");
``/self-distill`` commits the lesson as a ★2 reflection and ``/cross-distill``
curates it into a pitfalls/next-steps bundle whose evidence quotes ground
verbatim in the post. :func:`trial_reflection` / :func:`trial_bundle` are
those payloads verbatim; :func:`seed_trial_story` plants the whole story in a
Bank for read-side tests.

Typical use (see ``tests/test_testing.py``)::

    with Simulation(home=tmp_path / ".oms") as sim:
        await sim.start("trial")
        await sim.run_agent(transcript=trial_transcript())
        await sim.self_distill(trial_reflection(), rating=2)
        await sim.cross_distill(trial_bundle(post_id))
        await sim.inject()
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oms.bank import FakeBank
from oms.capture import SCRUB_VERSION, CanonicalTrace, TraceEvent

__all__ = [
    "DummyAdapter",
    "DummyModel",
    "ScriptedIO",
    "Simulation",
    "StepResult",
    "seed_trial_story",
    "trial_bundle",
    "trial_reflection",
    "trial_reply",
    "trial_transcript",
]

# --------------------------------------------------------------------------- #
# the three doubles
# --------------------------------------------------------------------------- #


class ScriptedIO:
    """A scripted ``input()`` / captured ``print()`` pair for the handlers'
    ``io=(input_fn, output_fn)`` seam. Responses are consumed in order; once
    exhausted, ``default`` answers every further prompt (handy for optional
    prompts like the ``oms end`` ★, which only fires when an unrated
    reflection exists)."""

    def __init__(self, *responses: str, default: str = "skip") -> None:
        self.responses = list(responses)
        self.default = default
        self.prompts: list[str] = []
        self.out: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else self.default

    def pair(self) -> tuple[Any, Any]:
        return (self, self.out.append)

    def saw(self, fragment: str) -> bool:
        return any(fragment in line for line in self.out)


class DummyModel:
    """Queue-scripted stand-in for a headless LLM (an adapter's
    ``distill_model()`` or the curator's discovered local model). Raises on an
    empty queue rather than inventing output — a test that under-scripts its
    conversation should fail loudly, not plausibly."""

    def __init__(self, *responses: str | dict[str, Any]) -> None:
        self.responses: list[str] = []
        self.prompts: list[str] = []
        for r in responses:
            self.push(r)

    def push(self, response: str | dict[str, Any]) -> None:
        self.responses.append(response if isinstance(response, str) else json.dumps(response))

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("DummyModel.complete() called with an empty queue — push() the next canned reply")
        return self.responses.pop(0)


class DummyAdapter:
    """The wrapped-agent seam: exactly the attributes the handlers touch —
    ``name``/``binary``, a headless ``distill_model()``, a ``capture()``
    producing a minimal :class:`CanonicalTrace` from ``transcript``, and a
    no-op ``install_skills`` (mirroring the Adapter ABC default)."""

    def __init__(self, name: str = "claude", *, model: DummyModel | None = None, transcript: str = "") -> None:
        self.name = name
        self.binary = name
        self.model = model if model is not None else DummyModel()
        self.transcript = transcript
        self.session_id = ""
        self.agent_id = ""

    def bind(self, *, session_id: str, agent_id: str) -> DummyAdapter:
        self.session_id = session_id
        self.agent_id = agent_id
        return self

    def distill_model(self) -> DummyModel:
        return self.model

    def install_skills(self, **_kwargs: Any) -> None:
        return None

    def capture(self) -> CanonicalTrace:
        return CanonicalTrace(
            session_id=self.session_id,
            agent_id=self.agent_id,
            adapter=self.name,
            events=[TraceEvent(ts=0.0, kind="agent", text=self.transcript or "(no transcript)")],
            source_fidelity="pty",
        )


# --------------------------------------------------------------------------- #
# the trial story — a real captured session, verbatim (Bank dump 2026-06-09)
# --------------------------------------------------------------------------- #

_TRIAL_CORRECTION = (
    "No, this is a bad fact. You could have used information like -91 degrees "
    "fahrenheit was the lowest (wind-chill) temp. in alaska"
)

_TRIAL_REFLECTION: dict[str, Any] = {
    "load_bearing_assumption": (
        "The standing rule `tell a good mathematical fact along with whatever math question I ask` "
        "is satisfied by real-world trivia keyed to the literal result value "
        "(e.g. -91 → -91°F Alaska wind-chill record), NOT by number-theoretic properties "
        "of the result (91 = 7 × 13 pseudo-prime trap)"
    ),
    "evidence": _TRIAL_CORRECTION,
    "evidence_ref": None,
    "proposed_next": (
        "For the next arithmetic answer in this session, pair the computed value with a "
        "physical/geographic record matching that exact number (temperature, elevation, year, speed) "
        "instead of factoring or primality observations"
    ),
    "predicted_outcome": (
        "The value-keyed real-world fact will be accepted without a correction message from the user; "
        "a factorization-style fact would draw another 'bad fact' rejection"
    ),
    "confidence": "medium",
}

# A stance reply engaging the reflection (the /discuss leg of the story —
# composed for the harness; the live session stopped at the reflection).
_TRIAL_REPLY: dict[str, Any] = {
    "load_bearing_assumption": (
        "The rule `tell a good mathematical fact` is about audience interest, not value-keyed records: "
        "any fact novel to the user (why 91 = 7 × 13 trips mental math, the history of the × sign) "
        "could satisfy it"
    ),
    "evidence": _TRIAL_CORRECTION,
    "evidence_ref": None,
    "proposed_next": (
        "on the next answer offer one value-keyed record AND one non-numeric math-history fact, "
        "then watch which of the two the user engages"
    ),
    "predicted_outcome": (
        "the user engages with at most one of the two facts; if the record is ignored, "
        "the value-keyed reading is too narrow"
    ),
    "confidence": "low",
}

_TRIAL_BUNDLE: dict[str, Any] = {
    "checks": [],
    "pitfalls": [
        {
            "text": (
                "Treating 'mathematical fact' literally as a property of the number itself is a trap: "
                "the user accepted value-keyed real-world trivia (−91 → −91°F Alaska "
                "wind-chill record) as the intended reading, not math-internal observations."
            ),
            "evidence": [
                {
                    "quote": (
                        "You could have used information like -91 degrees fahrenheit was the lowest "
                        "(wind-chill) temp. in alaska"
                    ),
                    "post_id": "{POST_ID}",
                }
            ],
            "confidence": "low",
            "applies_when": (
                "Interpreting an ambiguous standing rule like 'tell a good mathematical fact along with "
                "whatever math question I ask' for a computed numeric answer."
            ),
            "does_not_apply_when": (
                "The conversation is explicitly about number theory, or the user has previously accepted "
                "factorization/primality trivia without correction."
            ),
        }
    ],
    "next_steps": [
        {
            "text": (
                "On the next arithmetic answer under a tell-a-fact rule, attach a physical/geographic "
                "record matching the exact result value (temperature, elevation, year, speed) and watch "
                "for absence of a 'bad fact' correction to confirm the value-keyed re"
            ),
            "evidence": [
                {
                    "quote": (
                        "pair the computed value with a physical/geographic record matching that exact "
                        "number (temperature, elevation, year, speed) instead of factoring or primality "
                        "observations"
                    ),
                    "post_id": "{POST_ID}",
                }
            ],
            "confidence": "low",
            "applies_when": (
                "A session with the same standing tell-a-fact rule produces a numeric result that "
                "plausibly maps to a real-world record (e.g. −91 → −91°F)."
            ),
            "does_not_apply_when": (
                "The result value has no natural real-world record analogue, or the user has since "
                "clarified what kind of fact they want."
            ),
        }
    ],
    "rejected_hypotheses": [
        {
            "text": (
                "When a standing rule requires pairing a math answer with a 'good mathematical fact', "
                "do NOT use number-theoretic properties of the result (factorization like 91 = 7 × 13, "
                "primality/pseudo-prime observations) — the user rejected this as a ba"
            ),
            "evidence": [
                {"quote": "No, this is a bad fact.", "post_id": "{POST_ID}"},
                {"quote": "91 = 7 × 13 pseudo-prime trap", "post_id": "{POST_ID}"},
            ],
            "confidence": "medium",
            "applies_when": (
                "A user has a standing instruction to attach an interesting fact to each computed "
                "arithmetic result and the candidate fact is a factorization or primality property of "
                "that result."
            ),
            "does_not_apply_when": (
                "The user explicitly asks for number-theoretic content (e.g. 'is it prime?', "
                "'factor this'), or no standing tell-a-fact rule is in effect."
            ),
        }
    ],
    "confirmed_constraints": [],
    "transferable_insights": [],
}


def trial_transcript() -> str:
    """A PTY-shaped reconstruction of the trial exchange. The load-bearing
    line — the user's verbatim correction, which the reflection cites as
    evidence — is exact; the surrounding turns are plausible scaffolding."""
    return (
        "> what is 13 * -7?\n"
        "13 × -7 = -91. Bonus mathematical fact: |-91| = 91 = 7 × 13 — a classic\n"
        "pseudo-prime trap (91 looks prime but isn't).\n"
        f"> {_TRIAL_CORRECTION}\n"
        "You're right — a value-keyed real-world record lands better: -91°F is the\n"
        "lowest wind-chill temperature recorded in Alaska.\n"
    )


def trial_reflection() -> dict[str, Any]:
    """The ★2 reflection the live ``/self-distill`` committed, verbatim."""
    return deepcopy(_TRIAL_REFLECTION)


def trial_reply() -> dict[str, Any]:
    """A disagree-stance reply engaging the reflection (for the /discuss leg)."""
    return deepcopy(_TRIAL_REPLY)


def trial_bundle(post_id: str) -> dict[str, Any]:
    """The live ``/cross-distill`` bundle, verbatim, with its evidence rebound
    to ``post_id`` (simulated posts mint fresh ids). Every quote is a verbatim
    substring of :func:`trial_reflection` fields, so the bundle passes the
    distill parser's grounding check against whatever post stores it."""
    bundle = deepcopy(_TRIAL_BUNDLE)
    for bucket in bundle.values():
        for item in bucket:
            for ev in item.get("evidence", []):
                ev["post_id"] = post_id
    return bundle


async def seed_trial_story(bank: FakeBank) -> dict[str, str]:
    """Plant the whole trial story in ``bank`` exactly as the live Bank holds
    it — ended session, registered agent, raw packet + scrubbed trace, the ★2
    reflection, and the cross-goal distill bundle citing it. For read-side
    tests (inject, retrieval, quarantine, oms.web) that start from existing
    knowledge rather than replaying the conversation. Returns the ids."""
    ids = {
        "session": "trial",
        "agent": "trial/agent-001-claude",
        "raw": "trial/951450c1",
        "post": "trial/kds77s64",
        "distill": "curator/3df0178cd6811aee23579272",
    }
    await bank.put_session(ids["session"], status="ended")
    await bank.put_agent(ids["agent"], session_id=ids["session"], adapter="claude", seq=1)
    await bank.put_packet({"id": ids["raw"], "session_id": ids["session"], "type": "raw", "agent_id": ids["agent"]})
    await bank.put_trace(ids["raw"], trial_transcript(), scrub_version=SCRUB_VERSION, complete=True)
    await bank.put_packet({
        "id": ids["post"],
        "session_id": ids["session"],
        "type": "post",
        "agent_id": "trial/mcp",
        "kind": "reflection",
        "structured": trial_reflection(),
        "rating": 2,
        "goal": None,
    })
    await bank.put_session("curator", status="active")
    await bank.put_packet({
        "id": ids["distill"],
        "session_id": "curator",
        "type": "distill",
        "agent_id": "curator",
        "scope": "cross_goal",
        "bundle": trial_bundle(ids["post"]),
        "parents": [ids["post"]],
        "curator": "local",
        "goal": None,
    })
    return ids


# --------------------------------------------------------------------------- #
# the simulation driver
# --------------------------------------------------------------------------- #


@dataclass
class StepResult:
    """One simulated verb's outcome: the handler's exit code + captured output."""

    rc: int
    out: list[str]

    def saw(self, fragment: str) -> bool:
        return any(fragment in line for line in self.out)

    @property
    def ok(self) -> bool:
        return self.rc == 0


class Simulation:
    """Context manager that wires the doubles into the real verbs.

    On ``__enter__`` it patches the three seams (``_handlers._adapter_for``,
    ``cli._pty_spawn``, ``distill.resolve._discover_local_model``), points
    ``OMS_HOME`` at a private dir (so the active-session file never touches
    the real ``~/.oms``), sets ``OMS_INSTALL_SKILLS=deny``, and clears the
    discuss-gate/packet-cache module state; ``__exit__`` restores everything.
    pytest-free by design — usable from scripts as well as tests."""

    def __init__(
        self,
        *,
        bank: FakeBank | None = None,
        adapter: DummyAdapter | None = None,
        home: Path | str | None = None,
    ) -> None:
        self.bank = bank if bank is not None else FakeBank()
        self.adapter = adapter if adapter is not None else DummyAdapter()
        self.curator_model = DummyModel()
        self._home = Path(home) if home is not None else None
        self._saved_env: dict[str, str | None] = {}
        self._saved_attrs: list[tuple[Any, str, Any]] = []
        self._transcript = ""

    # -- seam plumbing ------------------------------------------------------ #

    def __enter__(self) -> Simulation:
        from oms import _handlers as handlers_mod
        from oms import cli as cli_mod
        from oms.core import clear_packet_cache
        from oms.forum import clear_discuss_gate

        # `oms.distill.resolve` the *name* is the re-exported function; the
        # submodule must come from the import system (the test_e2e trap).
        resolve_mod = importlib.import_module("oms.distill.resolve")

        if self._home is None:
            self._home = Path(tempfile.mkdtemp(prefix="oms-sim-"))
        for key, value in (("OMS_HOME", str(self._home)), ("OMS_INSTALL_SKILLS", "deny")):
            self._saved_env[key] = os.environ.get(key)
            os.environ[key] = value
        for key in ("OMS_SESSION", "OMS_NONINTERACTIVE"):
            self._saved_env[key] = os.environ.pop(key, None)

        def patch(obj: Any, name: str, value: Any) -> None:
            self._saved_attrs.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def adapter_for(name: str, *, session_id: str, agent_id: str) -> DummyAdapter:
            return self.adapter.bind(session_id=session_id, agent_id=agent_id)

        patch(handlers_mod, "_adapter_for", adapter_for)
        patch(cli_mod, "_pty_spawn", self._play_transcript_through_pty)
        patch(resolve_mod, "_discover_local_model", lambda: self.curator_model)
        clear_discuss_gate()
        clear_packet_cache()
        return self

    def __exit__(self, *_exc: object) -> None:
        for obj, name, value in reversed(self._saved_attrs):
            setattr(obj, name, value)
        self._saved_attrs.clear()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._saved_env.clear()

    def _play_transcript_through_pty(self, argv: list[str], *, tee: Path | None = None) -> None:
        """The PTY stub: 'plays' the scripted conversation by writing it to the
        tee file, so `_do_run_agent`'s real M11.6 path (read tee → CanonicalTrace
        → validate → scrub → bound → persist) runs on dummy bytes."""
        if tee is not None:
            Path(tee).write_text(self._transcript, encoding="utf-8")

    # -- the verbs ----------------------------------------------------------- #

    async def start(self, session: str | None = None, *, goal: str | None = None) -> StepResult:
        from oms import cli

        argv = ["start", *([session] if session else []), *(["--goal", goal] if goal else [])]
        io = ScriptedIO()
        rc = await cli._do_start(cli._build_parser().parse_args(argv), bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    async def register(self, name: str | None = None) -> StepResult:
        from oms import cli

        io = ScriptedIO()
        args = cli._build_parser().parse_args(["register", name or self.adapter.name])
        rc = await cli._do_register(args, bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    async def run_agent(self, *agent_args: str, transcript: str) -> StepResult:
        """The ``oms <name>`` leg: the stubbed PTY plays ``transcript`` and the
        real capture pipeline persists it as a scrubbed ``raw`` packet."""
        from oms import cli

        self._transcript = transcript
        self.adapter.transcript = transcript
        io = ScriptedIO()
        rc = await cli._do_run_agent(self.adapter.name, list(agent_args), None, bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    async def self_distill(
        self,
        post: dict[str, Any] | str,
        *,
        rating: int | None = None,
        accept: bool = True,
        guidance: str | None = None,
    ) -> StepResult:
        """The agent 'writes' ``post``; the human accepts (★ ``rating``, or
        unrated when None) or rejects. A reject must persist nothing (C1)."""
        from oms import _handlers as h

        self.adapter.model.push(post)
        inputs = ("y", "skip" if rating is None else str(rating)) if accept else ("n",)
        io = ScriptedIO(*inputs)
        rc = await h.do_self_distill(adapter=self.adapter.name, guidance=guidance, bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    async def discuss(
        self,
        reply: dict[str, Any] | str,
        *,
        stance: str = "synthesize",
        packet: str | None = None,
        accept: bool = True,
    ) -> StepResult:
        from oms import _handlers as h

        self.adapter.model.push(reply)
        io = ScriptedIO("y" if accept else "n")
        rc = await h.do_discuss(adapter=self.adapter.name, stance=stance, packet=packet, bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    async def cross_distill(self, bundle: dict[str, Any] | str, *, server: bool = False) -> StepResult:
        from oms import _handlers as h

        self.curator_model.push(bundle)
        io = ScriptedIO()
        rc = await h.do_cross_distill(server=server, bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    async def inject(self, packet: str | None = None, *, accept: bool = True) -> StepResult:
        from oms import _handlers as h

        io = ScriptedIO("y" if accept else "n")
        rc = await h.do_inject(packet=packet, bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    async def end(self, *, rating: int | str = "skip") -> StepResult:
        from oms import cli

        io = ScriptedIO(str(rating))
        rc = await cli._do_end(cli._build_parser().parse_args(["end"]), bank=self.bank, io=io.pair())
        return StepResult(rc, io.out)

    # -- assertion helpers ---------------------------------------------------- #

    def packets(self, type_: str | None = None) -> list[dict[str, Any]]:
        """Bank packets, optionally filtered by type, in insertion order."""
        rows = list(self.bank._packets.values())
        return [dict(p) for p in rows if type_ is None or p.get("type") == type_]
