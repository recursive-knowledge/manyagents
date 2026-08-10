"""A resilient, repeated, multi-agent rig for the KSI protocol.

``scripts/simulate_ksi_e2e.py`` runs the protocol once, with one agent, on one
toy transcript, and reports whatever that single sample happened to do. That is
enough to show the loop *can* close and not enough to trust a number: a run that
closes the loop and a run that stalls at the first gate look equally like "the
result". This rig replaces that with something you can argue from.

What changed, and why each change matters:

* **Repetition.** Every metric is a rate over ``--repeat`` independent runs, not
  a single observation. Rates print as ``k/n`` so a small denominator is visible
  rather than hidden behind a percentage.
* **Multi-agent sessions.** ``/discuss`` retrieval is session-local
  (``forum.discuss.retrieve``), so a session holding one post has nothing to
  engage and the verb can never succeed. Each generation now writes two posts
  into one session before replying, which is the first configuration in which
  the reply path is actually reachable.
* **Cross-session corpus.** A second session under the same goal contributes a
  *contradicting* finding, so ``/cross-distill`` sees disagreement across
  sessions — the case where ``rejected_hypotheses`` and the confidence rules
  have anything to do.
* **Realistic traces.** The transcripts carry failed attempts with concrete
  parameterizations, tool output, and error text, not a single tidy sentence.
  The post's ``evidence`` must be a verbatim excerpt of one, so a toy trace
  makes the grounding check trivially easy and the result meaningless.
* **A real second generation.** Generation 2 injects the bundle and then
  *attempts the task again*, so "the loop closed" means knowledge reached a
  later agent, not that a ledger row was written.
* **Resilience.** Every step is attempted through a retry/backoff wrapper and
  its failure is recorded, not raised. One bad sample degrades a rate; it never
  ends the run. Infrastructure failure (endpoint down) is counted separately
  from protocol failure (a guard rejected the content), because conflating them
  is how a broken endpoint gets read as a broken protocol.

Usage::

    MANYAGENT_LLM_BASE_URL=http://localhost:8000/v1 \
    MANYAGENT_LLM_API_KEY=local \
    MANYAGENT_LLM_MODEL=qwen3.5-9b \
    uv run python scripts/ksi_rig.py --repeat 5

``--json out.json`` writes every per-run record for later analysis.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from manyagent.distill.resolve import _OpenAICompatModel
from manyagent.testing import DummyAdapter, Simulation

# --------------------------------------------------------------------------- #
# Scenario material — three domains, mirroring the benchmark families KSI
# evaluates. Each domain supplies a messy trace per role. The `contra` trace
# disagrees with `lead` on purpose so the corpus contains a real conflict.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Domain:
    key: str
    goal: str
    lead: str
    second: str
    contra: str
    gen2: str
    guidance_a: str
    guidance_b: str


DOMAINS: tuple[Domain, ...] = (
    Domain(
        key="terminal",
        goal="terminal-service-restart",
        lead="""\
user: bad nginx configs keep reaching production even though deploy restarts the service
agent: $ systemctl restart nginx
agent: (exit 0)
agent: the unit reported success, so the deploy step passed
agent: $ curl -sS localhost/health
agent: 502 Bad Gateway
agent: so the restart "succeeded" while the site is down
agent: $ nginx -t
agent: nginx: [emerg] duplicate listen 0.0.0.0:80 in /etc/nginx/sites-enabled/api:12
agent: systemctl restart nginx returned 0 while the config was still invalid
agent: tried: reload instead of restart
agent: $ systemctl reload nginx
agent: (exit 0) -- reload also returns 0 on a bad config, so that is not the fix
agent: adding `nginx -t` ahead of the restart in deploy.sh makes the step fail loudly
user: ship it
""",
        second="""\
agent: second pass on the same session, looking at the rollback path
agent: $ systemctl status nginx --no-pager
agent: Active: active (running) since 09:14; main PID 811 (nginx)
agent: the OLD master process is still serving; the new config never loaded
agent: so a failed reload leaves stale workers alive with no error surfaced
agent: $ journalctl -u nginx -n 5 --no-pager
agent: nginx[811]: signal process started
agent: the unit logs a [warning] and keeps the previous binary
""",
        contra="""\
user: same deploy problem on the staging box
agent: $ nginx -t && systemctl restart nginx
agent: nginx: configuration file /etc/nginx/nginx.conf test is successful
agent: (exit 0)
agent: but staging still served the old config afterwards
agent: $ systemctl show nginx -p MainPID
agent: MainPID=811
agent: same PID as before the restart -- so `nginx -t` passing does NOT guarantee a reload happened
agent: the check is necessary but not sufficient; the unit needs ExecReload wired correctly
""",
        gen2="""\
user: harden the deploy script for the api service
agent: $ nginx -t
agent: nginx: [emerg] unknown directive "proxy_pas" in /etc/nginx/sites-enabled/api:31
agent: caught before restart this time
agent: $ sed -i 's/proxy_pas /proxy_pass /' /etc/nginx/sites-enabled/api
agent: $ nginx -t && systemctl restart nginx
agent: configuration file test is successful
""",
        guidance_a="Write about the exit-code behaviour of the restart command.",
        guidance_b="Write about what happens to the old worker process after a failed reload.",
    ),
    Domain(
        key="coding",
        goal="swe-async-timeout",
        lead="""\
user: test_stream_large_payload is flaky in CI, passes locally
agent: $ pytest tests/test_stream.py::test_stream_large_payload
agent: FAILED - asyncio timeout after 5.0s
agent: the 5s bound comes from asyncio_default_timeout in setup.cfg
agent: tried: bump the per-test marker only
agent: @pytest.mark.asyncio(timeout=30) -- still failed, the marker does not override the global
agent: $ grep -n asyncio_default_timeout setup.cfg
agent: 12:asyncio_default_timeout = 5
agent: raising asyncio_default_timeout to 30 in setup.cfg makes the test pass
""",
        second="""\
agent: second look -- why does it need 30s at all
agent: $ python -X importtime -c "import app.stream" 2>&1 | tail -3
agent: import of app.stream took 2.1s
agent: that is not it; the time is in the pool
agent: $ pytest tests/test_stream.py -k large --durations=3
agent: 28.40s call     tests/test_stream.py::test_stream_large_payload
agent: connection_pool.acquire() waited 28.4s then succeeded, max_size=2 with 3 waiters
""",
        contra="""\
user: the same suite on the release branch
agent: $ pytest tests/test_stream.py::test_stream_large_payload
agent: PASSED in 1.2s
agent: release branch has max_size=8 in connection_pool.py
agent: so raising asyncio_default_timeout was never required -- the pool size was the real bound
agent: reverting the setup.cfg change on release keeps the test green
""",
        gen2="""\
user: same flake showed up in test_stream_chunked
agent: $ pytest tests/test_stream.py::test_stream_chunked --durations=3
agent: 27.90s call     tests/test_stream.py::test_stream_chunked
agent: $ grep -n max_size app/connection_pool.py
agent: 18:max_size = 2
agent: raising max_size to 8 in connection_pool.py fixed it without touching setup.cfg
""",
        guidance_a="Write about the timeout configuration that produced the failure.",
        guidance_b="Write about where the wall-clock time was actually spent.",
    ),
    Domain(
        key="reasoning",
        goal="arc-grid-recolour",
        lead="""\
user: solve the recolour task, 5 training grids
agent: hypothesis 1: a global colour map keyed on the input colour alone
agent: applied map {1:8, 2:3, 3:4} to all grids
agent: grid 3 of 5 mismatched: expected 8 at (2,1), produced 3
agent: so a colour-only key is too coarse
agent: hypothesis 2: key the map on (colour, cell_degree) with degree over 4-neighbours
agent: grid 5 mismatch at (0,3): degree=2 mapped to 4, expected 6
agent: two parameterizations of the colour-map family failed, but the family is not exhausted
""",
        second="""\
agent: continuing on the same task, looking at topology instead of counts
agent: computed enclosed_by_ring via a flood fill from the border
agent: grids 4 and 5 both contain a closed ring; grids 1-3 do not
agent: keying the colour map on (colour, enclosed_by_ring) matched all 5 training grids
agent: submitted; evaluator returned 1.0
""",
        contra="""\
user: a sibling recolour task from the same set
agent: tried keying on (colour, enclosed_by_ring) straight away
agent: grid 2 mismatched at (4,4): the ring predicate fired on an open shape
agent: the flood fill treats a 1-cell gap as enclosed at 4-connectivity
agent: switching the fill to 8-connectivity fixed the predicate on this task
agent: so enclosed_by_ring transfers only when the fill connectivity matches the task's gap size
""",
        gen2="""\
user: new recolour task, 4 training grids
agent: checked for ring structure first
agent: flood fill at 8-connectivity marks grids 2 and 4 as enclosed
agent: keying on (colour, enclosed_by_ring) matched all 4 training grids on the first attempt
""",
        guidance_a="Write about the hypothesis that was falsified and how it was parameterized.",
        guidance_b="Write about the predicate that finally matched every training grid.",
    ),
)


# --------------------------------------------------------------------------- #
# Live model with retry. A transient 5xx or a dropped connection is
# infrastructure, not a protocol result, so it is retried and — if it still
# fails — recorded under its own counter instead of poisoning a protocol rate.
# --------------------------------------------------------------------------- #


class InfraError(RuntimeError):
    """The endpoint failed after retries. Not a protocol outcome."""


class LiveModel:
    def __init__(self, *, retries: int = 3, backoff: float = 0.5) -> None:
        self.responses: list[Any] = []
        self.calls = 0
        self.infra_failures = 0
        self._retries = retries
        self._backoff = backoff

    def push(self, _response: Any) -> None:
        return  # the live endpoint writes its own answer

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        client = _OpenAICompatModel(
            base_url=os.environ.get("MANYAGENT_LLM_BASE_URL", "http://localhost:8000/v1"),
            api_key=os.environ.get("MANYAGENT_LLM_API_KEY", "local"),
            model=os.environ.get("MANYAGENT_LLM_MODEL", "qwen3.5-9b"),
        )
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                self.calls += 1
                return client.complete(prompt, max_tokens=max_tokens)
            except Exception as exc:  # transient endpoint trouble
                last = exc
                time.sleep(self._backoff * (2**attempt))
        self.infra_failures += 1
        raise InfraError(f"endpoint failed after {self._retries} attempts: {last}")


# --------------------------------------------------------------------------- #
# Step bookkeeping
# --------------------------------------------------------------------------- #


@dataclass
class Run:
    domain: str
    seed: int
    steps: list[dict[str, Any]] = field(default_factory=list)
    posts_stored: int = 0
    posts_attempted: int = 0
    rejections: list[str] = field(default_factory=list)
    replies_stored: int = 0
    replies_attempted: int = 0
    bundles: int = 0
    insights: int = 0
    gen2_injected: bool = False
    gen2_post_stored: bool = False
    infra_failures: int = 0

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"step": name, "ok": ok, "detail": detail[:200]})


VERBOSE = False


async def _attempt(run: Run, name: str, coro_fn: Any) -> Any:
    """Run one protocol step. Never raises; classifies infra vs protocol.

    Catches ``BaseException`` deliberately: the CLI signals "no active session"
    and similar operator errors with ``SystemExit``, which is NOT an
    ``Exception``. A rig that only catches ``Exception`` therefore dies on the
    first such step and loses every run already completed — which is exactly
    what this rig exists to prevent.
    """
    try:
        res = await coro_fn()
    except InfraError as exc:
        run.infra_failures += 1
        run.record(name, False, f"INFRA {exc}")
        if VERBOSE:
            print(f"      {name}: INFRA {exc}")
        return None
    except BaseException as exc:
        run.record(name, False, f"{type(exc).__name__}: {exc}")
        if VERBOSE:
            print(f"      {name}: {type(exc).__name__}: {exc}")
        return None
    ok = getattr(res, "rc", 1) == 0
    detail = " | ".join(str(x) for x in getattr(res, "out", []))
    run.record(name, ok, detail[:200])
    if VERBOSE:
        print(f"      {name}: rc={getattr(res, 'rc', '?')} {detail[:140]}")
    return res


def _rejection_reason(res: Any) -> str | None:
    for line in getattr(res, "out", []) or []:
        text = str(line)
        if "rejected by the discipline" in text or "refused" in text:
            return text.split(":", 1)[-1].strip()[:90]
    return None


# --------------------------------------------------------------------------- #
# One full protocol run
# --------------------------------------------------------------------------- #


async def _gen1_session(sim: Any, run: Run, dom: Domain, *, adapter: str, trace: str, guidance: str) -> list[str]:
    """One session: run the agent, then post. Returns post ids now in the Bank."""
    sim.adapter = DummyAdapter(name=adapter, transcript=trace)
    sim.adapter.model = sim.shared_model
    await _attempt(run, f"start[{adapter}]", lambda: sim.start(goal=dom.goal))
    await _attempt(run, f"run[{adapter}]", lambda: sim.run_agent(transcript=trace))
    run.posts_attempted += 1
    res = await _attempt(run, f"self_distill[{adapter}]", lambda: sim.self_distill({}, rating=4, guidance=guidance))
    if res is not None:
        reason = _rejection_reason(res)
        if reason:
            run.rejections.append(reason)
    posts = [p for p in await sim.bank.list_packets(type="post") if p.get("goal") == dom.goal]
    return [str(p["id"]) for p in posts]


async def one_run(dom: Domain, seed: int, *, model: LiveModel) -> Run:
    run = Run(domain=dom.key, seed=seed)
    adapter = DummyAdapter(name="claude", transcript=dom.lead)
    adapter.model = model  # type: ignore[assignment]

    with Simulation(adapter=adapter) as sim:
        sim.curator_model = model  # type: ignore[assignment]
        sim.shared_model = model  # type: ignore[attr-defined]

        # --- generation 1, session one: two posts so /discuss has material --- #
        before = await _gen1_session(sim, run, dom, adapter="claude", trace=dom.lead, guidance=dom.guidance_a)
        run.posts_stored = len(before)

        run.posts_attempted += 1
        res = await _attempt(
            run, "self_distill[claude#2]", lambda: sim.self_distill({}, rating=3, guidance=dom.guidance_b)
        )
        if res is not None and (reason := _rejection_reason(res)):
            run.rejections.append(reason)
        posts = [p for p in await sim.bank.list_packets(type="post") if p.get("goal") == dom.goal]
        run.posts_stored = len(posts)

        # --- the reply path, now reachable: >=1 post exists in this session --- #
        if len(posts) >= 1:
            run.replies_attempted += 1
            target = f"@{posts[0]['id']}"
            await _attempt(run, "discuss", lambda: sim.discuss({}, stance="synthesize", packet=target))
            run.replies_stored = sum(1 for p in await sim.bank.list_packets(type="post") if p.get("kind") == "reply")

        await _attempt(run, "end[claude]", lambda: sim.end(rating="skip"))

        # --- generation 1, session two: a contradicting finding --- #
        await _gen1_session(sim, run, dom, adapter="codex", trace=dom.contra, guidance=dom.guidance_a)
        run.posts_stored = len([p for p in await sim.bank.list_packets(type="post") if p.get("goal") == dom.goal])

        # --- curate WHILE a session is active --- #
        # /cross-distill is an in-session verb: do_cross_distill resolves the
        # active session and exits if there is none. Curating after `end` is a
        # rig error, not a protocol result, and it cost a whole run the first
        # time round.
        await _attempt(run, "cross_distill", lambda: sim.cross_distill({}))
        await _attempt(run, "end[codex]", lambda: sim.end(rating="skip"))
        bundles = await sim.bank.list_packets(type="distill")
        run.bundles = len(bundles)
        run.insights = sum(len(v) for b in bundles for v in (b.get("bundle") or {}).values() if isinstance(v, list))

        # --- generation 2: inject, then ATTEMPT AGAIN --- #
        sim.adapter = DummyAdapter(name="gemini", transcript=dom.gen2)
        sim.adapter.model = model  # type: ignore[assignment]
        await _attempt(run, "start[gen2]", lambda: sim.start(goal=dom.goal))
        inj = await _attempt(run, "inject[gen2]", lambda: sim.inject())
        run.gen2_injected = inj is not None and getattr(inj, "rc", 1) == 0
        await _attempt(run, "run[gen2]", lambda: sim.run_agent(transcript=dom.gen2))
        run.posts_attempted += 1
        before_n = len([p for p in await sim.bank.list_packets(type="post") if p.get("goal") == dom.goal])
        res = await _attempt(run, "self_distill[gen2]", lambda: sim.self_distill({}, rating=5))
        if res is not None and (reason := _rejection_reason(res)):
            run.rejections.append(reason)
        after_n = len([p for p in await sim.bank.list_packets(type="post") if p.get("goal") == dom.goal])
        run.gen2_post_stored = after_n > before_n
        run.posts_stored = after_n
        await _attempt(run, "end[gen2]", lambda: sim.end(rating="skip"))

    return run


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _rate(k: int, n: int) -> str:
    return f"{k}/{n}" + (f" ({k / n:.0%})" if n else "")


def report(runs: list[Run], model: LiveModel) -> None:
    n = len(runs)
    print(f"\n{'=' * 74}\nKSI PROTOCOL RIG — {n} runs across {len({r.domain for r in runs})} domains\n{'=' * 74}")

    posts_att = sum(r.posts_attempted for r in runs)
    posts_ok = sum(1 for r in runs for s in r.steps if s["step"].startswith("self_distill") and s["ok"])
    reply_att = sum(r.replies_attempted for r in runs)
    reply_ok = sum(1 for r in runs if r.replies_stored > 0)

    print("\nPER-STEP SUCCESS (protocol outcomes)")
    print(f"  /self-distill accepted   : {_rate(posts_ok, posts_att)}")
    print(f"  /discuss stored a reply  : {_rate(reply_ok, reply_att)}")
    print(f"  /cross-distill bundle    : {_rate(sum(1 for r in runs if r.bundles), n)}")
    print(f"  gen2 injected            : {_rate(sum(1 for r in runs if r.gen2_injected), n)}")
    print(f"  gen2 posted after inject : {_rate(sum(1 for r in runs if r.gen2_post_stored), n)}")
    # Two different claims, kept apart on purpose. "Transferred" means gen-2
    # actually received curated knowledge. "Full cycle" additionally means
    # gen-2 contributed back, which is what makes the loop generational rather
    # than a one-way read.
    transferred = sum(1 for r in runs if r.bundles and r.gen2_injected)
    closed = sum(1 for r in runs if r.bundles and r.gen2_injected and r.gen2_post_stored)
    print(f"  knowledge REACHED gen2   : {_rate(transferred, n)}")
    print(f"  FULL CYCLE (gen2 posted) : {_rate(closed, n)}")

    ins = sorted(r.insights for r in runs)
    if ins:
        print("\nBUNDLE YIELD (insights kept, per run)")
        print(f"  min={ins[0]}  median={ins[len(ins) // 2]}  max={ins[-1]}")

    reasons: Counter[str] = Counter(x for r in runs for x in r.rejections)
    if reasons:
        print("\nREJECTION REASONS")
        for reason, count in reasons.most_common():
            print(f"  {count:3d}x {reason}")

    by_dom: dict[str, list[Run]] = {}
    for r in runs:
        by_dom.setdefault(r.domain, []).append(r)
    print("\nBY DOMAIN")
    for dom, rs in sorted(by_dom.items()):
        c = sum(1 for r in rs if r.bundles and r.gen2_injected and r.gen2_post_stored)
        print(
            f"  {dom:10s} loop closed {_rate(c, len(rs))}  insights median={sorted(r.insights for r in rs)[len(rs) // 2]}"
        )

    infra = sum(r.infra_failures for r in runs)
    print(f"\nINFRASTRUCTURE (not protocol): {infra} failed step(s); {model.calls} model calls")
    if infra:
        print("  ! infra failures present — protocol rates above are understated")


async def main_async(args: argparse.Namespace) -> int:
    # Seeded only so a sweep is reproducible; nothing here is security-sensitive.
    rng = random.Random(args.seed)  # noqa: S311
    model = LiveModel(retries=args.retries)
    doms = [d for d in DOMAINS if args.domain in ("all", d.key)]
    runs: list[Run] = []
    total = len(doms) * args.repeat
    i = 0
    for rep in range(args.repeat):
        for dom in doms:
            i += 1
            seed = rng.randrange(1 << 30)
            print(f"[{i}/{total}] {dom.key} rep={rep + 1} seed={seed} ...", flush=True)
            try:
                runs.append(await one_run(dom, seed, model=model))
            except Exception as exc:  # a rig bug must not lose the runs already done
                print(f"    rig error: {type(exc).__name__}: {exc}")
                bad = Run(domain=dom.key, seed=seed)
                bad.record("rig", False, f"{type(exc).__name__}: {exc}")
                runs.append(bad)

    report(runs, model)
    if args.out:
        payload = [
            {
                "domain": r.domain,
                "seed": r.seed,
                "posts_stored": r.posts_stored,
                "posts_attempted": r.posts_attempted,
                "replies_stored": r.replies_stored,
                "bundles": r.bundles,
                "insights": r.insights,
                "gen2_injected": r.gen2_injected,
                "gen2_post_stored": r.gen2_post_stored,
                "rejections": r.rejections,
                "infra_failures": r.infra_failures,
                "steps": r.steps,
            }
            for r in runs
        ]
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nwrote {args.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeat", type=int, default=3, help="runs per domain")
    ap.add_argument("--domain", default="all", choices=["all", *(d.key for d in DOMAINS)])
    ap.add_argument("--retries", type=int, default=3, help="endpoint retries per call")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", dest="out", help="write per-run records here")
    ap.add_argument("--verbose", action="store_true", help="print every step outcome")
    args = ap.parse_args()
    global VERBOSE
    VERBOSE = args.verbose
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
