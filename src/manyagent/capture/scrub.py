"""Secret scrub — *defense-in-depth* (manyagent.capture.md "Secret scrubbing").

The primary control is the access boundary: ``manyagent.bank`` migration ``00004``
keeps raw ``traces`` bodies out of the public-read role. This regex pass is the
**second** layer, run *before* persistence. ``SCRUB_VERSION`` is a deployed-code
version (not an ``MANYAGENT_*`` runtime knob): a re-scrub backfill compares each
``traces.scrub_version`` row against this constant and retro-quarantines newly
detected leaks (manyagent.capture.md "Operations & recovery").

Invariant: a :class:`~manyagent.capture.models.ScrubReport` carries **counts only,
never matched text** — the report is itself a leak surface (logs/observability).
"""

from __future__ import annotations

import re
from dataclasses import replace

from manyagent.capture.models import CanonicalTrace, ScrubReport, TraceEvent

# Bump when a pattern is added/changed; drives the re-scrub backfill seam.
# v2 (2026-06-22): added github / google_api / slack / jwt provider shapes and a
# JSON ``"KEY": "value"`` env_kv form — the corpus is now public-by-default
# (open-corpus decision), so this pass is the primary guard, not a backstop.
SCRUB_VERSION = "v2"

# (kind, pattern, replacement). Order matters on two counts:
#  - the specific ``sk-ant-`` shape is redacted before the generic ``sk-`` one,
#    so its kind label is accurate and ``openai`` never re-labels an Anthropic key;
#  - every replacement is a ``[REDACTED:...]`` token carrying no secret-shaped
#    prefix, so no later pattern can re-match an already-redacted span.
# Conservative by design: we do NOT add a bare high-entropy / 40-char-base64
# catch-all (e.g. for an unlabelled AWS *secret* key) — it mangles legitimate
# trace content (hashes, base64 payloads). Labelled secrets are caught via env_kv.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("anthropic", re.compile(r"sk-ant-(?:api\d\d-)?[A-Za-z0-9_-]{16,}"), "[REDACTED:anthropic]"),
    ("openai", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"), "[REDACTED:openai]"),
    # GitHub: classic PATs/tokens (gh[poursa]_ + 36) and fine-grained (github_pat_…).
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{59,})\b"), "[REDACTED:github]"),
    # Google API keys: literal ``AIza`` + 35 url-safe chars.
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "[REDACTED:google_api]"),
    # Slack tokens: xoxb / xoxa / xoxp / xoxr / xoxs.
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED:slack]"),
    # Raw JWTs (``eyJ`` = base64 of ``{"``): three url-safe base64 segments.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"), "[REDACTED:jwt]"),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws_access_key]"),
    (
        "bearer",
        re.compile(r"(authorization\s*:\s*bearer\s+)(\S+)", re.IGNORECASE),
        r"\g<1>[REDACTED:bearer]",
    ),
    # env_kv, shell form: ``FOO_API_KEY=value``.
    (
        "env_kv",
        re.compile(
            r"\b([A-Za-z][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|API)[A-Za-z0-9_]*[ \t]*=[ \t]*)(\S+)",
            re.IGNORECASE,
        ),
        r"\g<1>[REDACTED:env_kv]",
    ),
    # env_kv, JSON form: ``"FOO_API_KEY": "value"`` (structured tool output, jq, json.dumps).
    (
        "env_kv",
        re.compile(
            r'"([A-Za-z][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|API)[A-Za-z0-9_]*)"[ \t]*:[ \t]*"([^"]+)"',
            re.IGNORECASE,
        ),
        r'"\g<1>": "[REDACTED:env_kv]"',
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
