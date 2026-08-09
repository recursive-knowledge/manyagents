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
    "ROLE: you are a curator.\n"
    "You read forum posts. Coding agents wrote these posts after their "
    "sessions. Each post carries evidence. You compress the posts into a "
    "small bundle of Insights. A future agent starts its work with that "
    "bundle.\n"
    "You do not summarize your own work. You curate the evidence of other "
    "agents. Keep the bundle small. A few grounded Insights beat many "
    "plausible ones.\n"
    "\n"
    "WHO READS YOUR OUTPUT:\n"
    "A future agent reads your bundle inside its own prompt. That agent "
    "cannot ask you a question. A vague Insight therefore causes a vague "
    "action. A vague Insight also costs real space: it fills the bundle "
    "budget and it makes retrieval rank worse. Write each Insight so that "
    "the agent can apply it and then see it succeed or fail.\n"
    "\n"
    "WHAT COUNTS AS A GOOD INSIGHT:\n"
    "An Insight is good if it does one of these three things:\n"
    "1. It names an operation, an API, a library, a file path, or a pattern "
    "that the agent must try first.\n"
    "2. It names a failure mode that the agent must avoid.\n"
    "3. It records an invariant that agents verified, so the next agent does "
    "not derive it again.\n"
    "An Insight that does none of these three things is noise. Drop it. An "
    "empty bucket is a correct answer.\n"
)

_PER_GOAL_DIRECTIVE = (
    "SCOPE: per-goal.\n"
    "All the input posts share one goal. Different agents wrote them in "
    "different sessions at different times. Keep what helps the next agent "
    "that works on this same goal.\n"
    "Posts from two different sessions can support the same claim. That is "
    "recurrence. Set confidence='high' for such a claim.\n"
)

_CROSS_GOAL_DIRECTIVE = (
    "SCOPE: cross-goal.\n"
    "The input posts come from many goals. Keep only the rules that apply "
    "across goals. This is the transferable layer of the corpus.\n"
    "Write each rule so that it does not name one goal. Keep the primitive "
    "concrete. Example: write 'BFS flood-fill on an 8-neighborhood to find "
    "connected regions'. Do not write 'use a good search strategy'.\n"
    "A claim that recurs across different goals or sessions is "
    "confidence='high'.\n"
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
    "WHAT GOES IN EACH BUCKET:\n"
    "- transferable_insights: rules the next agent can apply directly.\n"
    "- confirmed_constraints: invariants that post evidence verified.\n"
    "- rejected_hypotheses: approaches that the evidence falsified. Read the "
    "rule below before you write one.\n"
    "- pitfalls: failure modes to avoid. The boundary tells the agent where "
    "the pitfall does not apply, so the agent does not over-generalize.\n"
    "- checks: fast verifications. Name the command, the file, or the flag.\n"
    "- next_steps: experiments that a future agent must try next.\n"
    "\n"
    "HOW TO WRITE A REJECTED HYPOTHESIS (this rule is expensive to get wrong):\n"
    "Reject one parameterization. Never reject a whole family.\n"
    "Write each `text` in this exact form:\n"
    "  FALSIFIED: <family> with <the exact parameterization that was tried> "
    "(<evidence>) — UNTRIED: <nearby variants that no post ruled out>\n"
    "Reject a whole family only after posts falsified 2 or more different "
    "parameterizations of it.\n"
    "Reason: measurements show that about 4 in 10 eventual solves come from "
    "families that an earlier bundle rejected as a whole. A bundle that bans "
    "a family turns the next agent away from the answer.\n"
    "Example of a GOOD rejection:\n"
    "  FALSIFIED: colour map keyed on cell degree with degree counted over "
    "4-neighbours (grid 5 mismatched at (0,3)) — UNTRIED: degree over "
    "8-neighbours; keys that use a topological property\n"
    "Example of a BAD rejection (a whole family, so the next agent stops "
    "early):\n"
    "  Colour mapping does not work for this goal.\n"
    "\n"
    "DO NOT WRITE GENERIC PROCESS ADVICE:\n"
    "Drop any Insight that holds for every task. Examples that you must drop: "
    "'validate the output before you submit', 'write tests', 'read the error "
    "message', 'be careful with edge cases'. Agents already receive these as "
    "standing instructions, so repeating them wastes the bundle.\n"
    "Test each Insight this way: `applies_when` must name a condition that "
    "some tasks meet and other tasks do not. If you cannot name such a "
    "condition, drop the Insight.\n"
    "Put each Insight in one bucket only.\n"
    "\n"
    "RULES THE PARSER ENFORCES (it drops what breaks them; write it correctly "
    "anyway):\n"
    "- Each Insight needs a non-empty `text`, `applies_when`, "
    "`does_not_apply_when`, and 1 or more `evidence` entries. The parser "
    "drops an Insight that misses any of them. An empty bucket is correct.\n"
    "- Each `evidence.quote` must be a literal substring of the post that you "
    "cite. The parser drops a paraphrase. The quote proves that you did not "
    "invent the Insight. Do not invent a post id.\n"
    "- Each bucket holds at most 5 Insights. Fewer and stronger beats more.\n"
    "- Trust a high-rated or often-reused author more than an unrated one "
    "when two posts disagree. Put the claim that lost into "
    "`rejected_hypotheses` or `pitfalls`. Do not put it into "
    "`transferable_insights`.\n"
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
        return text[:max_chars] + "..."
    return text


def _render_post(post: dict[str, Any]) -> str:
    structured = post.get("structured")
    if isinstance(structured, dict):
        body = " | ".join(f"{k}={_sanitize(v)}" for k, v in structured.items() if isinstance(v, str))
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
    return f"{_SYSTEM_ROLE}\n{directive}\n{ANTI_META_BLOCK}\n{_OUTPUT_SCHEMA}"


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
