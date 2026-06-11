"""The mechanical post parser (manyagent.forum.md "Write-time discipline").

Enforcement is **mechanical, not trusted to the model** — exactly the
philosophy of ``swarms/distillation/per_task.py:_as_insight_list`` (validate
``allowed_post_ids``, enforce caps regardless of model behaviour). Ported and
**hardened** for manyagent: swarms' ``evidence_post_id: int`` + ``task_id`` becomes
manyagent's ``evidence_ref``: a packet-id **string**, no task (the M6 analog of the
C3 swarms→manyagent ``Evidence`` mapping); plus manyagent-specific Bank-grounded checks
(``evidence_ref`` resolves, no-history hardening, quarantine refusal).

**C1 (manyagent.core.md:70/98; manyagent.forum.md:89):** :func:`parse_post` never
persists and never sets ``preference``. A rejected ``/self-distill`` post is
**not stored** — it returns ``(False, reason)`` so the CLI re-prompts the
agent. ``preference=accept|reject`` is distill-only (set via
``/cross-distill``), and the M3 model now rejects it on a post mechanically.
"""

from __future__ import annotations

import re
from typing import Any

from manyagent.bank import Bank
from manyagent.forum.anti_meta import has_banned_meta, is_concrete
from manyagent.forum.schema import validate_schema

_KINDS = {"reflection", "reply"}

# Forge protection (mirrors swarms ``_sanitize_agent_output``): a standalone
# protocol token on its own line in agent/trace-sourced text is bracketed so
# no downstream prompt render or block parser can treat it as real protocol.
_PROTOCOL_TOKENS = ("INSIGHT", "COMMENT", "EVIDENCE", "EVIDENCE_REF", "REPLY_TO", "STANCE", "POST")
_FORGE_RE = re.compile(rf"(?m)^(\s*)({'|'.join(_PROTOCOL_TOKENS)})(\s*:?\s*)$")


def _sanitize(text: str) -> str:
    return _FORGE_RE.sub(r"\1[\2]\3", text)


def _reject(reason: str) -> tuple[bool, str]:
    return (False, reason)


async def parse_post(record: dict[str, Any], *, bank: Bank) -> tuple[bool, dict[str, Any] | str]:  # noqa: C901 — sequential mechanical validation; the branches ARE the parser
    """Validate a candidate ``post`` record mechanically.

    Returns ``(True, sanitized_record)`` for the caller to persist, or
    ``(False, reason)`` — **not persisted** (C1). Never sets ``preference``.
    """
    if record.get("type") != "post":
        return _reject("not a post packet")
    kind = record.get("kind")
    if kind not in _KINDS:
        return _reject(f"kind must be one of {sorted(_KINDS)}, got {kind!r}")

    sid = str(record.get("session_id") or str(record.get("id", "")).split("/")[0])
    if not sid:
        return _reject("cannot determine session_id")
    goal = record.get("goal")

    # --- structural schema (pure) ---
    structured = record.get("structured")
    reason = validate_schema(structured)
    if reason is not None:
        return _reject(reason)
    assert isinstance(structured, dict)  # noqa: S101 — pure type-narrowing for mypy; validate_schema already gated

    # --- forge protection: neutralize protocol tokens in every text field ---
    clean_structured = {k: (_sanitize(v) if isinstance(v, str) else v) for k, v in structured.items()}
    blob = " ".join(str(clean_structured[f]) for f in clean_structured if isinstance(clean_structured[f], str))

    # --- banned process-meta (verbatim substring, case-insensitive) ---
    banned = has_banned_meta(blob)
    if banned is not None:
        return _reject(f"banned process-meta phrase present: {banned!r}")

    # --- concrete grounding: the load-bearing claim must name a primitive ---
    claim = str(clean_structured["load_bearing_assumption"])
    if not is_concrete(claim):
        return _reject("load_bearing_assumption is not concrete (names no specific primitive)")

    # --- prior posts under this goal (no-history hardening scope) ---
    prior = await bank.list_packets(session_id=sid, type="post", goal=goal)
    prior = [p for p in prior if p.get("id") != record.get("id")]
    has_history = bool(prior)

    evidence_ref = clean_structured.get("evidence_ref")
    reply_to = record.get("reply_to")

    if not has_history and (evidence_ref or reply_to):
        return _reject("no prior posts exist under this goal — citations forbidden (no-history hardening)")

    # --- forge: a cited evidence_ref MUST resolve to a real, non-quarantined packet ---
    if evidence_ref:
        cited = await bank.get_packet(str(evidence_ref))
        if cited is None:
            return _reject(f"evidence_ref {evidence_ref!r} cites a non-existent packet (forge/hallucination)")
        if cited.get("quarantined"):
            return _reject(f"evidence_ref {evidence_ref!r} cites a quarantined packet")

    # --- reply discipline ---
    if kind == "reply":
        stance = record.get("stance")
        if not reply_to or not stance:
            return _reject("a reply requires reply_to and stance")
        parent = await bank.get_packet(str(reply_to))
        if parent is None:
            return _reject(f"reply_to {reply_to!r} does not exist")
        if parent.get("quarantined"):
            return _reject(f"cannot reply to a quarantined packet {reply_to!r}")
    elif reply_to or record.get("stance"):
        return _reject("a reflection must not carry reply_to/stance")

    out = dict(record)
    out["structured"] = clean_structured
    out.pop("preference", None)  # C1: a post never carries preference
    return (True, out)
