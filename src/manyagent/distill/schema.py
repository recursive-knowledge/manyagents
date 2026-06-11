"""The 6-bucket evidence-grounded Insight schema (manyagent.distill.md "The Insight
schema", Open-Q §A1 resolved).

Ported from ``swarms/distillation/types.py:15-35`` + ``prompts.py:124-178`` and
**hardened** for manyagent (C3). The swarms→manyagent ``Evidence`` remap:

    swarms  ``{"task_id": "<id>", "post_id": <int>, "quote": "<verbatim>"}``
    manyagent     ``{"post_id": "<packet-id string>", "quote": "<verbatim>"}``

— a packet-id **string**, no ``task_id`` (manyagent has no task oracle; the
``allowed_post_ids: set[int]`` of ``per_task.py:_as_insight_list`` becomes the
cited packet-id **string** set, resolved against the real clustered posts).

This module is **pure constants + structural shapes**. The mechanical
drop/cap/verbatim/boundary enforcement lives in ``manyagent.distill.parse`` (the C3
port-and-harden); the anti-meta enforcement code is imported from
``manyagent.forum.anti_meta`` so the curator filters against the *same code* the
agent wrote against.
"""

from __future__ import annotations

# The six typed Insight buckets. `rejected_hypotheses` (what NOT to try) is
# first-class signal, not an afterthought (manyagent.distill.md:51).
BUCKETS: tuple[str, ...] = (
    "transferable_insights",
    "confirmed_constraints",
    "rejected_hypotheses",
    "pitfalls",
    "checks",
    "next_steps",
)

CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})

# Hard caps (mirror swarms ``prompts.py:172-174``; enforced mechanically in
# parse.py regardless of model output).
MAX_PER_BUCKET = 5
MAX_TEXT = 240
MAX_CONDITION = 200
MAX_QUOTE = 200
MAX_EVIDENCE_PER_INSIGHT = 5

# C3-ADD (mechanical, was prompt-level only in swarms ``prompts.py:163-164``):
# an unbounded rule is rejected. ``does_not_apply_when`` may not be empty or
# any of these — a rule that "always" applies has no boundary and is noise.
UNBOUNDED_BOUNDARIES: frozenset[str] = frozenset({"", "always", "never", "n/a", "na", "none"})


def empty_bundle() -> dict[str, list[dict[str, object]]]:
    """A well-formed bundle with all six buckets present and empty. Empty
    buckets are correct and preferable to filler (manyagent.distill.md:53)."""
    return {b: [] for b in BUCKETS}
