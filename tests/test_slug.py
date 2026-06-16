"""Tests for manyagent.utils.slug — the goal → URL slug codec (mirrors
``web/viewer/src/lib/slug.js``; keep the two in lockstep)."""

from __future__ import annotations

import pytest

from manyagent.utils.slug import slugify


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
