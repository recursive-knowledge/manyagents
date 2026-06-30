"""Tests for manyagent.utils.slug — the goal → URL slug codec (mirrors
``web/viewer/src/lib/slug.js``; keep the two in lockstep)."""

from __future__ import annotations

import pytest

from manyagent.utils.slug import normalize_goal, slugify


def test_basic_phrase() -> None:
    assert slugify("paper review 4") == "paper-review-4"


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ("  CFD Solver (revised!) ", "cfd-solver-revised"),  # case, punctuation, surrounding ws
        ("a___b---c   d", "a-b-c-d"),  # runs of non-slug collapse to one '-'
        ("Rust async runtime", "rust-async-runtime"),
        ("ml-training-loop", "ml-training-loop"),  # already slug-shaped: idempotent
    ],
)
def test_normalization(goal: str, expected: str) -> None:
    assert slugify(goal) == expected
    assert slugify(expected) == expected  # idempotent on a slug


def test_none_blank_and_punctuation_are_ungoaled() -> None:
    assert slugify(None) == "ungoaled"
    assert slugify("") == "ungoaled"
    assert slugify("   ") == "ungoaled"
    assert slugify("!!!") == "ungoaled"
    assert slugify("(ungoaled)") == "ungoaled"  # agrees with the viewer's display key


def test_truncates_to_80_with_no_trailing_hyphen() -> None:
    s = slugify("word " * 50)  # 250 raw chars
    assert len(s) <= 80
    assert not s.endswith("-")
    assert s == ("word-" * 16)[:80].rstrip("-")  # deterministic cut


def test_collision_two_near_identical_goals_share_one_slug() -> None:
    # Intentional: the slug is a derived board key, not an identity.
    assert slugify("Paper Review 4") == slugify("paper-review-4") == "paper-review-4"


def test_non_ascii_letters_drop_out() -> None:
    assert slugify("café résumé") == "caf-r-sum"


# --------------------------------------------------------------------------- #
# normalize_goal — the canonical storage/match key (preserves the "no goal"
# sentinel; never invents "ungoaled"). The aggregation keystone (decision #1).
# --------------------------------------------------------------------------- #


def test_normalize_goal_four_variants_collapse_to_one_slug() -> None:
    # The thesis: these four disjoint exact-match buckets must aggregate to one.
    variants = ["cfd solver", "cfd-solver", "CFD Solver", "  cfd_solver  "]
    slugs = {normalize_goal(v) for v in variants}
    assert slugs == {"cfd-solver"}


def test_normalize_goal_preserves_none() -> None:
    # An absent goal stays None — never "" and never the literal "ungoaled".
    assert normalize_goal(None) is None


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", "!!!", "(  )", "---"])
def test_normalize_goal_empty_or_punctuation_is_none(blank: str) -> None:
    # Slugifies to nothing → the no-goal sentinel (None), not "" or "ungoaled".
    assert normalize_goal(blank) is None


def test_normalize_goal_matches_slugify_for_real_goals() -> None:
    for g in ["paper review 4", "Rust async runtime", "ml-training-loop"]:
        assert normalize_goal(g) == slugify(g)


def test_normalize_goal_is_idempotent() -> None:
    once = normalize_goal("  CFD Solver (revised!) ")
    assert once == "cfd-solver-revised"
    assert normalize_goal(once) == once
