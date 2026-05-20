"""Secret scrub — *defense-in-depth* (oms.capture.md "Secret scrubbing").

The primary control is the access boundary: ``oms.bank`` migration ``00004``
keeps raw ``traces`` bodies out of the public-read role. This regex pass is the
**second** layer, run *before* persistence. ``SCRUB_VERSION`` is a deployed-code
version (not an ``OMS_*`` runtime knob): a re-scrub backfill compares each
``traces.scrub_version`` row against this constant and retro-quarantines newly
detected leaks (oms.capture.md "Operations & recovery").

Invariant: a :class:`~oms.capture.models.ScrubReport` carries **counts only,
never matched text** — the report is itself a leak surface (logs/observability).
"""

from __future__ import annotations

import re
from dataclasses import replace

from oms.capture.models import CanonicalTrace, ScrubReport, TraceEvent

# Bump when a pattern is added/changed; drives the re-scrub backfill seam.
SCRUB_VERSION = "v1"

# (kind, pattern, replacement). Order matters: the specific ``sk-ant-`` shape
# is redacted before the generic ``sk-`` one so its kind label is accurate and
# the generic pattern can never re-match the inserted ``[REDACTED:...]`` token
# (which contains no secret-shaped prefix).
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("anthropic", re.compile(r"sk-ant-(?:api\d\d-)?[A-Za-z0-9_-]{16,}"), "[REDACTED:anthropic]"),
    ("openai", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"), "[REDACTED:openai]"),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws_access_key]"),
    (
        "bearer",
        re.compile(r"(authorization\s*:\s*bearer\s+)(\S+)", re.IGNORECASE),
        r"\g<1>[REDACTED:bearer]",
    ),
    (
        "env_kv",
        re.compile(
            r"\b([A-Za-z][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|API)[A-Za-z0-9_]*[ \t]*=[ \t]*)(\S+)",
            re.IGNORECASE,
        ),
        r"\g<1>[REDACTED:env_kv]",
    ),
]


def scrub(trace: CanonicalTrace) -> tuple[CanonicalTrace, ScrubReport]:
    """Redact credential-shaped substrings from every event.

    Returns the scrubbed trace (with ``scrub_report`` stamped on it) and the
    same report. Pure: builds new events, never mutates the input.
    """
    report = ScrubReport()
    new_events: list[TraceEvent] = []
    for ev in trace.events:
        text = ev.text
        for kind, pat, repl in _PATTERNS:
            text, n = pat.subn(repl, text)
            if n:
                report.counts[kind] = report.counts.get(kind, 0) + n
        new_events.append(replace(ev, text=text))
    scrubbed = replace(trace, events=new_events, scrub_report=report)
    return scrubbed, report
