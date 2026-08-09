"""How often does a live model's /self-distill post survive the write-time gate?

The end-to-end run in ``scripts/simulate_ksi_e2e.py`` is sampling-dependent: the
same prompt and the same trace sometimes yield a stored post and sometimes a
rejection, and a rejection stalls the whole protocol (no post, so no forum, so
no bundle, so nothing to seed the next generation with). That makes the pass
rate of this one gate the ceiling on the whole loop, so it is worth a number
rather than an anecdote.

Each iteration renders the real ``render_post_prompt`` with the real trace,
calls the live model, and runs the real ``parse_post``. It records the verdict
and, on rejection, the reason plus the field that caused it.

    uv run python scripts/probe_post_gate.py --n 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from typing import Any

from manyagent.bank import FakeBank
from manyagent.distill.resolve import _OpenAICompatModel
from manyagent.forum import parse_post, render_post_prompt
from manyagent.forum.anti_meta import has_banned_meta, is_concrete

TRACE = """\
user: the deploy script restarts nginx but bad configs still reach production
agent: $ systemctl restart nginx
agent: systemctl restart nginx returned 0 while the config was still invalid
agent: $ nginx -t
agent: nginx -t: [emerg] duplicate listen 0.0.0.0:80 in /etc/nginx/sites-enabled/api
agent: so the unit reports success and keeps serving the OLD worker
agent: adding `nginx -t` ahead of the restart in deploy.sh makes the step fail loudly
"""

GOAL = "terminal-service-restart"


def _model() -> _OpenAICompatModel:
    return _OpenAICompatModel(
        base_url=os.environ.get("MANYAGENT_LLM_BASE_URL", "http://localhost:30005/v1"),
        api_key=os.environ.get("MANYAGENT_LLM_API_KEY", "local"),
        model=os.environ.get("MANYAGENT_LLM_MODEL", "qwen3.6-35b-a3b"),
    )


def _extract(raw: str) -> dict[str, Any] | None:
    from manyagent.distill.curator import _extract_json

    out = _extract_json(raw)
    return out if isinstance(out, dict) else None


async def run(n: int) -> int:
    model = _model()
    prompt = render_post_prompt(kind="reflection", goal=GOAL, trace_context=TRACE)
    verdicts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    samples: list[str] = []

    for i in range(n):
        raw = model.complete(prompt)
        obj = _extract(raw)
        if obj is None:
            verdicts["unparseable"] += 1
            print(f"  {i + 1:2d}. UNPARSEABLE")
            continue

        structured = obj.get("structured") if isinstance(obj.get("structured"), dict) else obj
        bank = FakeBank()
        await bank.put_session("S")
        record = {
            "id": "S/p1",
            "session_id": "S",
            "type": "post",
            "agent_id": "S/agent-001-claude",
            "kind": "reflection",
            "goal": GOAL,
            "structured": structured,
        }
        ok, res = await parse_post(record, bank=bank, trace_context=TRACE)
        assumption = str((structured or {}).get("load_bearing_assumption", ""))
        if ok:
            verdicts["stored"] += 1
            print(f"  {i + 1:2d}. STORED     | {assumption[:88]}")
        else:
            verdicts["rejected"] += 1
            reason = str(res).split("(")[0].strip()
            reasons[reason] += 1
            samples.append(assumption)
            print(f"  {i + 1:2d}. REJECTED   | {reason}")
            print(f"      assumption: {assumption[:88]}")
            print(f"      is_concrete={is_concrete(assumption)} banned={has_banned_meta(assumption)}")

    print(f"\n{'=' * 68}\nVERDICTS over {n} live posts")
    for k, v in verdicts.most_common():
        print(f"  {k:12s} {v:2d}  ({v / n:.0%})")
    if reasons:
        print("REJECTION REASONS")
        for k, v in reasons.most_common():
            print(f"  {v:2d}x {k}")
    if samples:
        print("\nREJECTED ASSUMPTIONS (verbatim):")
        for s in samples:
            print(f"  - {s}")
    print(json.dumps({"n": n, "verdicts": dict(verdicts), "reasons": dict(reasons)}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=8)
    args = ap.parse_args()
    return asyncio.run(run(args.n))


if __name__ == "__main__":
    raise SystemExit(main())
