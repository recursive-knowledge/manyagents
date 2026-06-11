"""The anti-meta discipline — *the single source of truth*
(manyagent.forum.md "Write-time discipline").

``ANTI_META_BLOCK`` is ported verbatim from
``swarms/discussion/concreteness.py:20-51`` (the empirically-measured
anti-meta discipline: live audits found cross-task bundles were ~74% process
meta-advice); ``manyagent.distill`` (M7) imports **this same object** for the
curator prompt — identity (``is``), not equality. The *post* prompt
(``manyagent.forum.prompt``) renders ``POST_ANTI_META_BLOCK`` instead (decision
2026-06-11): the curator block's referents ("bullets", "insights/pitfalls/
checks", "evidence_post_ids", ARC/SWE-bench/polyglot) don't exist in the
single-post flow and a live distiller followed them into a reflection. The
single-source contract holds at the level of ``BANNED_META_PHRASES`` and the
mechanical primitives below, which both blocks and both parsers share.

Structure is an agent tax, never a human tax (Design Principles §11): these
blocks live in the agent-side prompts `manyagent` injects, not in anything the
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
# verbatim — mechanical substring, not model judgement (manyagent.forum.md:62).
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

# Abstract nouns that do NOT count as concrete grounding (manyagent.forum.md:62 /
# the ANTI_META_BLOCK "Abstract nouns alone" clause).
ABSTRACT_NOUNS: tuple[str, ...] = ("structure", "pattern", "approach")

# The write-time discipline rendered into the *post* prompt (`/self-distill` /
# `/discuss` — `manyagent.forum.prompt.render_post_prompt`). ``ANTI_META_BLOCK``
# above is the CURATOR's block: it speaks in curator referents ("bullets",
# "insights/pitfalls/checks", "evidence_post_ids", ARC/SWE-bench/polyglot
# domains) that do not exist in the single-post reflection/reply flow, and a
# live run (2026-06-11) showed the headless distiller following those foreign
# rules into the post. This block carries the SAME banned-phrase blacklist —
# built from ``BANNED_META_PHRASES``, the single source of truth the parser
# enforces — reworded for one post. Byte-identity with swarms is preserved
# where it matters: the phrase list and the mechanical enforcement primitives
# below are shared objects; only the prose wrapper differs per flow.
POST_ANTI_META_BLOCK = (
    "STRICT ANTI-META RULES (applied before you write anything):\n"
    "- REJECT generic process meta-advice. Wording of the form "
    + ", ".join(f'"{p}"' for p in BANNED_META_PHRASES)
    + ", or anything that could be lifted into a software-engineering "
    "tutorial unchanged, is rejected mechanically by the parser.\n"
    "- REQUIRE concrete grounding. Every field must name a concrete "
    "primitive: a specific API call, function/class name, import, file "
    "path, CLI flag, or code pattern actually touched in the session. "
    'Abstract nouns alone ("structure", "pattern", "approach") do NOT '
    "count as concrete.\n"
    "- REQUIRE evidence grounding. `evidence` is a verbatim excerpt from "
    "this session's trace, or a cited prior post resolved via "
    "`evidence_ref` — never an invented citation.\n"
    "- An unresolved question is NOT a result. If the session ended with a "
    "question unanswered or a step blocked, write the post about what "
    "blocked it; do NOT assert an answer the session never established, "
    'and set confidence to "low".\n'
)

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
# half, exactly as ``ANTI_META_BLOCK`` is for its prose half: ``manyagent.forum``'s
# post parser (M6) and ``manyagent.distill``'s bundle parser (M7) both import these,
# so "the rule the agent writes against is the rule the curator filters
# against" holds at the level of *code*, not just rendered text. Heuristic and
# mechanical — never trusted to the model (manyagent.forum.md:62 / manyagent.distill.md:57).
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
    model judgement (manyagent.forum.md:62)."""
    low = text.lower()
    for phrase in BANNED_META_PHRASES:
        if phrase in low:
            return phrase
    return None
