"""Outcome weighting — the swarms ``native_score`` replacement (oms.distill.md
"Outcome / confidence model"). OMA has no objective evaluator, so a single
score becomes a triple, computed **mechanically**:

| signal | source | role |
|--------|--------|------|
| downstream **reuse** (load-bearing default) | ``bank.reuse_score`` view over the injection ledger | the trusted weight — behavioral, hard to game |
| **★** progress rating (soft prior) | optional ``post.rating`` 1..5; unrated valid | bucketed high/med/low; bias only, never a gate |
| **accept** | folded into the reuse view (a distill accept lifts the session outcome) | artifact-quality preference data |

This module produces (a) a per-post signal dict, (b) a deterministic priority
tuple that orders posts in the rendered prompt high-signal-first (mirrors
``swarms/distillation/cross_task.py:_post_priority``), and (c) a short
human-readable weight hint the curator prompt surfaces. ★ from different goals
are never compared; there is no global numeric ranking (oms.distill.md:80).
"""

from __future__ import annotations

from typing import Any

from oms.bank import Bank
from oms.utils import config


def _rating_bucket(rating: Any) -> str:
    """≥4★ → high (wins conflicts, promotes confidence); ≤2★ → low (its claims
    are routed to rejected_hypotheses/pitfalls by the prompt); 3/None → neutral
    (still curated, just unweighted — swarms' no-score behavior)."""
    if not isinstance(rating, int):
        return "neutral"
    if rating >= 4:
        return "high"
    if rating <= 2:
        return "low"
    return "neutral"


async def weigh_posts(posts: list[dict[str, Any]], *, bank: Bank) -> list[dict[str, Any]]:
    """Annotate clustered posts with their outcome signal and return them
    ordered high-signal-first (deterministic; stable for idempotent re-runs).

    Each returned post gains ``_signal = {reuse, inject_count, rating_bucket}``.
    The reuse number is the **load-bearing default**: a post that was injected
    into a later session that then rated/accepted well scores high.
    """
    rows = await bank.reuse_score()
    reuse_by_id = {r["packet_id"]: r for r in rows}

    annotated: list[dict[str, Any]] = []
    for p in posts:
        r = reuse_by_id.get(p["id"], {})
        # OMS_REUSE_WEIGHT is the one knob a user reaches for to tune the
        # load-bearing default signal (oms.core.md "reuse is the load-bearing
        # default"); applied here so it flows into both ordering and the
        # rendered prompt hint (Design Principles §8 — no dead OMS_ knob).
        reuse = float(r.get("reuse_score", 0.0) or 0.0) * config.OMS_REUSE_WEIGHT
        inject_count = int(r.get("inject_count", 0) or 0)
        bucket = _rating_bucket(p.get("rating"))
        q = dict(p)
        q["_signal"] = {"reuse": reuse, "inject_count": inject_count, "rating_bucket": bucket}
        annotated.append(q)

    def _priority(p: dict[str, Any]) -> tuple[Any, ...]:
        s = p["_signal"]
        # reply > standalone reflection ("round-1 > round-0"); then reuse;
        # then ★ bucket; then recency; then id (total order → deterministic).
        return (
            1 if p.get("kind") == "reply" else 0,
            s["reuse"],
            {"high": 2, "neutral": 1, "low": 0}[s["rating_bucket"]],
            str(p.get("created_at") or ""),
            str(p.get("id")),
        )

    annotated.sort(key=_priority, reverse=True)
    return annotated


def weight_hint(post: dict[str, Any]) -> str:
    """A compact provenance line the curator prompt shows next to each post so
    high-reuse / ≥4★ authors visibly win conflicting claims. Bias only."""
    s = post["_signal"]
    parts = [f"reuse={s['reuse']:.0f}", f"injected={s['inject_count']}x", f"rating={s['rating_bucket']}"]
    return " ".join(parts)
