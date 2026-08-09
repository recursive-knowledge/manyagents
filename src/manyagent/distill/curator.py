"""The ``/cross-distill`` curator state machine (manyagent.distill.md "State
machine"). Idempotent and resumable: a killed run writes nothing partial and
re-runs reproducibly from the stored posts.

```
1. posts = cluster(goal, window, scope)        # per-goal or cross-goal
   → zero posts under scope ⇒ abort "Run /self-distill first!"
2. fresh distill already covers exactly these posts ⇒ return it (no spend)
3. curator = resolve(MANYAGENT_CURATOR_MODE)         # local | server | auto
4. raw = curator(stable system, rendered posts)  # cache-split prompt
5. bundle = validate_bundle(raw, posts)        # mechanical drop/cap
6. put distill Packet(scope, goal, parents, curator=mode); return it
```

**No carry-forward / independence (swarms V2, ``distiller.py:56-63,92-94``):**
``cluster()`` lists only ``type="post"`` — a ``distill`` packet is never an
input, so a bundle never feeds back. ``per_goal`` input is one goal's posts;
``cross_goal`` input is the whole corpus; the two sets are structurally
independent (no cross-leak), and ``include_quarantined=False`` everywhere.

**Idempotency-key refinement (M7 build decision; logged on manyagent.distill.md):**
the deterministic packet id is keyed on ``scope + goal + sorted(parent_ids)``
**only — not the curator mode**. Step 2 ("covers exactly these posts → no
spend") and resumability ("re-runs reproducibly") must hold even when an
``auto`` run fell back server→local between attempts; folding the mode into
the key would re-spend on every fallback. Provenance still lands on the
packet's ``curator`` field (the concrete executor), just not in its identity.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from typing import Any

from manyagent.bank import Bank
from manyagent.core import Packet
from manyagent.distill.parse import validate_bundle
from manyagent.distill.prompts import build_distill_prompt
from manyagent.distill.resolve import Curator, resolve
from manyagent.distill.schema import BUCKETS
from manyagent.distill.weighting import weigh_posts
from manyagent.utils import config
from manyagent.utils.slug import normalize_goal

_SCOPES = {"per_goal", "cross_goal"}


class NoPostsError(RuntimeError):
    """No posts under the requested scope. The message is the exact sentinel
    the CLI surfaces (manyagent.distill.md Verification: ``"Run /self-distill
    first!"``)."""

    def __init__(self) -> None:
        super().__init__("Run /self-distill first!")


class CurationError(RuntimeError):
    """The curator LLM returned output no JSON object could be recovered from.
    Nothing is persisted — the run stays resumable (re-running re-spends; an
    all-dropped *parseable* bundle is, by contrast, a valid empty result)."""


def _since(window_days: int) -> str:
    cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=window_days)
    return cutoff.isoformat()


def _packet_id(scope: str, goal: str | None, parent_ids: list[str]) -> str:
    key = "\x1f".join([scope, goal or "", *parent_ids])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"curator/{digest}"


_THINK_BLOCK_RE = re.compile(r"<(think|thinking|reasoning)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _end_of_string(text: str, i: int) -> int:
    """Index just past the JSON string literal that opens at ``text[i]``.

    A backslash escapes the next character, so ``"a\\"b"`` is one string and
    the inner quote does not end it.
    """
    i += 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return i  # unterminated string: consume the rest


def _balanced_object_spans(text: str) -> list[tuple[int, int]]:
    """Every balanced ``{...}`` span in ``text``, outermost first.

    Braces inside a string literal (``"see {here}"``) are skipped, so they
    never open or close a span.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i = _end_of_string(text, i)
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
        i += 1
    return spans


def _extract_json(raw: str) -> Any | None:
    """Recover the bundle object from the model's output. ``None`` ⇒
    unrecoverable (CurationError).

    Real model output is messier than "JSON, maybe fenced". A reasoning model
    emits a chain of thought *before* the bundle, and that prose routinely
    contains braces; a chatty model appends "hope that helps {...}" *after* it.
    The old first-``{``-to-last-``}`` slice spanned those, so a response that
    plainly contained a valid bundle decoded to nothing and the whole curation
    raised. Both shapes are measured, not hypothetical — see
    ``tests/test_distill.py`` for the captured cases.

    So: strip reasoning blocks, then scan for *balanced* object spans and take
    the best one. "Best" prefers a span that actually looks like a bundle (it
    carries a known bucket key) over an incidental object from the prose.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # A reasoning trace is never the answer; drop it before looking for braces.
    stripped = _THINK_BLOCK_RE.sub("", raw)
    # An unterminated <think> (truncated generation) leaves the opener behind.
    for opener in ("</think>", "</thinking>", "</reasoning>"):
        idx = stripped.rfind(opener)
        if idx != -1:
            stripped = stripped[idx + len(opener) :]

    candidates: list[Any] = []
    for start, end in _balanced_object_spans(stripped):
        try:
            parsed = json.loads(stripped[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    if not candidates:
        return None

    # Prefer a real bundle over an incidental object mentioned in the prose;
    # among equals prefer the richest (most buckets present).
    def _rank(obj: dict[str, Any]) -> tuple[int, int]:
        buckets = sum(1 for b in BUCKETS if b in obj)
        return (1 if buckets else 0, buckets)

    return max(candidates, key=_rank)


async def _cluster(
    *,
    scope: str,
    goal: str | None,
    bank: Bank,
    window_days: int,
) -> list[dict[str, Any]]:
    since = _since(window_days)
    if scope == "per_goal":
        if goal is None:
            raise ValueError("per_goal distill requires a goal (it scopes exactly one goal's posts)")
        return await bank.list_packets(type="post", goal=goal, since=since, include_quarantined=False)
    # cross_goal: the whole corpus within the window, any goal.
    return await bank.list_packets(type="post", since=since, include_quarantined=False)


async def curate(
    *,
    scope: str,
    goal: str | None = None,
    bank: Bank,
    model: Any | None = None,
    mode: str | None = None,
    server_url: str | None = None,
    window_days: int | None = None,
) -> Packet:
    """Curate goal-scoped posts into a ``distill`` packet. Raises
    :class:`NoPostsError` (exact sentinel) when the scope is empty,
    :class:`CurationError` when the LLM output is unrecoverable."""
    if scope not in _SCOPES:
        raise ValueError(f"bad scope {scope!r}; expected one of {sorted(_SCOPES)}")
    win = window_days if window_days is not None else config.MANYAGENT_CROSSDISTILL_WINDOW_DAYS
    # Compare/aggregate on the canonical slug — a raw goal here must match the
    # normalized form stored on write (decision #1) so case/spacing variants
    # cluster into one bucket instead of fragmenting.
    goal = normalize_goal(goal)
    stored_goal = goal if scope == "per_goal" else None

    posts = await _cluster(scope=scope, goal=goal, bank=bank, window_days=win)
    if not posts:
        raise NoPostsError()

    parent_ids = sorted(str(p["id"]) for p in posts)
    pid = _packet_id(scope, stored_goal, parent_ids)

    # Step 2 — idempotency: pid encodes exactly scope+goal+these posts, so an
    # existing packet *is* a fresh distill over exactly this input. A
    # directly-quarantined distill is still returned (flag intact): the input
    # is unchanged so re-curation would land the SAME content-addressed id,
    # and silently overwriting it violates manyagent.bank.md:92 (append-only); the
    # consumer decides exclusion (manyagent.core.md: quarantine is non-hiding;
    # /inject's Settled human gate is the protection layer). Retro-quarantine
    # of a *parent post* is instead the path to a fresh curation — the
    # excluded post changes the parent set ⇒ a different pid (manyagent.bank.md
    # :86/:100), handled automatically by _cluster(include_quarantined=False).
    existing = await bank.get_packet(pid)
    if existing is not None and existing.get("type") == "distill" and existing.get("bundle") is not None:
        return Packet(**existing)

    curator: Curator = resolve(mode, model=model, server_url=server_url)
    weighted = await weigh_posts(posts, bank=bank)
    system, user = build_distill_prompt(posts=weighted, scope=scope, goal=stored_goal)

    try:
        raw = await curator.complete(system, user)
    except ValueError as exc:
        raise CurationError(f"curator model returned an unparseable response: {exc}") from exc
    payload = _extract_json(raw)
    if payload is None:
        raise CurationError("curator returned no recoverable JSON bundle (nothing persisted; run is resumable)")

    bundle = validate_bundle(payload, posts=posts)

    record: dict[str, Any] = {
        "id": pid,
        "session_id": "curator",
        "type": "distill",
        "agent_id": "curator",
        "goal": stored_goal,
        "scope": scope,
        "bundle": bundle,
        "parents": parent_ids,
        "curator": getattr(curator, "mode", "local"),
    }
    # packets.session_id is NOT NULL + FK → sessions(id); distill packets live
    # under the synthetic "curator" session (id convention curator/<digest>).
    await bank.put_session("curator")
    await bank.put_packet(record)
    return await Packet.fetch(pid, bank=bank, force=True)
