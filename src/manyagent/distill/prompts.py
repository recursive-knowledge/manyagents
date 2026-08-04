"""The cache-split curator prompt (manyagent.distill.md:59; ports
``swarms/distillation/prompts.py:_build_distill_system:475-501``).

Prompt-cache eligibility (Anthropic ``cache_control: ephemeral`` / OpenAI
automatic prompt cache) requires a byte-stable prefix at the start of input
across calls. The rule block is huge and identical across every curation, so
cost forces the split: the **stable system prefix** is role directive +
``ANTI_META_BLOCK`` + output schema (constant per scope); the **variable user
message** is the rendered goal-scoped posts. Posts are NEVER interpolated into
the prefix — doing so would defeat the cache on every call (the swarms
cache-miss-from-prefix-mutation gotcha).

``ANTI_META_BLOCK`` is imported from ``manyagent.forum`` and re-exported: it is the
*same object* (identity, not equality) the agent wrote against, so the rule
the curator filters against is byte-for-byte the rule the agent saw
(manyagent.forum.md / manyagent.distill.md "the anti-meta discipline").

C4 corollary (Design Principles §6/§11): a hosted curator distilling the
*public corpus* is corpus-curation, not being the user's *task* inference
provider — the structure is an agent/curator tax, never a human tax.
"""

from __future__ import annotations

import re
from typing import Any

from manyagent.distill.schema import BUCKETS
from manyagent.forum import ANTI_META_BLOCK, assert_anti_meta_rules_present

__all__ = ["ANTI_META_BLOCK", "assert_anti_meta_rules_present", "build_distill_prompt"]

_SYSTEM_ROLE = (
    "ROLE: you are a curator. You read a corpus of structured, "
    "evidence-grounded forum posts written by coding agents after their "
    "sessions, and you distill them into a compact, falsifiable bundle of "
    "Insights for a future agent to be seeded with. You are NOT summarizing "
    "your own work — you are curating collective evidence. Be scarce: a small "
    "bundle of grounded Insights beats a large bundle of plausible ones.\n"
)

_PER_GOAL_DIRECTIVE = (
    "SCOPE: per-goal. Every input post shares ONE goal (across sessions and "
    "agents and time). Distill what transfers to the next agent pursuing that "
    "same goal. A claim independently grounded by posts from different "
    "sessions is recurrence — mark it confidence='high'.\n"
)

_CROSS_GOAL_DIRECTIVE = (
    "SCOPE: cross-goal. Input posts span many goals. Distill ONLY rules that "
    "generalize across goals — the corpus-wide transferable layer. Keep each "
    "primitive concrete even while wording it goal-agnostically. A claim "
    "recurring across different goals/sessions is confidence='high'.\n"
)

_OUTPUT_SCHEMA = (
    "OUTPUT (strict JSON, no prose outside it). Six buckets, each a list of "
    "Insights:\n"
    "{\n" + "".join(f'  "{b}": [<Insight>, ...],\n' for b in BUCKETS) + "}\n"
    "where <Insight> is:\n"
    "{\n"
    '  "text": "<the rule, \'when X do Y\' — concrete, <=240 chars>",\n'
    '  "applies_when": "<concrete condition it holds, <=200 chars>",\n'
    '  "does_not_apply_when": "<concrete boundary, <=200 chars; NOT '
    "'always'/'never'/'n/a' — an unbounded rule is REJECTED>\",\n"
    '  "evidence": [{"post_id": "<a real cited packet id>", "quote": '
    '"<verbatim <=200-char excerpt copied from that post>"}],\n'
    '  "confidence": "high" | "medium" | "low"\n'
    "}\n"
    "Field semantics:\n"
    "- transferable_insights: concrete actionable rules.\n"
    "- confirmed_constraints: invariants verified by post evidence.\n"
    "- rejected_hypotheses: approaches evidence showed are wrong "
    "(first-class; what NOT to try).\n"
    "- pitfalls: failure modes to avoid; the boundary names where they do "
    "NOT apply so a future agent does not over-generalize.\n"
    "- checks: quick concrete verifications (name the command/file/flag).\n"
    "- next_steps: specific experiments a future agent should try.\n"
    "NON-NEGOTIABLE (the parser drops violations mechanically — do not rely "
    "on it, but it will):\n"
    "- Every Insight needs non-empty text, applies_when, does_not_apply_when, "
    "and >=1 evidence entry, or it is DROPPED. Empty buckets are correct.\n"
    "- VERBATIM QUOTE: each evidence.quote MUST be a literal substring of the "
    "cited post. Paraphrases are DROPPED — the quote is what proves the "
    "Insight is grounded, not invented. Do not invent post ids.\n"
    "- At most 5 Insights per bucket. Prefer fewer, higher-signal.\n"
    "- CONFIDENCE: mark confidence='high' ONLY if the Insight's evidence "
    "cites >=2 DISTINCT sessions (recurrence). A claim grounded in a single "
    "session is at most 'medium' — the curator demotes a single-session "
    "'high' to 'medium' mechanically.\n"
    "- Weight high-reuse / high-rating authors over unrated ones when claims "
    "conflict; a low-rated or contradicted claim becomes a "
    "rejected_hypotheses/pitfalls Insight, not a transferable one.\n"
)

# One worked example pair (models follow examples far better than prose). The
# GOOD Insight is concrete, bounded, and evidence-grounded; the BAD Insight is
# the meta/vague anti-pattern the rules forbid (no concrete primitive, no
# boundary, no verbatim evidence). Domain-neutral on purpose — a generic HTTP
# client, not any one benchmark — so the curator is not biased toward a domain.
_FEWSHOT = (
    "EXAMPLE (one good Insight, one bad Insight — for shape only; do NOT copy "
    "this text or its post ids into your output):\n"
    "GOOD (a transferable_insight; concrete primitive, bounded, verbatim "
    "evidence from >=2 sessions => confidence high):\n"
    "{\n"
    '  "text": "set connect_timeout=2s on the requests.Session and retry only '
    'on HTTP 5xx, not on 4xx",\n'
    '  "applies_when": "the client talks to a flaky upstream that intermittently '
    'returns 503",\n'
    '  "does_not_apply_when": "the upstream returns 4xx (a 4xx is a client bug; '
    'retrying masks it)",\n'
    '  "evidence": [{"post_id": "<id-A>", "quote": "<verbatim excerpt naming '
    'connect_timeout>"}, {"post_id": "<id-B>", "quote": "<verbatim excerpt from '
    'a second session>"}],\n'
    '  "confidence": "high"\n'
    "}\n"
    "BAD (REJECTED — process meta, no concrete primitive, unbounded boundary, "
    "no verbatim evidence): \n"
    "{\n"
    '  "text": "validate first and check edge cases before shipping",\n'
    '  "applies_when": "any task",\n'
    '  "does_not_apply_when": "never",\n'
    '  "evidence": [],\n'
    '  "confidence": "high"\n'
    "}\n"
)

# Sanitize rendered post text exactly as swarms ``_sanitize_prompt_excerpt``:
# neutralize a standalone protocol token line, collapse newlines, bound length
# (the binding constraint on how much signal reaches the curator).
_PROTOCOL_LINE_RE = re.compile(r"(?m)^(\s*)(INSIGHT|COMMENT|EVIDENCE|POST)(\s*)$")
_POST_EXCERPT_CHARS = 2000


def _sanitize(value: Any, *, max_chars: int = _POST_EXCERPT_CHARS) -> str:
    text = "" if value is None else str(value)
    text = _PROTOCOL_LINE_RE.sub(r"\1[\2]\3", text)
    text = " ".join(text.splitlines())
    if len(text) > max_chars:
        cut = text[:max_chars]
        # Truncate at the last word boundary so a quote is not cut mid-word
        # (a mid-word cut leaves an unmatched fragment that fails the curator's
        # verbatim check). Fall back to the hard cut if there is no whitespace.
        boundary = cut.rfind(" ")
        if boundary > 0:
            cut = cut[:boundary]
        return cut + "..."
    return text


def _render_post(post: dict[str, Any]) -> str:
    structured = post.get("structured")
    if isinstance(structured, dict):
        # Render bare field VALUES (no `key=` prefix) joined by ` | `. The
        # curator's verbatim check (parse._post_searchable) builds its corpus
        # from the structured *values* only, so a `key=` prefix here would let
        # the model quote `field=...` and fail the verbatim substring check.
        body = " | ".join(_sanitize(v) for v in structured.values() if isinstance(v, str))
    else:
        body = _sanitize(post.get("text") or post.get("content"))
    meta = f"kind={post.get('kind')}"
    if post.get("reply_to"):
        meta += f" reply_to={post.get('reply_to')} stance={post.get('stance')}"
    hint = ""
    sig = post.get("_signal")
    if isinstance(sig, dict):
        hint = (
            f" [reuse={float(sig.get('reuse', 0)):.0f}"
            f" injected={int(sig.get('inject_count', 0))}x"
            f" rating={sig.get('rating_bucket', 'neutral')}]"
        )
    return f"- id={post.get('id')} agent={post.get('agent_id')} {meta}{hint}: {body}"


def _stable_system(scope: str) -> str:
    """The cache-stable system prefix. Stable across every call of the same
    ``scope`` (role directive + ANTI_META_BLOCK + schema); contains NO
    per-call post data."""
    directive = _PER_GOAL_DIRECTIVE if scope == "per_goal" else _CROSS_GOAL_DIRECTIVE
    return f"{_SYSTEM_ROLE}\n{directive}\n{ANTI_META_BLOCK}\n{_OUTPUT_SCHEMA}\n{_FEWSHOT}"


def build_distill_prompt(
    *,
    posts: list[dict[str, Any]],
    scope: str,
    goal: str | None,
) -> tuple[str, str]:
    """Return ``(system, user)``. ``system`` is the cache-stable prefix (never
    contains posts); ``user`` is the variable rendered corpus. Posts should
    arrive already weighted/ordered (``manyagent.distill.weighting.weigh_posts``)."""
    system = _stable_system(scope)
    scope_line = f"SCOPE={scope} GOAL={goal if goal is not None else '(cross-goal / ungoaled corpus)'}"
    rendered = "\n".join(_render_post(p) for p in posts)
    user = f"{scope_line}\nPOSTS ({len(posts)}):\n{rendered}\n\nReturn the JSON bundle now."
    return system, user
