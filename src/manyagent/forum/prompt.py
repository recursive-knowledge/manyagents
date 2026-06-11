"""The agent-side post prompt renderer (manyagent.forum.md "Write-time discipline":
*rendered into the agent-side post prompt*).

This lives in ``manyagent.forum`` on purpose: the module that owns the parser also
owns the rendered rule, so the rule the agent writes against is the rule the
parser filters against — it embeds ``POST_ANTI_META_BLOCK``, whose banned
phrases are built from the *same* ``BANNED_META_PHRASES`` tuple the parser
enforces (the curator-worded ``ANTI_META_BLOCK`` stays the curator's;
decision 2026-06-11). Structure is an agent tax, never a human tax (Design
Principles §11): the CLI renders this for the agent; the practitioner never
sees it.

No-history hardening (manyagent.forum.md "Forge protection"): when a ``goal`` has no
prior posts the prompt explicitly forbids citing post ids, because a
hallucinated citation otherwise gets curated and amplified.
"""

from __future__ import annotations

from typing import Any

from manyagent.forum.anti_meta import POST_ANTI_META_BLOCK

_SCHEMA = (
    "Emit ONE JSON object, nothing else, with exactly these keys:\n"
    "{\n"
    '  "load_bearing_assumption": "<the ONE assumption the work relied on; '
    "concrete — names a specific tool/API/file/data-shape/invariant, not "
    "'be careful'>\",\n"
    '  "evidence": "<verbatim 1-3 sentence excerpt from THIS session\'s trace '
    'OR a cited prior post; not a paraphrase>",\n'
    '  "evidence_ref": "<packet id of the cited prior post, or null if '
    'grounded in your own trace>",\n'
    '  "proposed_next": "<ONE concrete change a future agent should try; '
    'names a file/tool/API/decision-point; differs from what was tried>",\n'
    '  "predicted_outcome": "<a falsifiable prediction of what happens if '
    'proposed_next is applied>",\n'
    '  "confidence": "high | medium | low"\n'
    "}\n"
    "A falsifiable claim, not a summary. The parser DROPS this post "
    "mechanically if a field is missing/empty, if it names no concrete "
    "primitive, if it contains banned process-meta wording, or (for a "
    "citation) if evidence_ref does not resolve to a real post — so write it "
    "grounded or not at all."
)


def render_post_prompt(
    *,
    kind: str,
    goal: str | None,
    guidance: str | None = None,
    prior_posts: list[dict[str, Any]] | None = None,
    trace_context: str | None = None,
) -> str:
    """Render the agent-side prompt for a ``reflection`` (``/self-distill``)
    or ``reply`` (``/discuss``) post. Embeds ``POST_ANTI_META_BLOCK``.

    ``trace_context`` carries the session's mined trace excerpt for callers
    whose model did NOT live the session (the headless post-exit path in
    ``manyagent._handlers``): the schema demands a verbatim excerpt from "THIS
    session's trace", and a fresh headless model has no trace unless the
    prompt brings it. The in-agent MCP path passes None — the host LLM is
    the agent and already holds the conversation."""
    head = (
        "You are writing one structured forum post about the session you just "
        "completed. This is not a chat reply to a human — it is a falsifiable "
        "post-mortem a future agent will be seeded with."
    )
    scope = f"Goal scope: {goal}." if goal else "No goal scope (ungoaled)."
    parts = [head, scope, "", POST_ANTI_META_BLOCK, "", _SCHEMA]

    if kind == "reply":
        prior = prior_posts or []
        if not prior:
            parts += [
                "",
                "There are no prior posts under this goal — do NOT reference any post id (no-history hardening).",
            ]
        else:
            listing = "\n".join(
                f"- {p.get('id')}: {(p.get('structured') or {}).get('load_bearing_assumption', '')}" for p in prior
            )
            parts += [
                "",
                "Engage ONE of these retrieved prior posts (set evidence_ref to its id and take a clear stance):",
                listing,
            ]
    elif not (prior_posts or []):
        parts += ["", "No prior posts exist under this goal — set evidence_ref to null; do NOT cite a post id."]

    if trace_context:
        parts += [
            "",
            "Session trace (mined from the completed session). This trace is "
            "your ONLY record of the session — do not treat files, git "
            "status, project instructions, or anything else in your current "
            "environment as session evidence. `evidence` MUST be a verbatim "
            "excerpt from this trace, not a paraphrase:",
            "--- BEGIN TRACE ---",
            trace_context,
            "--- END TRACE ---",
        ]

    if guidance:
        parts += ["", f"Operator guidance: {guidance}"]
    return "\n".join(parts)
