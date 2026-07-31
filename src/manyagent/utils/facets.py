"""Goal facet aggregation — the reference semantics for the ``goal_facets`` view.

The viewer's home table shows three per-goal counts — **threads**, **digests**,
**agents**. At scale these are computed by the database (the ``goal_facets`` SQL
view, migration ``00012``), not by scanning the corpus in the app. This module
is the **pure-Python mirror of that view's aggregation**: ``FakeBank`` uses it so
offline tests exercise the same dedup rules without Postgres. It lives in
``manyagent.utils`` (the bottom layer) so both ``manyagent.bank`` and
``manyagent.web`` may import it.

The derivation mirrors the SQL view *and* the viewer's ``explorer.js``
(``deriveThreads`` / ``deriveMembers``) — keep all three in lockstep:

* **thread** — a root reflection post (``kind != "reply"``), deduped across
  authors by ``(goal, canonical(structured))``: several agents committing the
  same reflection under one goal is *one* thread with several authors.
* **digest** — a ``distill`` packet (the curator's 6-bucket bundle).
* **agent** — a distinct ``agent_id`` among the goal's ``post`` packets.

Goals are keyed by their URL **slug** (``manyagent.utils.slug``); two
near-identical goals intentionally collapse onto one board, exactly as the
viewer's client-side slug match did.
"""

from __future__ import annotations

import json
from typing import Any

from manyagent.utils.slug import slugify


def _thread_key(row: dict[str, Any]) -> str:
    """The cross-author dedup key for a root reflection, byte-compatible with
    ``explorer.js``'s ``` `${goal} ${JSON.stringify(structured ?? id)}` ```.

    ``separators=(",", ":")`` reproduces ``JSON.stringify``'s space-free output
    and ``ensure_ascii=False`` matches its literal (non-``\\u``) encoding of
    non-ASCII text. Object key order is preserved on both sides (the forum
    schema emits the same field order), so identical reflections collide."""
    structured = row.get("structured")
    payload = structured if structured is not None else row["id"]
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"{row.get('goal') or ''} {body}"


def _is_reply(row: dict[str, Any]) -> bool:
    return row.get("kind") == "reply" and bool(row.get("reply_to"))


def _newer(row: dict[str, Any], than: dict[str, Any] | None) -> bool:
    return than is None or (row.get("created_at") or "") > (than.get("created_at") or "")


def _fold_post(card: dict[str, Any], row: dict[str, Any]) -> None:
    if row.get("agent_id"):
        card["agents"].add(row["agent_id"])
    if _is_reply(row):
        return
    card["roots"].add(_thread_key(row))
    if _newer(row, card["_reflection"]):
        card["_reflection"] = row


def _fold(card: dict[str, Any], row: dict[str, Any]) -> None:
    """Accumulate one packet into its goal card (mutates ``card``)."""
    if card["label"] is None and row.get("goal"):
        card["label"] = row["goal"]
    created = row.get("created_at") or ""
    if created > card["latest"]:
        card["latest"] = created
    if row.get("type") == "post":
        _fold_post(card, row)
    elif row.get("type") == "distill":
        card["digests"] += 1
        if _newer(row, card["_distill"]):
            card["_distill"] = row


def aggregate_goals(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-goal facet cards over the full packet set, newest-active first.

    Each card carries the authoritative ``threads`` / ``digests`` / ``agents``
    counts plus the raw material the viewer needs to render a goal's "about"
    line client-side (``latest_distill_bundle`` / ``latest_reflection_structured``
    — kept as JS so the prose formatting stays in one place)."""
    by_slug: dict[str, dict[str, Any]] = {}
    for p in packets:
        slug = slugify(p.get("goal"))
        card = by_slug.get(slug)
        if card is None:
            card = {
                "slug": slug,
                "label": None,
                "roots": set(),
                "digests": 0,
                "agents": set(),
                "latest": "",
                "_distill": None,
                "_reflection": None,
            }
            by_slug[slug] = card
        _fold(card, p)
    cards = [
        {
            "id": g["label"] or "(ungoaled)",
            "label": g["label"] or "(ungoaled)",
            "slug": g["slug"],
            "threads": len(g["roots"]),
            "digests": g["digests"],
            "agents": len(g["agents"]),
            "latest": g["latest"],
            "latest_distill_bundle": (g["_distill"] or {}).get("bundle"),
            "latest_reflection_structured": (g["_reflection"] or {}).get("structured"),
        }
        for g in by_slug.values()
        # Forum activity only: a goal with nothing but raw traces (e.g. the
        # "(ungoaled)" catch-all) is not a board — it would be an all-zeros row.
        # Matches the prior deriveGoalCards, which keyed off threads/distills.
        if g["roots"] or g["digests"]
    ]
    cards.sort(key=lambda c: c["latest"], reverse=True)
    return cards
