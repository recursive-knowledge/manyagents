"""One complete KSI generation, end to end, driven by a real LLM.

``scripts/simulate_story.py`` already runs every verb, but its model seams
return canned JSON, so the *protocol* is exercised and the *model* is not.
``scripts/simulate_ksi.py`` does the opposite: a real model, but only the
distillation stage, over hand-authored posts. Neither answers the question this
script exists for — **can a live model, given manyagent's actual prompts, carry a
lesson through the whole protocol and survive every shipped guard?**

So this drives the real verbs from ``manyagent.testing.Simulation`` with a live
OpenAI-compatible endpoint wired into both model seams:

    session start -> register -> run agent (produces the trace)
      -> /self-distill   real model writes the post; parse_post enforces
                         anti-meta, concreteness, and verbatim grounding
                         against the trace it was shown
      -> /discuss        real model writes a stance reply; the
                         retrieval-before-post gate applies
      -> /cross-distill  real curator; the real curate() state machine —
                         clustering, weighting, content-addressed idempotency,
                         packet persistence
      -> /inject         preview + human gate + the reuse ledger
      -> session end

Generation 2 then starts a fresh session on the same goal and injects the
bundle, which is the step that closes the KSI loop: a *different* agent begins
from knowledge the first generation produced.

Nothing about the guards is relaxed. A post the model writes badly is dropped by
the shipped parser exactly as it would be in production, and this script reports
that rather than retrying, because a drop is the finding.

Usage::

    MANYAGENT_LLM_BASE_URL=http://localhost:8000/v1 \
    MANYAGENT_LLM_API_KEY=local \
    MANYAGENT_LLM_MODEL=qwen3.5-9b \
    MANYAGENT_LLM_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}' \
    uv run python scripts/simulate_ksi_e2e.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from manyagent.distill.resolve import _OpenAICompatModel
from manyagent.testing import DummyAdapter, Simulation

# The session the first agent "lived". The post's `evidence` must be a verbatim
# excerpt of this text or the shipped parser drops the post, so this doubles as
# the ground truth the grounding check runs against.
TRANSCRIPT = """\
user: the deploy script restarts nginx but bad configs still reach production
agent: reproducing now
agent: $ systemctl restart nginx
agent: systemctl restart nginx returned 0 while the config was still invalid
agent: $ nginx -t
agent: nginx -t: [emerg] duplicate listen 0.0.0.0:80 in /etc/nginx/sites-enabled/api
agent: so the unit reports success and keeps serving the OLD worker
agent: adding `nginx -t` ahead of the restart in deploy.sh makes the step fail loudly
user: ship that
"""

GOAL = "terminal-service-restart"


class LiveModel:
    """A real endpoint behind the interface ``Simulation`` expects.

    ``Simulation`` pushes a canned response before each verb; here the push is
    discarded and the endpoint answers instead. ``responses`` exists because
    ``Simulation.cross_distill`` inspects its depth to detect the idempotency
    short-circuit.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.responses: list[Any] = []
        self.calls = 0
        self.log: list[tuple[str, str]] = []

    def push(self, _response: Any) -> None:
        return  # the live model writes its own answer

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.calls += 1
        model = _OpenAICompatModel(
            base_url=os.environ.get("MANYAGENT_LLM_BASE_URL", "http://localhost:8000/v1"),
            api_key=os.environ.get("MANYAGENT_LLM_API_KEY", "local"),
            model=os.environ.get("MANYAGENT_LLM_MODEL", "qwen3.5-9b"),
        )
        out = model.complete(prompt, max_tokens=max_tokens)
        self.log.append((prompt, out))
        return out


def _hdr(step: str) -> None:
    print(f"\n{'=' * 72}\n{step}\n{'=' * 72}")


def _show(result: Any) -> None:
    for line in result.out:
        text = str(line).strip()
        if text:
            print(f"  | {text}")
    print(f"  -> rc={result.rc}")


async def _gen1(sim: Any, findings: list[str]) -> None:
    """One full generation: attempt, post, reply, curate, seed, close."""
    _hdr("GEN 1 · session start + register")
    _show(await sim.start(goal=GOAL))
    _show(await sim.register())

    _hdr("GEN 1 · run the wrapped agent (produces the trace)")
    _show(await sim.run_agent(transcript=TRANSCRIPT))

    _hdr("GEN 1 · /self-distill — the model writes a post, the parser judges it")
    _show(await sim.self_distill({}, rating=5))
    posts = [p for p in await sim.bank.list_packets(type="post") if p.get("goal") == GOAL]
    if not posts:
        findings.append("GEN1 /self-distill produced no stored post (parser dropped the model's output)")
    else:
        print(f"  stored {len(posts)} post(s):")
        for p in posts:
            print(f"    {p['id']}  {json.dumps(p.get('structured'))[:170]}")

    _hdr("GEN 1 · /discuss — a stance reply under the retrieval gate")
    _show(await sim.discuss({}, stance="synthesize"))
    replies = [p for p in await sim.bank.list_packets(type="post") if p.get("kind") == "reply"]
    print(f"  stored {len(replies)} reply/replies")
    if posts and not replies:
        findings.append("GEN1 /discuss stored no reply even though a post existed to engage")

    _hdr("GEN 1 · /cross-distill — the real curate() state machine")
    _show(await sim.cross_distill({}))
    bundles = await sim.bank.list_packets(type="distill")
    if not bundles:
        findings.append("/cross-distill stored no bundle")
    for b in bundles:
        kept = sum(len(v) for v in (b.get("bundle") or {}).values() if isinstance(v, list))
        print(f"    {b['id']}  scope={b.get('scope')}  parents={len(b.get('parents') or [])}  insights={kept}")

    _hdr("GEN 1 · /inject — preview, human gate, reuse ledger")
    _show(await sim.inject())

    _hdr("GEN 1 · session end")
    _show(await sim.end(rating="skip"))


async def _gen2(sim: Any, findings: list[str]) -> None:
    """The step that closes the loop: a later agent starts from gen-1 knowledge."""
    _hdr("GEN 2 · a NEW session on the same goal starts from gen-1 knowledge")
    _show(await sim.start(goal=GOAL))
    res = await sim.inject()
    _show(res)
    if not (res.saw("inject") or res.rc == 0):
        findings.append("GEN2 could not inject the gen-1 bundle (the loop does not close)")


async def _report_corpus(sim: Any) -> None:
    _hdr("RESULT · what generation 2 inherits")
    all_posts = await sim.bank.list_packets(type="post")
    all_bundles = await sim.bank.list_packets(type="distill")
    print(f"  posts   : {len(all_posts)}")
    print(f"  bundles : {len(all_bundles)}")
    for b in all_bundles:
        for bucket, items in (b.get("bundle") or {}).items():
            for it in items or []:
                if isinstance(it, dict):
                    print(f"    [{bucket}] {str(it.get('text'))[:110]}")
                    for ev in it.get("evidence") or []:
                        print(f'        grounded in {ev.get("post_id")}: "{str(ev.get("quote"))[:80]}"')


async def run(*, verbose: bool) -> int:
    agent_model = LiveModel("agent")
    curator_model = LiveModel("curator")
    adapter = DummyAdapter(transcript=TRANSCRIPT)
    adapter.model = agent_model  # type: ignore[assignment]

    findings: list[str] = []

    with Simulation(adapter=adapter) as sim:
        sim.curator_model = curator_model  # type: ignore[assignment]
        await _gen1(sim, findings)
        await _gen2(sim, findings)
        await _report_corpus(sim)
        print(f"\n  live model calls — agent={agent_model.calls} curator={curator_model.calls}")

    _hdr("FINDINGS")
    if findings:
        for f in findings:
            print(f"  !! {f}")
    else:
        print("  none — a lesson travelled the whole protocol and survived every guard")

    if verbose:
        for label, model in (("agent", agent_model), ("curator", curator_model)):
            for i, (_prompt, out) in enumerate(model.log):
                print(f"\n--- {label} call {i + 1} raw output ---\n{out[:1500]}")
    return 1 if findings else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="dump raw model output")
    args = ap.parse_args()
    return asyncio.run(run(verbose=args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
