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
    "STRICT ANTI-META RULES (apply these before you write anything):\n"
    "- REJECT generic process meta-advice. Drop any Insight that reads like "
    'these: "validate first", "decompose before solving", "check edge '
    'cases", "think step by step", "verify boundary conditions", "test '
    'incrementally", "iterate", "reason carefully", "handle errors". Drop '
    "any wording that fits every coding task or reasoning task. Use this "
    "test: if you can lift the Insight into a software-engineering tutorial "
    "and change nothing, it is not an Insight. Drop it.\n"
    "- REQUIRE concrete grounding. Each Insight must name at least one "
    "concrete primitive that comes from the posts. A concrete primitive is "
    "an API call, a function name, a class name, an import, a file path, a "
    "CLI flag, a data-shape invariant, an error type, a grid or shape "
    "operation, or a named code pattern. Abstract nouns alone "
    '("structure", "pattern", "approach") do NOT count as concrete.\n'
    "- REQUIRE evidence grounding. Each Insight must come from at least one "
    "post in the input. Record that support in the Insight's `evidence` "
    "list. Each entry needs the cited `post_id` and a verbatim `quote` from "
    "that post. Do not invent a post id. Do not paraphrase the quote: the "
    "parser compares it against the post and drops an Insight when the "
    "quote is not literal.\n"
    "- PREFER transferable wording. Word a cross-goal Insight so it applies "
    "to more than one goal, but keep the primitive concrete. Example: write "
    '"BFS flood-fill on an 8-neighborhood to isolate connected regions of '
    'the same colour". That names an operation and still fits many goals.\n'
    "- QUALITY OVER QUANTITY. Return at most 5 insights, 5 pitfalls, "
    "and 5 checks. Choose the best Insights, not the most. An empty list is "
    "correct when the posts carry no concrete signal.\n"
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
    "STRICT ANTI-META RULES (apply these before you write anything):\n"
    "- REJECT generic process meta-advice. The parser drops this post when a "
    "field contains any of these: "
    + ", ".join(f'"{p}"' for p in BANNED_META_PHRASES)
    + ". It also drops wording that you could lift into a "
    "software-engineering tutorial and change nothing.\n"
    "- REQUIRE concrete grounding. Each field must name a concrete primitive "
    "that you touched in this session. A concrete primitive is an API call, "
    "a function name, a class name, an import, a file path, a CLI flag, a "
    "shell command, or a named code pattern. Abstract nouns alone "
    '("structure", "pattern", "approach") do NOT count as concrete.\n'
    "  Put the primitive in `backticks`. The parser looks for code-shaped "
    "text, and a bare command in prose does not read as code-shaped. Write "
    "`systemctl restart nginx` returns 0, not: systemctl restart nginx "
    "returns 0. The second form is dropped even though it says the same "
    "thing.\n"
    "- REQUIRE evidence grounding. Copy `evidence` word for word from this "
    "session's trace. You may instead cite one prior post and put its packet "
    "id in `evidence_ref`. Never invent a citation.\n"
    "- An unresolved question is NOT a result. Sometimes a session ends with "
    "a question still open, or with a step still blocked. Then write the "
    "post about the thing that blocked you. Do not assert an answer that the "
    'session did not establish. Set `confidence` to "low".\n'
)

# Rendered-prompt CI guard (mirrors swarms ``_REQUIRED_PHRASES``): if any
# clause is missing from the injected prompt the agent cannot see the
# blacklist, silently defeating the discipline.
#
# Split into shared (present in BOTH the curator block and the post block)
# and curator-only (present ONLY in ANTI_META_BLOCK / distill prompts).
# ``assert_anti_meta_rules_present`` checks only the shared phrases by
# default for a post prompt, so that ``render_post_prompt`` output does
# not raise a false-positive AssertionError.
_SHARED_REQUIRED_PHRASES: tuple[str, ...] = (
    "STRICT ANTI-META RULES",
    "validate first",
    "decompose before solving",
    "check edge cases",
    "boundary conditions",
    "REQUIRE concrete grounding",
    "Abstract nouns alone",
    "REQUIRE evidence grounding",
)

# Phrases that exist ONLY in the curator block. ``evidence_post_ids`` used to
# head this list, inherited from swarms — but manyagent has no such field. Its
# grounding lives in each Insight's ``evidence: [{post_id, quote}]``, and
# ``distill.parse.validate_bundle`` reads nothing else, so the clause told the
# curator to fill a field the parser never looks at while the output schema in
# the same prompt asked for ``evidence[]``. Two grounding mechanisms in one
# prompt cost real Insights: anything grounded only via the dead field arrives
# with an empty ``evidence`` list and the parser drops it. The clause now names
# the real field, and the anchor below moved to wording unique to that clause.
_CURATOR_ONLY_PHRASES: tuple[str, ...] = (
    "at most 5 insights",
    "5 pitfalls",
    "5 checks",
    "PREFER transferable wording",
)

# Kept for backward compatibility — the full union used by the curator prompt.
_REQUIRED_PHRASES: tuple[str, ...] = _SHARED_REQUIRED_PHRASES + _CURATOR_ONLY_PHRASES


def assert_anti_meta_rules_present(text: str, *, post_prompt: bool = False) -> None:
    """CI helper: confirm a rendered prompt exposes every required clause.

    When ``post_prompt=True`` only the shared phrases are checked — the
    curator-only phrases (``evidence_post_ids``, insight/pitfall/check caps)
    are intentionally absent from the post-flow prompt and must not raise a
    false-positive AssertionError against ``render_post_prompt()`` output.
    """
    phrases = _SHARED_REQUIRED_PHRASES if post_prompt else _REQUIRED_PHRASES
    for phrase in phrases:
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
# --flag, a /path, an Error/Exception name, a [severity] marker.
#
# The alternatives above the last one came from swarms, whose benchmarks were
# ARC, SWE-bench and polyglot — grid operations and code identifiers. KSI also
# evaluates a *terminal* family, where the concrete primitives are log markers
# and command output rather than code. Measured on the terminal scenario in
# ``scripts/simulate_ksi.py``: the Insight "nginx accepts duplicate listen
# directives in different files, causing [emerg] errors" was dropped as
# non-concrete while a sibling Insight built from the *same* post survived,
# because the model happened to backtick ``nginx -t`` there and not here. The
# bracketed-severity alternative closes that gap. It stays deliberately narrow
# — a lowercase word in square brackets, which is log-level shaped — because
# every widening of this gate admits prose into the corpus.
CONCRETE_RE = re.compile(
    r"`[^`]+`"  # backticked token
    r"|\b\w+\.\w+"  # dotted path / attribute / file.ext
    r"|\b\w+\("  # a call
    r"|--?[A-Za-z][\w-]+"  # a CLI flag
    r"|/[\w./-]+"  # a path
    r"|\b[a-z]+_[a-z_]+\b"  # snake_case
    r"|\b[A-Z][a-z]+[A-Z]\w+\b"  # CamelCase
    r"|\b\w+(?:Error|Exception)\b"  # an error type
    r"|\[[a-z]{3,}\]"  # a [severity] / log-level marker
)


def is_concrete(text: str) -> bool:
    """True iff ``text`` names a concrete primitive and is not a bare abstract
    noun ("structure"/"pattern"/"approach") — the ANTI_META_BLOCK "Abstract
    nouns alone do NOT count as concrete" clause, made mechanical."""
    stripped = text.strip()
    if stripped.lower() in ABSTRACT_NOUNS:
        return False
    return CONCRETE_RE.search(stripped) is not None


# Whole-word pattern for the single-word phrase "iterate": substring matching
# would falsely flag "iteration", "iterator", "max_iter=", "reiterate", etc.
# All other banned phrases are multi-word and cannot collide this way.
_ITERATE_RE = re.compile(r"\biterate\b", re.IGNORECASE)


def has_banned_meta(text: str) -> str | None:
    """Return the first banned process-meta phrase present in ``text``
    (case-insensitive verbatim substring for multi-word phrases; whole-word
    regex for the single-word "iterate" to avoid matching "iteration" /
    "iterator" / "max_iter"), else ``None``. Mechanical, not model judgement
    (manyagent.forum.md:62)."""
    low = text.lower()
    for phrase in BANNED_META_PHRASES:
        if phrase == "iterate":
            if _ITERATE_RE.search(text):
                return phrase
        elif phrase in low:
            return phrase
    return None
