"""M13.1 tests for ``manyagent.adapters.miners`` — the Claude conversation miner.

Headlines: hook-tier binding reads exactly the bound transcript paths
(surviving ``/clear``'s multiple session files), the scan tier falls back to
the munged-cwd project dir filtered by the run window, parsing degrades
per-line (garbage never kills a segment), and every text field is scrubbed
before the artifact leaves the miner (it may go public as a rendition).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from manyagent.adapters.base import MineContext
from manyagent.adapters.miners.claude import MINER_VERSION, mine


def _entry(etype: str, content: Any, *, ts: str = "2026-06-10T17:52:00.000Z", sid: str = "hs-1") -> str:
    return json.dumps({"type": etype, "sessionId": sid, "timestamp": ts, "message": {"content": content}})


def _transcript(path: Path, *, sid: str = "hs-1") -> Path:
    """A realistic-shaped Claude Code transcript: user turn, assistant text +
    tool_use blocks, harness metadata entries, and one garbage line."""
    lines = [
        json.dumps({"type": "file-history-snapshot", "messageId": "x"}),  # metadata — skipped
        _entry("user", "What is 50-141", sid=sid),
        "{not json — torn line",  # never kills the segment
        _entry(
            "assistant",
            [
                {"type": "text", "text": "50 - 141 = **-91**"},
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "echo hi", "key": "sk-ant-api03-" + "A" * 24},
                },
            ],
            sid=sid,
        ),
        json.dumps({"type": "queue-operation", "op": "enqueue"}),  # metadata — skipped
        _entry("user", [{"type": "text", "text": "thanks"}, {"type": "tool_result", "content": "ignored"}], sid=sid),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ctx(tmp_path: Path, **over: Any) -> MineContext:
    base: dict[str, Any] = {"cwd": tmp_path / "proj", "window": (1000.0, 2000.0), "bindings": []}
    base.update(over)
    return MineContext(**base)


def test_hook_tier_mines_every_bound_transcript(tmp_path: Path) -> None:
    """Bindings name the exact files — including the second session a mid-run
    /clear rolled over to — and the artifact carries both segments."""
    t1 = _transcript(tmp_path / "one.jsonl", sid="hs-1")
    t2 = _transcript(tmp_path / "two.jsonl", sid="hs-2")
    ctx = _ctx(
        tmp_path,
        bindings=[
            {"transcript_path": str(t1), "harness_session_id": "hs-1", "ts": 1500.0},
            {"transcript_path": str(t1), "harness_session_id": "hs-1", "ts": 1600.0},  # SessionEnd dup
            {"transcript_path": str(t2), "harness_session_id": "hs-2", "ts": 1700.0},
        ],
    )
    art = mine(ctx)
    assert art is not None
    assert art["miner_version"] == MINER_VERSION
    assert art["binding"] == "hook"
    assert art["completeness"] == "full"
    assert art["run_started"] == 1000.0
    assert [s["harness_session_id"] for s in art["segments"]] == ["hs-1", "hs-2"]

    turns = art["segments"][0]["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant", "tool", "user"]
    assert turns[0]["text"] == "What is 50-141"
    assert turns[1]["text"] == "50 - 141 = **-91**"
    assert turns[2]["tool"]["name"] == "Bash"
    assert turns[0]["ts"] == "2026-06-10T17:52:00.000Z"


def test_text_fields_are_scrubbed(tmp_path: Path) -> None:
    """The tool input carried an sk-ant- key — it must not survive into the
    rendition (which may be public)."""
    t1 = _transcript(tmp_path / "one.jsonl")
    art = mine(_ctx(tmp_path, bindings=[{"transcript_path": str(t1)}]))
    assert art is not None
    body = json.dumps(art)
    assert "sk-ant-" not in body
    assert "[REDACTED:anthropic]" in body


def test_transcript_path_is_basename_only_no_local_layout_leak(tmp_path: Path) -> None:
    """The full on-disk path embeds the OS username + home tree; the public
    rendition must carry only the basename."""
    sub = tmp_path / "home" / "someuser" / ".claude"
    sub.mkdir(parents=True)
    t1 = _transcript(sub / "abc-123.jsonl")
    art = mine(_ctx(tmp_path, bindings=[{"transcript_path": str(t1)}]))
    assert art is not None
    seg = art["segments"][0]
    assert seg["transcript"] == "abc-123.jsonl"
    assert "transcript_path" not in seg
    assert "someuser" not in json.dumps(art)


def test_scrub_runs_before_cap_so_no_subfloor_credential_fragment(tmp_path: Path) -> None:
    """A credential straddling the per-turn cap must be redacted, not
    truncated to a sub-floor fragment the regex misses (cap-before-scrub
    would leak the prefix)."""
    from manyagent.adapters.miners.claude import _TURN_TEXT_CAP

    key = "sk-ant-api03-" + "Z" * 40
    text = "A" * (_TURN_TEXT_CAP - 5) + key  # the key straddles the cap boundary
    path = tmp_path / "one.jsonl"
    path.write_text(_entry("user", text) + "\n", encoding="utf-8")
    art = mine(_ctx(tmp_path, bindings=[{"transcript_path": str(path)}]))
    assert art is not None
    body = json.dumps(art)
    assert "sk-ant-api03-Z" not in body  # no surviving key fragment (the load-bearing property)
    assert "[REDA" in body  # the redaction landed (the marker itself may be cap-truncated)


def test_artifact_is_size_bounded(tmp_path: Path) -> None:
    """A huge run can't store an unbounded body: over budget → trailing turns
    drop and completeness downgrades (mirrors the raw-trace bound)."""
    from manyagent.adapters.miners.claude import _MAX_ARTIFACT_BYTES

    big = "x" * 3000
    lines = [_entry("user", big) for _ in range(800)]  # ~2.4 MB pre-cap
    path = tmp_path / "one.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    art = mine(_ctx(tmp_path, bindings=[{"transcript_path": str(path)}]))
    assert art is not None
    assert len(json.dumps(art, ensure_ascii=False).encode()) <= _MAX_ARTIFACT_BYTES
    assert art["completeness"] == "partial"


def test_scan_tier_uses_munged_cwd_and_run_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No bindings (hooks declined): scan ~/.claude/projects/<munged-cwd>/
    for transcripts touched during the run window; outside-window files are
    not part of this run."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # type: ignore[arg-type]
    cwd = tmp_path / "code" / "My-Proj"
    munged = str(cwd).replace("/", "-")
    d = tmp_path / ".claude" / "projects" / munged
    d.mkdir(parents=True)
    now = time.time()
    in_window = _transcript(d / "in.jsonl", sid="hs-in")
    stale = _transcript(d / "stale.jsonl", sid="hs-stale")
    os.utime(in_window, (now, now))
    os.utime(stale, (now - 7200, now - 7200))

    art = mine(MineContext(cwd=cwd, window=(now - 60, now + 60), bindings=[]))
    assert art is not None
    assert art["binding"] == "scan"
    assert [s["harness_session_id"] for s in art["segments"]] == ["hs-in"]


def test_returns_none_when_nothing_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # type: ignore[arg-type]
    assert mine(_ctx(tmp_path)) is None  # no bindings, no project dir
    # A bound path that no longer exists → partial would need ≥1 parsed
    # segment; with nothing parseable the miner returns None outright.
    assert mine(_ctx(tmp_path, bindings=[{"transcript_path": str(tmp_path / "gone.jsonl")}])) is None


def test_missing_file_among_bound_marks_partial(tmp_path: Path) -> None:
    t1 = _transcript(tmp_path / "one.jsonl")
    ctx = _ctx(
        tmp_path,
        bindings=[
            {"transcript_path": str(t1)},
            {"transcript_path": str(tmp_path / "vanished.jsonl")},
        ],
    )
    art = mine(ctx)
    assert art is not None
    assert art["completeness"] == "partial"
    assert len(art["segments"]) == 1


def test_claude_adapter_delegates_to_miner(tmp_path: Path) -> None:
    from manyagent.adapters.builtin.claude import ClaudeAdapter

    t1 = _transcript(tmp_path / "one.jsonl")
    adapter = ClaudeAdapter(session_id="S1", agent_id="S1/agent-001-claude")
    art = adapter.mine(_ctx(tmp_path, bindings=[{"transcript_path": str(t1)}]))
    assert art is not None and art["miner_version"] == MINER_VERSION
