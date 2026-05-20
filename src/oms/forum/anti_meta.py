"""The byte-identical ``ANTI_META_BLOCK`` — *the single source of truth*
(oms.forum.md "Write-time discipline").

Ported verbatim from ``swarms/discussion/concreteness.py:20-51`` (the
empirically-measured anti-meta discipline: live audits found cross-task
bundles were ~74% process meta-advice). ``oms.forum`` renders it into the
agent-side post prompt; ``oms.distill`` (M7) imports **this same object** so
the rule the agent writes against is byte-for-byte the rule the curator
filters against. The contract is identity (``is``), not equality — there is
exactly one definition, here.

Structure is an agent tax, never a human tax (Design Principles §11): this
block lives in the agent-side skill prompt `oms` injects, not in anything the
practitioner sees.
"""

from __future__ import annotations

import re

ANTI_META_BLOCK = (
    "STRICT ANTI-META RULES (applied before you write anything):\n"
    "- REJECT generic process meta-advice. Insights of the form "
    '"validate first", "decompose before solving", "check edge '
    'cases", "think step by step", "verify boundary conditions", '
    '"test incrementally", "iterate", "reason carefully", '
    '"handle errors", or any wording that could apply to literally '
    "any coding/reasoning task are REJECTED. If your bullet could be "
    "lifted and dropped into a software-engineering tutorial unchanged, "
    "it is not an insight -- drop it.\n"
    "- REQUIRE concrete grounding. Every bullet must name at least one "
    "concrete primitive drawn from the posts: e.g. a specific grid "
    "operation, color index, shape signature, transformation rule "
    "(ARC); a specific API call, function/class name, import, file "
    "path, or code pattern (SWE-bench); a specific language feature, "
    "library function, stdlib module, or test-runner flag (polyglot). "
    'Abstract nouns alone ("structure", "pattern", "approach") do '
    "NOT count as concrete.\n"
    "- REQUIRE evidence grounding. Every bullet must be derivable from "
    "at least one forum post or attempt in the input. Put supporting forum "
    "post IDs in evidence_post_ids when posts support the bullet. Do not "
    "invent post IDs; if the only support is an attempt, leave "
    "evidence_post_ids empty and make the attempt grounding explicit.\n"
    "- PREFER transferable wording. For cross-task insights, describe "
    "the primitive generically enough to apply across multiple tasks, "
    'but keep the primitive itself concrete (e.g. "BFS flood-fill on '
    '8-neighborhood to isolate connected regions of the same color" -- '
    "concrete operation, still task-agnostic).\n"
    "- QUALITY OVER QUANTITY. Return at most 5 insights, 5 pitfalls, "
    "and 5 checks. Pick the best bullets, not the most. Empty lists "
    "are fine when there is no concrete signal.\n"
)

# The enumerated banned process-meta phrases (the empirically-measured failure
# payload). The parser rejects a post whose body contains any of these
# verbatim — mechanical substring, not model judgement (oms.forum.md:62).
BANNED_META_PHRASES: tuple[str, ...] = (
    "validate first",
    "decompose before solving",
    "check edge cases",
    "think step by step",
    "verify boundary conditions",
    "test incrementally",
    "iterate",
    "reason carefully",
    "handle errors",
)

# Abstract nouns that do NOT count as concrete grounding (oms.forum.md:62 /
# the ANTI_META_BLOCK "Abstract nouns alone" clause).
ABSTRACT_NOUNS: tuple[str, ...] = ("structure", "pattern", "approach")

# Rendered-prompt CI guard (mirrors swarms ``_REQUIRED_PHRASES``): if any
# clause is missing from the injected prompt the agent cannot see the
# blacklist, silently defeating the discipline.
_REQUIRED_PHRASES: tuple[str, ...] = (
    "STRICT ANTI-META RULES",
    "validate first",
    "decompose before solving",
    "check edge cases",
    "boundary conditions",
    "REQUIRE concrete grounding",
    "Abstract nouns alone",
    "REQUIRE evidence grounding",
    "evidence_post_ids",
    "at most 5 insights",
    "5 pitfalls",
    "5 checks",
)


def assert_anti_meta_rules_present(text: str) -> None:
    """CI helper: confirm a rendered prompt exposes every required clause."""
    for phrase in _REQUIRED_PHRASES:
        if phrase not in text:
            raise AssertionError(
                f"Expected anti-meta phrase {phrase!r} in rendered prompt; "
                f"missing means the agent cannot see the blacklist."
            )


# --------------------------------------------------------------------------- #
# Shared mechanical enforcement primitives
#
# These are the *single source of truth* for the anti-meta rule's mechanical
# half, exactly as ``ANTI_META_BLOCK`` is for its prose half: ``oms.forum``'s
# post parser (M6) and ``oms.distill``'s bundle parser (M7) both import these,
# so "the rule the agent writes against is the rule the curator filters
# against" holds at the level of *code*, not just rendered text. Heuristic and
# mechanical — never trusted to the model (oms.forum.md:62 / oms.distill.md:57).
# --------------------------------------------------------------------------- #

# A concrete primitive looks like code/identifier material, not prose: a
# `backticked` token, dotted.path, snake_case/CamelCase id, a call(), a
# --flag, a /path, an Error/Exception name.
CONCRETE_RE = re.compile(
    r"`[^`]+`"  # backticked token
    r"|\b\w+\.\w+"  # dotted path / attribute / file.ext
    r"|\b\w+\("  # a call
    r"|--?[A-Za-z][\w-]+"  # a CLI flag
    r"|/[\w./-]+"  # a path
    r"|\b[a-z]+_[a-z_]+\b"  # snake_case
    r"|\b[A-Z][a-z]+[A-Z]\w+\b"  # CamelCase
    r"|\b\w+(?:Error|Exception)\b"  # an error type
)


def is_concrete(text: str) -> bool:
    """True iff ``text`` names a concrete primitive and is not a bare abstract
    noun ("structure"/"pattern"/"approach") — the ANTI_META_BLOCK "Abstract
    nouns alone do NOT count as concrete" clause, made mechanical."""
    stripped = text.strip()
    if stripped.lower() in ABSTRACT_NOUNS:
        return False
    return CONCRETE_RE.search(stripped) is not None


def has_banned_meta(text: str) -> str | None:
    """Return the first banned process-meta phrase present in ``text``
    (case-insensitive verbatim substring), else ``None``. Mechanical, not
    model judgement (oms.forum.md:62)."""
    low = text.lower()
    for phrase in BANNED_META_PHRASES:
        if phrase in low:
            return phrase
    return None
