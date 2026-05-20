"""The ``CanonicalTrace`` contract (oma.capture.md "CanonicalTrace").

Plain ``@dataclass`` value objects — the design doc shows them literally as
dataclasses, *not* the M3 frozen-Pydantic ``Packet`` pattern: this is the
adapter author's conformance target, validated (not coerced) by
``oma.capture``. Pipeline stages treat them as effectively-immutable and
rebuild via :func:`dataclasses.replace` rather than mutating in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The five event kinds an adapter may emit (oma.capture.md "CanonicalTrace").
EVENT_KINDS = frozenset({"user", "agent", "tool_call", "tool_result", "system"})
# Adapter-declared fidelity: native structured logs vs. a raw PTY tee.
SOURCE_FIDELITIES = frozenset({"structured", "pty"})


@dataclass
class TraceEvent:
    """One normalized step. ``text`` is the post-scrub invariant: by the time a
    trace is persisted no ``TraceEvent.text`` carries a secret."""

    ts: float  # monotonic offset (s) from session start
    kind: str  # one of EVENT_KINDS
    text: str  # already scrubbed before persistence
    truncated: bool = False  # set when this event was size-reduced by bounding


@dataclass
class ScrubReport:
    """What the scrub pass removed — **counts/kinds only, never the secret**.

    The report itself is a leak surface (it ends up in logs/observability), so
    nothing here is ever populated with matched text."""

    counts: dict[str, int] = field(default_factory=dict)  # kind -> redaction count

    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class CanonicalTrace:
    """A heterogeneous agent session, normalized. ``source_fidelity`` is
    first-class: ``oma.distill`` must degrade gracefully on ``"pty"`` and never
    assume tool-call structure exists (oma.capture.md)."""

    session_id: str
    agent_id: str
    adapter: str  # "claude" | "codex" | "gemini" | ...
    events: list[TraceEvent]
    source_fidelity: str  # "structured" | "pty"
    scrub_report: ScrubReport = field(default_factory=ScrubReport)
    bytes_in: int = 0
    bytes_out: int = 0  # set by bounding (== bytes_in when nothing was reduced)
