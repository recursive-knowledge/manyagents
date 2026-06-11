"""The falsifiable post-mortem schema (manyagent.forum.md "The post packet").

swarms' per-task post-mortem schema (``forum_prompt.py:668-675``) adapted to
manyagent: *a falsifiable claim, not a summary*. This module is **pure** — purely
structural validation of the agent-emitted ``structured`` jsonb. Bank-grounded
checks (``evidence_ref`` resolves, no-history, quarantine) live in
``manyagent.forum.parser``.

The structure IS the agent tax (Design Principles §11): the practitioner never
authors this; the CLI agent that already holds the session does, against the
injected skill prompt.
"""

from __future__ import annotations

from typing import Any

# All non-empty, agent-generated. ``evidence_ref`` is intentionally NOT here:
# null is valid (the claim is grounded in the author's own trace).
REQUIRED_FIELDS: tuple[str, ...] = (
    "load_bearing_assumption",
    "evidence",
    "proposed_next",
    "predicted_outcome",
    "confidence",
)
CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})

# Field caps (mirror swarms' mechanical truncation: text[:240], quote[:200]).
_MAX_FIELD = 2000


def validate_schema(structured: Any) -> str | None:
    """Return ``None`` if ``structured`` is a conformant post-mortem, else a
    short human reason (the parser surfaces it so the CLI can re-prompt — C1:
    a non-conformant ``/self-distill`` post is **not** persisted)."""
    if not isinstance(structured, dict):
        return "structured body must be a JSON object"
    for field in REQUIRED_FIELDS:
        val = structured.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            return f"missing or empty required field {field!r}"
        if not isinstance(val, str):
            return f"field {field!r} must be a string"
        if len(val) > _MAX_FIELD:
            return f"field {field!r} exceeds {_MAX_FIELD} chars (bounded)"
    confidence = structured["confidence"].strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        return f"confidence must be one of {sorted(CONFIDENCE_LEVELS)}, got {structured['confidence']!r}"
    ref = structured.get("evidence_ref")
    if ref is not None and not (isinstance(ref, str) and ref.strip()):
        return "evidence_ref must be a packet-id string or null"
    return None
