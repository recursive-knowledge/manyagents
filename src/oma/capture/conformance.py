"""Conformance validation — *the adapter author's contract, enforced here*.

oma.capture.md "Conformance is the adapter author's job — Settled": there is
**no** central PTY-parsing heuristic. The adapter maps its agent to
``CanonicalTrace`` and declares ``source_fidelity``; ``oma.capture`` validates
that mapping against the fixed schema and rejects non-conformant traces. This
runs *before* scrub/bound/persist so a malformed trace never reaches the Bank.
"""

from __future__ import annotations

from oma.capture.models import EVENT_KINDS, SOURCE_FIDELITIES, CanonicalTrace


class ConformanceError(ValueError):
    """An adapter emitted a trace that does not match the ``CanonicalTrace``
    contract (caught at PR time in practice; defended here at runtime)."""


def validate(trace: CanonicalTrace) -> CanonicalTrace:
    """Return ``trace`` unchanged iff it conforms; else raise
    :class:`ConformanceError` naming the first violation."""
    if not trace.session_id:
        raise ConformanceError("session_id is required")
    if "/" in trace.session_id:
        # The minted raw packet id is f"{session_id}/{uuid}"; a '/' in the
        # session_id would break Packet.session_id derivation (oma.core).
        raise ConformanceError(f"session_id must not contain '/': {trace.session_id!r}")
    if not trace.agent_id:
        raise ConformanceError("agent_id is required")
    if not trace.adapter:
        raise ConformanceError("adapter is required")
    if trace.source_fidelity not in SOURCE_FIDELITIES:
        raise ConformanceError(f"source_fidelity {trace.source_fidelity!r} not in {sorted(SOURCE_FIDELITIES)}")
    for i, ev in enumerate(trace.events):
        if ev.kind not in EVENT_KINDS:
            raise ConformanceError(f"events[{i}].kind {ev.kind!r} not in {sorted(EVENT_KINDS)}")
        if not isinstance(ev.text, str):
            raise ConformanceError(f"events[{i}].text must be str, got {type(ev.text).__name__}")
    return trace
