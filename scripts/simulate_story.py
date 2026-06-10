"""Simulate the Overview's Alice/Bob (… Carol/Dave/Erin) stories end-to-end
through the **real** ``oms.cli`` handlers on a shared in-memory FakeBank.

Nothing here is mocked except the three seams a real run would shell out to:
the wrapped agent's headless model (returns canned post JSON), the PTY spawn
(no-op recorder), and the curator's local-model discovery (returns a canned
6-bucket bundle). Every verb — start / register / <agent> / self-distill /
discuss / cross-distill / inject / end — and every guard (anti-meta,
verbatim-grounded evidence, retrieval-before-post, the idempotent curator,
the recurrence promotion, the behavioural reuse signal) is the shipped code.

Run:  uv run python scripts/simulate_story.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
from typing import Any

os.environ["OMS_CURATOR_MODE"] = "local"  # local curator; no server attempt
os.environ["OMS_HOME"] = tempfile.mkdtemp(prefix="oms-sim-")

from oms import cli
from oms.bank import FakeBank
from oms.capture.models import CanonicalTrace, TraceEvent

_resolve_mod = importlib.import_module("oms.distill.resolve")

# Mutable seam state: the next post the wrapped agent "writes", and the next
# bundle the curator "returns". Set immediately before the verb that consumes it.
STATE: dict[str, str] = {"post": "{}", "bundle": "{}"}


class _Model:
    def __init__(self, key: str) -> None:
        self._key = key

    def complete(self, _prompt: str, *, max_tokens: int | None = None) -> str:
        return STATE[self._key]


class _Adapter:
    """The wrapped-agent seam: a headless model returning canned post JSON and
    a capture() producing a minimal trace so the real pipeline writes a raw."""

    def __init__(self, name: str, session_id: str, agent_id: str) -> None:
        self.name = name
        self.binary = name
        self._sid = session_id
        self._aid = agent_id

    def distill_model(self) -> Any:
        return _Model("post")

    def capture(self) -> CanonicalTrace:
        return CanonicalTrace(
            session_id=self._sid,
            agent_id=self._aid,
            adapter=self.name,
            events=[TraceEvent(ts=0.0, kind="agent", text="(real work elided)")],
            source_fidelity="pty",
        )


from oms import _handlers as h  # noqa: E402

h._adapter_for = lambda name, *, session_id, agent_id: _Adapter(name, session_id, agent_id)  # type: ignore[assignment]
h._validate_adapter = lambda name: None  # type: ignore[assignment]  # doubles, not PATH binaries — skip the register gate
cli._pty_spawn = lambda argv, tee=None: None  # type: ignore[assignment]  # M11.6 added tee=
_resolve_mod._discover_local_model = lambda: _Model("bundle")  # type: ignore[attr-defined]


class _IO:
    """Captured-output io with a scripted input() queue."""

    def __init__(self, *responses: str) -> None:
        self._r = list(responses)
        self.out: list[str] = []

    def __call__(self, _prompt: str = "") -> str:
        # Fallback for UNSCRIPTED prompts must be "n": allowance gates are
        # affirmative-by-default (2026-06-10), so anything else silently
        # ACCEPTS e.g. the agent-exit "end session?" offer mid-story.
        return self._r.pop(0) if self._r else "n"

    def pair(self) -> tuple[Any, Any]:
        return (self, self.out.append)


def _args(*argv: str) -> Any:
    return cli._build_parser().parse_args(list(argv))


async def verb(bank: FakeBank, *argv: str, inputs: tuple[str, ...] = (), post: str | None = None) -> list[str]:  # noqa: C901 — one elif per verb is the dispatch; refactoring would be artificial
    """Drive one verb through its real handler. ``post`` sets the canned agent
    JSON for self-distill/discuss. M11.4: the four knowledge-loop verbs live
    in ``oms._handlers`` (kwargs API, no argparse coupling); the session-
    lifecycle verbs stay in ``oms.cli._DISPATCH``."""
    if post is not None:
        STATE["post"] = post
    io = _IO(*inputs)
    name = argv[0]
    if name == "run":  # `oms <adapter>` — the PTY leg
        rc = await cli._do_run_agent(argv[1], list(argv[2:]), None, bank=bank, io=io.pair())
    elif name == "self-distill":
        rc = await h.do_self_distill(
            adapter=argv[argv.index("--adapter") + 1] if "--adapter" in argv else "claude",
            bank=bank,
            io=io.pair(),
        )
    elif name == "discuss":
        kwargs: dict[str, Any] = {"adapter": "claude"}
        if "--adapter" in argv:
            kwargs["adapter"] = argv[argv.index("--adapter") + 1]
        if "--stance" in argv:
            kwargs["stance"] = argv[argv.index("--stance") + 1]
        # positional @packet
        for tok in argv[1:]:
            if tok.startswith("@"):
                kwargs["packet"] = tok
                break
        rc = await h.do_discuss(bank=bank, io=io.pair(), **kwargs)
    elif name == "cross-distill":
        rc = await h.do_cross_distill(server="--server" in argv, bank=bank, io=io.pair())
    elif name == "inject":
        packet = next((tok for tok in argv[1:] if tok.startswith("@")), None)
        rc = await h.do_inject(packet=packet, bank=bank, io=io.pair())
    else:  # start / register / end / status / uninstall — still on the CLI
        handler = cli._DISPATCH[name]
        rc = await handler(_args(*argv), bank=bank, io=io.pair())
    if rc != 0:
        raise SystemExit(f"verb {argv!r} failed (rc={rc}): {io.out}")
    return io.out


def _reflection(claim: str, evidence: str, nxt: str, outcome: str, conf: str) -> str:
    return json.dumps({
        "load_bearing_assumption": claim,
        "evidence": evidence,
        "evidence_ref": None,
        "proposed_next": nxt,
        "predicted_outcome": outcome,
        "confidence": conf,
    })


def _hr(title: str) -> None:
    print(f"\n{'═' * 78}\n  {title}\n{'═' * 78}")


def _show_packets(bank: FakeBank, goal: str | None = None) -> None:
    for p in sorted(bank._packets.values(), key=lambda r: str(r.get("created_at"))):
        if goal is not None and p.get("goal") != goal and p.get("type") != "distill":
            continue
        tag = p["type"]
        if tag == "post":
            tag = f"post/{p.get('kind')}"
        extra = ""
        if p["type"] == "post":
            extra = f"  ★={p.get('rating')}  goal={p.get('goal')!r}"
        if p["type"] == "distill":
            extra = f"  scope={p.get('scope')}  parents={len(p.get('parents', []))}"
        print(f"  [{tag:<14}] {p['id']:<26}{extra}")


def _show_bundle(bank: FakeBank, pid: str) -> None:
    b = bank._packets[pid].get("bundle") or {}
    for bucket, items in b.items():
        if not items:
            continue
        print(f"  • {bucket}:")
        for it in items:
            print(f"      - ({it['confidence']}) {it['text']}")
            print(f"        applies_when:        {it['applies_when']}")
            print(f"        does_not_apply_when: {it['does_not_apply_when']}")
            for ev in it["evidence"]:
                print(f'        ⮑ evidence {ev["post_id"]}: "{ev["quote"]}"')


async def story_a(bank: FakeBank) -> None:
    _hr("STORY A — goal-mediated serendipity (Alice → Bob, goal 'cfd-solver')")

    # --- Alice (Claude), session ALIC-E001 -------------------------------
    await verb(bank, "start", "cfd-solver", "--id", "ALIC-E001")
    await verb(bank, "register", "claude")
    await verb(bank, "run", "claude")  # a day of solver work → a raw packet
    await verb(
        bank,
        "self-distill",
        "--adapter",
        "claude",
        post=_reflection(
            "the default Poisson-solve `rtol` of 1e-6 silently under-converges for a "
            "lid-driven cavity and produces a checkerboard velocity mode by step 400",
            "residual plateaued at 3e-4 while the velocity field showed a checkerboard "
            "mode at step 400 on a 128x128 grid",
            "set the pressure-solve tolerance to `-ksp_rtol 1e-10` in PETSc; momentum stays 1e-6",
            "the checkerboard mode disappears; KSP iterations per step roughly double",
            "medium",
        ),
        inputs=("4",),  # single commit gate: bare 4 commits with ★4
    )
    await verb(bank, "end")
    alice_post = next(
        p["id"] for p in bank._packets.values() if p.get("kind") == "reflection" and p.get("goal") == "cfd-solver"
    )
    print(f"  Alice posted a falsifiable reflection ({alice_post}), ★4, then `oms end`.")
    print("  She told no one. The goal — not a session id — is the only key.")

    # --- Bob (Codex), a different org, SAME goal, new session ------------
    await verb(bank, "start", "cfd-solver", "--id", "BOBB-2K7Q")
    await verb(bank, "register", "codex")

    # The curator is goal-scoped *across sessions* — it reaches Alice's post.
    STATE["bundle"] = json.dumps({
        "confirmed_constraints": [
            {
                "text": "set the pressure-solve `rtol` to 1e-10 for implicit projection so the "
                "Poisson solve does not silently under-converge",
                "applies_when": "implicit pressure projection on a lid-driven cavity",
                "does_not_apply_when": "explicit or compressible solvers, or matrix-free multigrid smoothers",
                "evidence": [{"post_id": alice_post, "quote": "produces a checkerboard velocity mode by step 400"}],
                "confidence": "medium",
            }
        ],
        "checks": [
            {
                "text": "after tightening `-ksp_rtol`, watch KSP iterations per step as the regression signal",
                "applies_when": "implicit projection solvers after a tolerance change",
                "does_not_apply_when": "explicit time stepping",
                "evidence": [{"post_id": alice_post, "quote": "residual plateaued at 3e-4"}],
                "confidence": "medium",
            }
        ],
    })
    out = await verb(bank, "cross-distill")
    print(f"  Bob ran /cross-distill under 'cfd-solver': {out[-1]}")
    bundle_id = next(p["id"] for p in bank._packets.values() if p["type"] == "distill")
    _show_bundle(bank, bundle_id)

    await verb(bank, "inject", f"@{bundle_id}", inputs=("y",))
    print(f"  Bob /inject'd the bundle → injection ledger row (p={bundle_id} → BOBB-2K7Q).")

    await verb(bank, "run", "codex")  # sets rtol 1e-10 on day 1; never hits the checkerboard
    await verb(
        bank,
        "self-distill",
        "--adapter",
        "codex",
        post=_reflection(
            "applying `-ksp_rtol` 1e-10 to the pressure solve from day one keeps a 64^3 "
            "lid-driven cavity free of the checkerboard mode",
            "no checkerboard appeared through step 600 at 64 cubed with the tightened tolerance",
            "keep momentum at 1e-6 and track `KSP` iterations per step as the regression signal",
            "stable velocity field; about 2x KSP iterations per step versus the loose tolerance",
            "high",
        ),
        inputs=("5",),  # Bob's session went well: ★5
    )
    # /discuss engages the in-session thread (retrieval-before-post is enforced).
    await verb(
        bank,
        "discuss",
        "--adapter",
        "codex",
        "--stance",
        "agree",
        post=_reflection(
            "the tightened `-ksp_rtol` 1e-10 result reproduces at 64^3 exactly as predicted",
            "checkerboard absent through step 600; KSP iters per step rose from 11 to 23",
            "promote the pressure-tolerance setting as a default `checks` item",
            "future cfd-solver sessions avoid the day-one checkerboard",
            "high",
        ),
        inputs=("y",),
    )
    await verb(bank, "end")

    score = next(s for s in await bank.reuse_score(bundle_id))
    _hr("STORY A — payoff: the behavioural reuse signal")
    print(f"  Bob's session ended ★5. reuse_score({bundle_id}) = {score}")
    print(f"  The injected bundle's parents = {bank._packets[bundle_id]['parents']}")
    print("  → Alice's claim, carried by the bundle Bob reused and rated well, now has a")
    print("    hard-to-game behavioural score. Nobody coordinated; the goal mediated it.")


async def story_b(bank: FakeBank) -> None:
    _hr("STORY B — pruning a dead end (Carol → Dave → Erin, goal 'rust-async-runtime')")

    # Carol (Gemini): a confident reflection that is *wrong above a threshold*.
    await verb(bank, "start", "rust-async-runtime", "--id", "CARO-L001")
    await verb(bank, "register", "gemini")
    await verb(bank, "run", "gemini")
    await verb(
        bank,
        "self-distill",
        "--adapter",
        "gemini",
        post=_reflection(
            "calling `tokio::spawn` per task inside the hot request loop is fine at our throughput",
            "a 30-minute soak at 800 tasks per second showed no measurable scheduler overhead",
            "keep the per-task `tokio::spawn` and revisit only if throughput targets rise",
            "latency stays flat as load grows",
            "high",
        ),
        inputs=("4",),
    )
    await verb(bank, "end")
    carol_post = next(
        p["id"]
        for p in bank._packets.values()
        if p.get("kind") == "reflection" and p.get("goal") == "rust-async-runtime"
    )
    print(f"  Carol posted a confident claim ({carol_post}, ★4): per-task spawn 'is fine'.")

    # Dave (Claude), same goal: his session *refutes* it, with a flamegraph.
    await verb(bank, "start", "rust-async-runtime", "--id", "DAVE-3X09")
    await verb(bank, "register", "claude")
    await verb(bank, "run", "claude")
    await verb(
        bank,
        "self-distill",
        "--adapter",
        "claude",
        post=_reflection(
            "per-task `tokio::spawn` in the hot loop collapses above roughly 10k tasks per "
            "second as scheduler overhead dominates",
            "a flamegraph showed 38 percent of CPU in tokio spawn at 12k tasks per second; "
            "a bounded worker pool removed it",
            "replace per-task spawn with a fixed `JoinSet` worker pool sized to cores",
            "tail latency drops sharply above 10k tasks per second",
            "high",
        ),
        inputs=("5",),
    )
    dave_post = next(
        p["id"]
        for p in bank._packets.values()
        if p.get("kind") == "reflection" and p.get("goal") == "rust-async-runtime" and p["id"] != carol_post
    )
    print(f"  Dave posted the refuting counter-evidence ({dave_post}, ★5).")

    # The goal's curator now sees a confident claim AND a contradicting post.
    STATE["bundle"] = json.dumps({
        "rejected_hypotheses": [
            {
                "text": "per-task `tokio::spawn` in a hot loop does not hold at high throughput; "
                "use a bounded worker pool",
                "applies_when": "request hot loops above roughly 10k tasks per second",
                "does_not_apply_when": "low-rate or one-shot spawning below roughly 1k tasks per second",
                "evidence": [{"post_id": dave_post, "quote": "a flamegraph showed 38 percent of CPU"}],
                "confidence": "high",
            }
        ],
    })
    out = await verb(bank, "cross-distill")
    dave_bundle = sorted(
        (p for p in bank._packets.values() if p["type"] == "distill"),
        key=lambda r: str(r.get("created_at")),
    )[-1]["id"]
    print(f"  Dave ran /cross-distill: {out[-1]}")
    _show_bundle(bank, dave_bundle)

    # Erin, a week later, same goal. Same posts ⇒ the curator is *idempotent*
    # (no re-spend); she inherits the corrected bundle and is warned off.
    await verb(bank, "start", "rust-async-runtime", "--id", "ERIN-4Z42")
    await verb(bank, "register", "codex")
    out = await verb(bank, "cross-distill")
    erin_bundle = sorted(
        (p for p in bank._packets.values() if p["type"] == "distill"),
        key=lambda r: str(r.get("created_at")),
    )[-1]["id"]
    print(f"  Erin ran /cross-distill: {out[-1]}")
    print(f"  Idempotent: Erin's bundle id == Dave's ({erin_bundle == dave_bundle}) — no re-spend.")
    await verb(bank, "inject", f"@{erin_bundle}", inputs=("y",))
    print("  Erin /inject'd it: the bundle's `rejected_hypotheses` warns her off the spawn")
    print("  path and names the threshold. The corpus did not just accumulate — it")
    print("  *corrected itself*: refutation is first-class, demoted with a boundary.")


async def story_c(bank: FakeBank) -> None:
    _hr("STORY C — cross-goal transfer (a primitive recurs across unrelated goals)")

    primitive = (
        (
            "CFDX-C001",
            "cfd-solver",
            "long mixed-precision reductions in the residual norm need `math.fsum` style "
            "compensated summation or you lose about 3 digits",
        ),
        (
            "MLTR-C002",
            "ml-training-loop",
            "the gradient-accumulation reduction loses about 3 digits without `math.fsum` "
            "compensated summation across micro-batches",
        ),
        (
            "GAME-C003",
            "game-physics",
            "the contact-impulse accumulator drifts over long frames without `math.fsum` compensated summation",
        ),
    )
    posts: list[tuple[str, str]] = []
    for sid_, goal, claim in primitive:
        await verb(bank, "start", goal, "--id", sid_)
        await verb(bank, "register", "claude")
        await verb(bank, "run", "claude")
        await verb(
            bank,
            "self-distill",
            "--adapter",
            "claude",
            post=_reflection(
                claim,
                "double-precision accumulation diverged from a Kahan baseline by ~3 digits",
                "switch the reduction to `math.fsum` / compensated summation",
                "the lost low-order digits are recovered",
                "low",
            ),
            inputs=("skip",),  # unrated is a first-class valid state
        )
        await verb(bank, "end")
        pid = sorted(
            (p for p in bank._packets.values() if p.get("kind") == "reflection" and p.get("goal") == goal),
            key=lambda r: str(r.get("created_at")),
        )[-1]["id"]
        posts.append((pid, sid_))
    print("  Three practitioners, three unrelated goals, the same primitive — each said")
    print("  it with 'low' confidence; none would generalise it alone.")

    # A newcomer to *anything* numerically heavy: a session with NO goal lands
    # in the default bucket (OMS_DEFAULT_GOAL) ⇒ the CLI selects
    # scope=cross_goal. The (canned) curator emits ONE insight with confidence
    # 'low'; the **real** parser sees evidence from ≥2 distinct sessions and
    # mechanically promotes it to high (recurrence promotion).
    STATE["bundle"] = json.dumps({
        "transferable_insights": [
            {
                "text": "long mixed-precision reductions need `math.fsum` / compensated summation",
                "applies_when": "any long reduction in mixed or low precision",
                "does_not_apply_when": "short reductions or exact-integer accumulation",
                "evidence": [
                    {"post_id": posts[0][0], "quote": "you lose about 3 digits"},
                    {"post_id": posts[1][0], "quote": "loses about 3 digits without"},
                    {"post_id": posts[2][0], "quote": "drifts over long frames"},
                ],
                "confidence": "low",  # the model's guess …
            }
        ],
    })
    await verb(bank, "start", "--id", "NEWB-5Q00")  # no goal ⇒ default bucket ⇒ cross_goal
    await verb(bank, "register", "gemini")
    out = await verb(bank, "cross-distill")
    xbundle = sorted(
        (p for p in bank._packets.values() if p["type"] == "distill"),
        key=lambda r: str(r.get("created_at")),
    )[-1]["id"]
    print(f"  Newcomer (no goal) ran /cross-distill: {out[-1]}")
    insight = bank._packets[xbundle]["bundle"]["transferable_insights"][0]
    print(f"  Curator model proposed confidence='low'; parser emitted confidence='{insight['confidence']}'.")
    print(
        f"  → recurrence promotion: cited posts span "
        f"{len({p[1] for p in posts})} distinct sessions ⇒ forced HIGH "
        f"(real mechanical code, not the model)."
    )
    _show_bundle(bank, xbundle)


async def main() -> None:
    bank = FakeBank()
    await story_a(bank)
    await story_b(bank)
    await story_c(bank)

    _hr("FINAL BANK STATE (one shared corpus, every packet written by a real verb)")
    _show_packets(bank)
    print(f"\n  sessions={len(bank._sessions)}  packets={len(bank._packets)}  injections={len(bank._injections)}")
    print("  Every post passed the mechanical anti-meta + verbatim-evidence parser;")
    print("  every bundle is verbatim-grounded; reuse is behavioural; nothing mocked")
    print("  but the agent model, the PTY, and the curator's local-model discovery.")


if __name__ == "__main__":
    asyncio.run(main())
