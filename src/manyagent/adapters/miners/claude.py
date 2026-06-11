"""Claude Code conversation miner (M13.1).

Source files: ``~/.claude/projects/<munged-cwd>/<session-id>.jsonl`` — one
JSON object per line; the entry types we consume are ``user`` (message
content: string, or a block list whose ``text`` blocks we join) and
``assistant`` (content block list: ``text`` blocks become assistant turns,
``tool_use`` blocks become tool turns with a capped input preview). Every
other entry type (``file-history-snapshot``, ``queue-operation``, ``mode``,
``ai-title``, …) is harness metadata and skipped. The format is undocumented
and drifts — every parse step degrades per-line/per-file, never per-run.

Binding tiers (Trace Renditions & Mining §4a): the ``manyagent._hook`` records
name the exact transcript paths (``binding: "hook"`` — survives ``/clear``
rolling new session files mid-run); with no bindings we fall back to scanning
the munged-cwd project dir for files touched during the run window
(``binding: "scan"``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from manyagent.adapters.base import MineContext

MINER_VERSION = "claude-v1"

# Per-turn text cap: the rendition is a conversation view, not an archive —
# the byte-exact record is the raw trace. Tool input previews are shorter.
# Capping happens AFTER scrub (a credential straddling the cap would otherwise
# leave a sub-floor fragment the regex misses — the rendition is public).
_TURN_TEXT_CAP = 4000
_TOOL_PREVIEW_CAP = 400
_MAX_TURNS_PER_SEGMENT = 2000
# Total artifact ceiling (mirrors manyagent.capture's MANYAGENT_TRACE_MAX_BYTES on the raw
# path): a long run with many /clear segments, or a wide scan sweep, must not
# store an unbounded body. Over budget → trailing turns/segments dropped and
# completeness downgraded.
_MAX_ARTIFACT_BYTES = 1_000_000
# mtime slack for the scan fallback. Small on purpose: mining runs at the
# child's exit, so a transcript still being written well after belongs to a
# DIFFERENT session — a wide window would mine concurrent sessions in the same
# cwd into this run's (public) rendition. The hook tier binds exact paths and
# is immune; the scan tier is a best-effort fallback and says so (binding).
_WINDOW_SLACK_S = 15.0


def _projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _munged_candidates(cwd: Path) -> list[str]:
    """Claude Code munges the cwd into a project-dir name. The exact rule is
    undocumented; try the observed form (path separators → '-') first, then
    a defensive everything-non-alphanumeric variant. Deduped, order kept."""
    s = str(cwd)
    return list(dict.fromkeys([s.replace("/", "-"), re.sub(r"[^A-Za-z0-9-]", "-", s)]))


def _bound_paths(ctx: MineContext) -> list[Path]:
    out: list[Path] = []
    for rec in ctx.bindings:
        p = rec.get("transcript_path")
        if isinstance(p, str) and p:
            path = Path(p)
            if path not in out:
                out.append(path)
    return out


def _scan_paths(ctx: MineContext) -> list[Path]:
    lo, hi = ctx.window
    out: list[Path] = []
    for cand in _munged_candidates(ctx.cwd):
        d = _projects_root() / cand
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.jsonl")):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if lo - _WINDOW_SLACK_S <= mtime <= hi + _WINDOW_SLACK_S and p not in out:
                out.append(p)
    return out


def _cap(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + f" … [truncated {len(text) - limit} chars]"
    return text


def _joined_text(content: Any) -> str:
    """A message's human text: the string itself, or its ``text`` blocks
    joined (tool_result and other block types are not conversation text)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _parse_transcript(path: Path) -> dict[str, Any] | None:  # noqa: C901 — one linear per-entry dispatch; splitting scatters the defensive parse
    """One transcript file → one segment, or None when unreadable/empty.
    Text fields are stored UNCAPPED here — :func:`_scrub_then_cap` scrubs the
    full strings then caps, so truncation can't strand a sub-floor credential
    fragment past the scrub. ``transcript`` is the file basename only: the
    full path embeds the OS username + home layout and this artifact is
    public (the parent-dir munge is the same for every run anyway)."""
    turns: list[dict[str, Any]] = []
    harness_sid: str | None = None
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue  # torn/garbled line — keep going
                if not isinstance(entry, dict):
                    continue
                sid = entry.get("sessionId")
                if harness_sid is None and isinstance(sid, str) and sid:
                    harness_sid = sid
                etype = entry.get("type")
                ts = entry.get("timestamp")
                raw_message = entry.get("message")
                message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
                if etype == "user":
                    text = _joined_text(message.get("content"))
                    if text.strip():
                        turns.append({"role": "user", "ts": ts, "text": text})
                elif etype == "assistant":
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and str(block.get("text") or "").strip():
                            turns.append({"role": "assistant", "ts": ts, "text": str(block["text"])})
                        elif block.get("type") == "tool_use":
                            preview = json.dumps(block.get("input") or {}, ensure_ascii=False)
                            turns.append({
                                "role": "tool",
                                "ts": ts,
                                "text": "",
                                "tool": {"name": str(block.get("name") or "?"), "input_preview": preview},
                            })
    except OSError:
        return None
    if not turns:
        return None
    return {
        "harness_session_id": harness_sid or path.stem,
        "transcript": path.name,  # basename only — see docstring
        "turns": turns[:_MAX_TURNS_PER_SEGMENT],
    }


def _scrub_then_cap(artifact: dict[str, Any]) -> None:
    """Scrub every text field, THEN cap it — in that order, so a credential
    spanning a cap boundary is redacted before truncation could hide its
    tail. Per-string (one probe event per field) rather than over the
    serialized body: an env-kv style redaction spanning JSON structure would
    corrupt the document."""
    from manyagent.capture.models import CanonicalTrace, TraceEvent
    from manyagent.capture.scrub import scrub

    slots: list[tuple[dict[str, Any], str, int]] = []
    for seg in artifact["segments"]:
        for turn in seg["turns"]:
            if turn.get("text"):
                slots.append((turn, "text", _TURN_TEXT_CAP))
            tool = turn.get("tool")
            if tool and tool.get("input_preview"):
                slots.append((tool, "input_preview", _TOOL_PREVIEW_CAP))
    if not slots:
        return
    probe = CanonicalTrace(
        session_id="probe",
        agent_id="probe/a",
        adapter="probe",
        events=[TraceEvent(ts=float(i), kind="system", text=obj[key]) for i, (obj, key, _cap_n) in enumerate(slots)],
        source_fidelity="structured",
    )
    scrubbed, _report = scrub(probe)
    for (obj, key, cap_n), ev in zip(slots, scrubbed.events, strict=True):
        obj[key] = _cap(ev.text, cap_n)


def _bound_artifact(artifact: dict[str, Any]) -> None:
    """Drop trailing turns (then trailing segments) until the serialized body
    fits ``_MAX_ARTIFACT_BYTES``; downgrade ``completeness`` if anything went.
    Mirrors manyagent.capture's size-bound on the raw path so a rendition can never
    out-grow the trace it derives from."""

    def size() -> int:
        return len(json.dumps(artifact, ensure_ascii=False).encode())

    if size() <= _MAX_ARTIFACT_BYTES:
        return
    artifact["completeness"] = "partial"
    segs = artifact["segments"]
    while segs and size() > _MAX_ARTIFACT_BYTES:
        if segs[-1]["turns"]:
            segs[-1]["turns"].pop()
        else:
            segs.pop()


def mine(ctx: MineContext) -> dict[str, Any] | None:
    """The ``Adapter.mine`` delegate for Claude Code. Returns the normalized
    conversation artifact, or None when no transcript was found/parseable."""
    paths = _bound_paths(ctx)
    binding = "hook"
    if not paths:
        paths = _scan_paths(ctx)
        binding = "scan"
    if not paths:
        return None
    segments: list[dict[str, Any]] = []
    failed = 0
    for p in paths:
        seg = _parse_transcript(p)
        if seg is None:
            failed += 1
        else:
            segments.append(seg)
    if not segments:
        return None
    artifact: dict[str, Any] = {
        "miner_version": MINER_VERSION,
        "binding": binding,
        "completeness": "full" if failed == 0 else "partial",
        "run_started": ctx.window[0],
        "segments": segments,
    }
    _scrub_then_cap(artifact)
    _bound_artifact(artifact)
    return artifact
