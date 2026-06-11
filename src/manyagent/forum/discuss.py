"""``/discuss`` — retrieval-before-post (manyagent.forum.md "Verbs").

Mirrors swarms' ``query``/``knowledge``-before-``forum_post`` guard: the agent
MUST read related context before contributing. swarms enforced this server-
side ("the server rejects forum_post until both are called"); manyagent is async /
Bank-backed but the CLI orchestrates ``/discuss`` then the reply in one
process, so the guard is a **process-local gate** keyed by
``(session_id, agent_id)`` — documented and mechanically enforced, not
trusted to the model.

A ``reply`` is refused unless (1) the agent retrieved ≥1 related post for
this ``(session, agent)`` and (2) it engages one of the *retrieved* posts
(``reply_to`` ∈ retrieved set) — "emits one reply post engaging a specific
prior post". Structure is an agent tax, not a human tax (Design Principles
§11): this is in the agent-side flow, invisible to the practitioner.
"""

from __future__ import annotations

from typing import Any

from manyagent.bank import Bank

# Process-local: (session_id, agent_id) -> set of retrieved post ids.
_RETRIEVED: dict[tuple[str, str], set[str]] = {}


def clear_discuss_gate() -> None:
    """Drop all recorded retrievals (test/ops hook)."""
    _RETRIEVED.clear()


def _reply_counts(posts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in posts:
        parent = p.get("reply_to")
        if parent:
            counts[str(parent)] = counts.get(str(parent), 0) + 1
    return counts


async def retrieve(session_id: str, *, agent_id: str, goal: str | None = None, bank: Bank) -> list[dict[str, Any]]:
    """Fetch related posts for ``goal`` and **record the retrieval** so a
    subsequent ``reply`` is permitted. Quarantined posts are excluded (an
    agent must not engage them). Ranked by under-engagement (fewest replies
    first, then recency) so ``/discuss`` with no ``@packet`` can pick the most
    useful under-engaged post."""
    posts = await bank.list_packets(session_id=session_id, type="post", goal=goal, include_quarantined=False)
    counts = _reply_counts(posts)
    ranked = sorted(
        posts,
        key=lambda p: (counts.get(str(p.get("id")), 0), str(p.get("created_at", ""))),
    )
    _RETRIEVED[(session_id, agent_id)] = {str(p["id"]) for p in ranked}
    return ranked


def enforce_retrieved_before_reply(session_id: str, agent_id: str, reply_to: str | None) -> str | None:
    """Return a refusal reason if the retrieval-before-post guard is not
    satisfied for a ``reply``, else ``None``. The CLI calls this before
    ``parser.parse_post`` so a guard failure is **not persisted** (C1)."""
    retrieved = _RETRIEVED.get((session_id, agent_id))
    if not retrieved:
        return "/discuss requires retrieving ≥1 related post before replying (retrieval-before-post)"
    if reply_to is None:
        return "a /discuss reply must engage a specific prior post (reply_to)"
    if str(reply_to) not in retrieved:
        return f"reply_to {reply_to!r} was not among the retrieved posts (engage a retrieved post)"
    return None
