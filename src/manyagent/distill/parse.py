"""Mechanical bundle validation — **port + harden (C3)**.

This ports the *mechanical-not-trusted-to-the-model* philosophy of
``swarms/distillation/per_task.py:_as_insight_list:284-340`` (drop Insights
missing required fields; filter ``evidence`` to the real cited-post set;
enforce caps regardless of model output) and **hardens** it for manyagent, because
``manyagent.distill.md:53`` requires two checks that swarms enforced only at
*prompt level* (``prompts.py:163-169``) to be **mechanical** here:

  * **C3-ADD #1** — ``does_not_apply_when`` ∈ {empty, "always", "never",
    "n/a", ...} ⇒ the Insight is DROPPED (an unbounded rule is rejected).
  * **C3-ADD #2** — each ``evidence[].quote`` must be a literal,
    whitespace-normalized substring of the cited post; a paraphrase ⇒ the
    evidence entry is DROPPED (verbatim, not model-judged).

Plus the swarms→manyagent ``Evidence`` remap (``schema.py``): ``post_id`` is a
packet-id **string** (no ``task_id``); swarms' ``allowed_post_ids: set[int]``
becomes the cited packet-id **string** set, resolved against the real
clustered posts. Anti-meta enforcement is the **same code** the M6 post
parser uses (``manyagent.forum.anti_meta.has_banned_meta`` / ``is_concrete``) — the
rule the curator filters against is byte-for-byte the rule the agent wrote
against. Never raises on bad model output; it drops and caps.

A dated C3 Decision-log entry on ``manyagent.distill.md`` records this divergence
from a verbatim port (the plan's only mandatory Decision-log entry for M7;
Design Principles §3).
"""

from __future__ import annotations

from typing import Any

from manyagent.distill.schema import (
    BUCKETS,
    CONFIDENCE_LEVELS,
    MAX_CONDITION,
    MAX_EVIDENCE_PER_INSIGHT,
    MAX_PER_BUCKET,
    MAX_QUOTE,
    MAX_TEXT,
    UNBOUNDED_BOUNDARIES,
    empty_bundle,
)
from manyagent.forum.anti_meta import has_banned_meta, is_concrete


def _norm(s: str) -> str:
    """Whitespace-normalized, case-sensitive — the verbatim-quote comparison
    basis (matches the swarms ``" ".join(text.splitlines())`` render
    semantics; collapses every run so a model copying through the rendered
    prompt still matches the true stored post)."""
    return " ".join(s.split())


def _post_searchable(post: dict[str, Any]) -> str:
    parts: list[str] = []
    structured = post.get("structured")
    if isinstance(structured, dict):
        parts += [v for v in structured.values() if isinstance(v, str)]
    for key in ("text", "content"):
        v = post.get(key)
        if isinstance(v, str):
            parts.append(v)
    return _norm(" ".join(parts))


def _clean_evidence(
    raw: Any,
    *,
    allowed: set[str],
    corpus: dict[str, str],
) -> tuple[list[dict[str, str]], set[str]]:
    """Return surviving evidence entries and the set of cited post ids.

    An entry survives iff its ``post_id`` resolves to a real clustered post
    (the swarms→manyagent string-id ``allowed_post_ids`` remap) AND its ``quote`` is
    a verbatim (whitespace-normalized) substring of that post (C3-ADD #2)."""
    if not isinstance(raw, list):
        return [], set()
    out: list[dict[str, str]] = []
    cited: set[str] = set()
    for ev in raw[:MAX_EVIDENCE_PER_INSIGHT]:
        if not isinstance(ev, dict):
            continue
        pid = ev.get("post_id")
        if pid is None:
            continue
        pid = str(pid).strip()
        if pid not in allowed:  # invented / non-clustered id → drop
            continue
        quote = ev.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        quote = quote.strip()[:MAX_QUOTE]
        if _norm(quote) not in corpus.get(pid, ""):  # paraphrase → drop
            continue
        out.append({"post_id": pid, "quote": quote})
        cited.add(pid)
    return out, cited


def _clean_insight(
    item: Any,
    *,
    allowed: set[str],
    corpus: dict[str, str],
    session_of: dict[str, str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or "").strip()
    if not text:
        return None
    text = text[:MAX_TEXT]
    # Same mechanical anti-meta as the M6 post parser (shared code).
    if has_banned_meta(text) is not None or not is_concrete(text):
        return None

    applies = str(item.get("applies_when") or "").strip()
    boundary = str(item.get("does_not_apply_when") or "").strip()
    if not applies or not boundary:
        return None
    if boundary.lower() in UNBOUNDED_BOUNDARIES:  # C3-ADD #1: unbounded → drop
        return None

    evidence, cited = _clean_evidence(item.get("evidence"), allowed=allowed, corpus=corpus)
    if not evidence:  # no real verbatim grounding → drop
        return None

    confidence = str(item.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "low"
    # Recurrence promotion (mechanical weighting; manyagent.distill.md:80): a
    # primitive independently cited by posts from >=2 distinct sessions → high.
    if len({session_of.get(pid, pid) for pid in cited}) >= 2:
        confidence = "high"

    return {
        "text": text,
        "applies_when": applies[:MAX_CONDITION],
        "does_not_apply_when": boundary[:MAX_CONDITION],
        "evidence": evidence,
        "confidence": confidence,
    }


def validate_bundle(
    payload: Any,
    *,
    posts: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Coerce an LLM-emitted bundle into a mechanically valid one.

    ``posts`` are the real clustered post records the curation ran over; only
    evidence citing one of them, with a verbatim quote, survives. Always
    returns all six buckets (empty lists where nothing survived — empty is
    correct and preferable to filler). Never trusts the model.
    """
    allowed = {str(p["id"]) for p in posts}
    corpus = {str(p["id"]): _post_searchable(p) for p in posts}
    session_of = {str(p["id"]): str(p.get("session_id") or str(p["id"]).split("/")[0]) for p in posts}

    bundle = empty_bundle()
    if not isinstance(payload, dict):
        return bundle
    for bucket in BUCKETS:
        items = payload.get(bucket)
        if not isinstance(items, list):
            continue
        kept: list[dict[str, Any]] = []
        for item in items:
            cleaned = _clean_insight(item, allowed=allowed, corpus=corpus, session_of=session_of)
            if cleaned is not None:
                kept.append(cleaned)
            if len(kept) >= MAX_PER_BUCKET:  # hard cap regardless of model output
                break
        bundle[bucket] = kept
    return bundle
