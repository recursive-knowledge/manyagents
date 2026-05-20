"""oms.distill — the **curator** (M7; oms.distill.md).

Reads goal-scoped ``post`` packets (oms.forum) and emits a ``distill`` packet:
a structured, evidence-grounded, *scarce* 6-bucket Insight bundle for a future
agent to be seeded with at ``/inject`` time. It is not the worker agent
self-summarizing; it is a separate curator LLM over collective evidence.

* **6-bucket Insight schema** (``schema``) — swarms ``types.py`` ported, the
  ``Evidence`` remapped to oms packet-id **strings** (no ``task_id``).
* **Mechanical validation, port + harden (C3)** (``parse``) — swarms'
  *mechanical-not-trusted-to-the-model* ``_as_insight_list`` ported, plus the
  two checks oms.distill.md:53 requires be mechanical (the ``always``/``n/a``
  unbounded-rule rejection and the verbatim-quote substring) that swarms left
  at prompt level. Anti-meta enforcement is the **same code** the M6 post
  parser uses.
* **Cache-split prompt** (``prompts``) — stable system prefix (role +
  ``ANTI_META_BLOCK`` + schema) + variable rendered posts; the block is the
  *same object* the agent wrote against.
* **Outcome weighting** (``weighting``) — reuse (load-bearing) + ★ + accept.
* **Hybrid curator** (``resolve`` / ``server``) — ``local|server|auto``.
* **Idempotent state machine** (``curator``) — no carry-forward, per/cross
  independence, exact ``"Run /self-distill first!"`` on an empty scope.

C1: ``preference`` is distill-only (set via ``/cross-distill`` accept/reject,
M8) — never produced here on a post.
"""

from __future__ import annotations

from oms.distill.curator import CurationError, NoPostsError, curate
from oms.distill.parse import validate_bundle
from oms.distill.prompts import ANTI_META_BLOCK, build_distill_prompt
from oms.distill.resolve import Curator, NoLocalCurator, resolve
from oms.distill.schema import BUCKETS, empty_bundle
from oms.distill.server import ServerCurator, ServerUnavailable
from oms.distill.weighting import weigh_posts

__all__ = [
    "ANTI_META_BLOCK",
    "BUCKETS",
    "CurationError",
    "Curator",
    "NoLocalCurator",
    "NoPostsError",
    "ServerCurator",
    "ServerUnavailable",
    "build_distill_prompt",
    "curate",
    "empty_bundle",
    "resolve",
    "validate_bundle",
    "weigh_posts",
]
