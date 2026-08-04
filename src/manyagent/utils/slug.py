"""Goal-slug codec — the URL-normalized form of a goal label.

Human-facing website URLs are keyed on the *goal*, not the opaque session id:
``"paper review 4"`` → ``paper-review-4``. The slug is a **derived match key**,
never an identity — the viewer re-derives it from each packet's goal to group a
goal board, so two near-identical goals intentionally share one board, and the
display name is recovered from the matched packets (not the slug).

This algorithm is mirrored byte-for-byte in ``web/viewer/src/lib/slug.js``; keep
the two in lockstep (a divergence would split the CLI's open-link from the
viewer's board match).
"""

from __future__ import annotations

import re

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_CHARS = 80
_UNGOALED = "ungoaled"


def slugify(goal: str | None) -> str:
    """URL-normalize a goal label to a stable, ≤80-char slug.

    Lowercase; every run of non-``[a-z0-9]`` chars collapses to a single ``-``;
    leading/trailing ``-`` stripped; truncated to 80 chars, then any ``-`` left
    dangling by the cut removed. ``None`` / blank / all-punctuation →
    ``"ungoaled"`` (mirrors the viewer's ``goal ?? "(ungoaled)"`` grouping, and
    ``slugify("(ungoaled)") == "ungoaled"`` so the two agree). Non-ASCII letters
    are not transliterated — they fall in the non-slug class and drop out.
    """
    if goal is None:
        return _UNGOALED
    s = _NON_SLUG.sub("-", goal.strip().lower()).strip("-")
    s = s[:_MAX_CHARS].rstrip("-")
    return s or _UNGOALED


def normalize_goal(goal: str | None) -> str | None:
    """Canonical *storage/match* form of a goal label — the aggregation key.

    Unlike :func:`slugify` (which always yields a non-empty URL slug, mapping
    blanks to ``"ungoaled"``), this preserves the codebase's "no goal" sentinel:
    ``None`` stays ``None`` and an empty/all-punctuation goal that slugifies to
    nothing also returns ``None`` (an absent goal must NOT become ``""`` or the
    literal string ``"ungoaled"``). For any real goal the result equals
    ``slugify(goal)``, so ``"cfd solver"``, ``"cfd-solver"``, ``"CFD Solver"``
    and ``"  cfd_solver  "`` all collapse to the single bucket ``"cfd-solver"``.

    This is the on-write canonicalization and the on-compare key: normalizing a
    user-typed goal the same way it was normalized on write makes
    ``list_packets(goal=normalize_goal(g))`` aggregate across case/spacing/
    punctuation variants instead of fragmenting into disjoint exact-match
    buckets.
    """
    if goal is None:
        return None
    s = _NON_SLUG.sub("-", goal.strip().lower()).strip("-")
    s = s[:_MAX_CHARS].rstrip("-")
    return s or None
