"""oma.capture — raw agent session → normalized, bounded, scrubbed, replayable
``raw`` Packet (M4; oma.capture.md).

*The invisible prerequisite* (Design Principles §2): ``oma.distill`` cannot
produce useful knowledge unless the trace it summarizes is faithful, bounded,
scrubbed, and replayable. The adapter author conforms to the
``CanonicalTrace`` contract; this module's load-bearing jobs are exactly
**validate → scrub → bound → persist**.

The pipeline order is deliberate: scrub *before* bound costs nothing in v1 and
forecloses any future LLM-assisted bounder ever seeing a raw secret.
Persistence is two Bank calls (``put_packet`` then ``put_trace``); a crash
between them leaves a ``raw`` packet with no body — observable
(``get_trace → None``) and recoverable, not silent corruption. A transactional
wrapper is intentionally deferred (v1).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from oma.bank import Bank, get_bank
from oma.capture.bound import bound
from oma.capture.conformance import ConformanceError, validate
from oma.capture.models import CanonicalTrace, ScrubReport, TraceEvent
from oma.capture.scrub import SCRUB_VERSION, scrub

__all__ = [
    "SCRUB_VERSION",
    "CanonicalTrace",
    "ConformanceError",
    "ScrubReport",
    "TraceEvent",
    "bound",
    "persist",
    "scrub",
    "validate",
]


def _serialize(trace: CanonicalTrace) -> str:
    """The replayable raw body: the full post-scrub, post-bound trace as JSON
    (provenance + audit; re-running distillation reads this back)."""
    return json.dumps(
        {
            "session_id": trace.session_id,
            "agent_id": trace.agent_id,
            "adapter": trace.adapter,
            "source_fidelity": trace.source_fidelity,
            "bytes_in": trace.bytes_in,
            "bytes_out": trace.bytes_out,
            "scrub_report": {"counts": trace.scrub_report.counts},
            "events": [asdict(e) for e in trace.events],
        },
        ensure_ascii=False,
    )


async def persist(trace: CanonicalTrace, *, bank: Bank | None = None, complete: bool = True) -> str:
    """Run the full capture pipeline and persist as a ``raw`` Packet (1:1 with
    a ``traces`` row in ``oma.bank``).

    ``complete`` records whether *capture itself* finished (a session can be
    killed mid-stream — oma.capture.md "Operations & recovery"); it is distinct
    from size *truncation*, which is recorded per-event via ``truncated`` and
    via ``bytes_in``/``bytes_out``. Returns the minted ``raw`` packet id.
    """
    validate(trace)
    scrubbed, _report = scrub(trace)
    bounded = bound(scrubbed)

    b = bank or get_bank()
    packet_id = f"{bounded.session_id}/{uuid.uuid4().hex[:8]}"
    await b.put_packet({
        "id": packet_id,
        "type": "raw",
        "session_id": bounded.session_id,
        "agent_id": bounded.agent_id,
    })
    await b.put_trace(packet_id, _serialize(bounded), scrub_version=SCRUB_VERSION, complete=complete)
    return packet_id
