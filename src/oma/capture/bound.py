"""Size bounding — *the remaining genuinely hard problem* (oma.capture.md
"Trace size vs. context window — Open (highest-risk)").

A long session exceeds any local model's context, so a budget
(``OMA_TRACE_MAX_BYTES``) is enforced *here*, not in ``oma.distill`` (which
stays a pure summarizer). v1 is a faithful head+tail reduction — chunk on turn
boundaries for ``structured``, on byte windows for ``pty`` — with
``truncated=True`` on every reduced/synthesized event and a single explicit
``[... elided ...]`` marker so the gap is visible, not silent. A smarter
map-reduce is the Open follow-up; the seam (this module owns it) is what
matters now.
"""

from __future__ import annotations

from dataclasses import replace

from oma.capture.models import CanonicalTrace, TraceEvent
from oma.utils import config

_MARKER_RESERVE = 256  # bytes kept aside so the elision marker itself fits the budget


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _byte_size(events: list[TraceEvent]) -> int:
    return sum(_nbytes(e.text) for e in events)


def _truncate_text(text: str, nbytes: int) -> str:
    """Cut ``text`` to at most ``nbytes`` UTF-8 bytes without splitting a
    codepoint."""
    if nbytes <= 0:
        return ""
    return text.encode("utf-8")[:nbytes].decode("utf-8", "ignore")


def _marker(ts: float, elided_units: int, elided_bytes: int, budget: int, unit: str) -> TraceEvent:
    return TraceEvent(
        ts=ts,
        kind="system",
        text=f"[... {elided_units} {unit} / {elided_bytes} bytes elided for size budget {budget} ...]",
        truncated=True,
    )


def _bound_structured(events: list[TraceEvent], budget: int) -> list[TraceEvent]:
    """Keep whole events from the head and tail; replace the middle with one
    truncated marker. A boundary event larger than its share is byte-truncated."""
    half = max(1, (budget - _MARKER_RESERVE) // 2)

    head: list[TraceEvent] = []
    acc = 0
    for e in events:
        b = _nbytes(e.text)
        if head and acc + b > half:
            break
        head.append(e if acc + b <= half else replace(e, text=_truncate_text(e.text, half), truncated=True))
        acc += b
        if acc >= half:
            break

    tail: list[TraceEvent] = []
    acc = 0
    # Iterate only events *after* head: once head truncated the first event in
    # place, the tail must not re-pick it (the slice is load-bearing — keep it).
    for e in reversed(events[len(head) :]):
        b = _nbytes(e.text)
        if tail and acc + b > half:
            break
        tail.append(e if acc + b <= half else replace(e, text=_truncate_text(e.text, half), truncated=True))
        acc += b
        if acc >= half:
            break
    tail.reverse()

    elided = events[len(head) : len(events) - len(tail)]
    if not elided:  # head + tail already cover everything — no gap to mark
        return [*head, *tail]
    pivot_ts = elided[0].ts
    marker = _marker(pivot_ts, len(elided), _byte_size(elided), budget, "events")
    return [*head, marker, *tail]


def _bound_pty(events: list[TraceEvent], budget: int) -> list[TraceEvent]:
    """Flatten the PTY tee and keep a head + tail byte window."""
    raw = "".join(e.text for e in events).encode("utf-8")
    half = max(1, (budget - _MARKER_RESERVE) // 2)
    head_txt = raw[:half].decode("utf-8", "ignore")
    tail_txt = raw[-half:].decode("utf-8", "ignore")
    first_ts = events[0].ts if events else 0.0
    last_ts = events[-1].ts if events else 0.0
    elided_bytes = len(raw) - _nbytes(head_txt) - _nbytes(tail_txt)
    return [
        TraceEvent(ts=first_ts, kind="system", text=head_txt, truncated=True),
        _marker(first_ts, max(elided_bytes, 1), elided_bytes, budget, "bytes"),
        TraceEvent(ts=last_ts, kind="system", text=tail_txt, truncated=True),
    ]


def bound(trace: CanonicalTrace, *, max_bytes: int | None = None) -> CanonicalTrace:
    """Enforce the byte budget. Sets ``bytes_out``; preserves adapter-set
    ``bytes_in``. A trace already within budget is returned unchanged (only
    ``bytes_out`` stamped)."""
    budget = max_bytes if max_bytes is not None else config.OMA_TRACE_MAX_BYTES
    size = _byte_size(trace.events)
    if size <= budget:
        return replace(trace, bytes_out=size)
    events = (
        _bound_pty(trace.events, budget) if trace.source_fidelity == "pty" else _bound_structured(trace.events, budget)
    )
    return replace(trace, events=events, bytes_out=_byte_size(events))
