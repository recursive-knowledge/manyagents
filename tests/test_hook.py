"""Tests for ``oms._hook`` — the harness lifecycle hook sink (M12 groundwork).

The sink is installed user-scope, so it fires for the user's *everyday*
harness sessions too: the headline invariants are (1) without
``OMS_SESSION`` it writes nothing and exits 0, (2) for wrapped runs it
appends one JSONL binding record per hook event under
``$OMS_HOME/bindings/``, and (3) it never raises and never exits nonzero —
a misbehaving hook can disturb the host session.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from oms import _hook


@pytest.fixture
def oms_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".oms"
    monkeypatch.setenv("OMS_HOME", str(home))
    return home


def _payload(**over: object) -> str:
    base: dict[str, object] = {
        "hook_event_name": "SessionStart",
        "session_id": "1e772edd-fe86-4d07-98b0-b785bb950264",
        "transcript_path": "/home/u/.claude/projects/-home-u-proj/1e772edd.jsonl",
        "cwd": "/home/u/proj",
    }
    base.update(over)
    return json.dumps(base)


def test_no_oms_session_writes_nothing(oms_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMS_SESSION", raising=False)
    assert _hook.main(io.StringIO(_payload())) == 0
    assert not (oms_home / "bindings").exists()


def test_appends_one_record_per_event(oms_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One wrapped PTY run can span several harness sessions (`/clear` rolls
    a fresh id) — the sink appends, never overwrites."""
    monkeypatch.setenv("OMS_SESSION", "trial")
    assert _hook.main(io.StringIO(_payload())) == 0
    assert _hook.main(io.StringIO(_payload(hook_event_name="SessionEnd", reason="clear"))) == 0
    lines = (oms_home / "bindings" / "trial.jsonl").read_text().splitlines()
    assert len(lines) == 2
    start, end = (json.loads(line) for line in lines)
    assert start["oms_session"] == "trial"
    assert start["event"] == "SessionStart"
    assert start["harness_session_id"] == "1e772edd-fe86-4d07-98b0-b785bb950264"
    assert start["transcript_path"].endswith("1e772edd.jsonl")
    assert start["cwd"] == "/home/u/proj"
    assert end["event"] == "SessionEnd"
    assert end["reason"] == "clear"
    assert isinstance(end["ts"], float)


def test_garbage_stdin_is_swallowed(oms_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_SESSION", "trial")
    assert _hook.main(io.StringIO("not json {")) == 0
    assert not (oms_home / "bindings").exists()


def test_non_object_payload_ignored(oms_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_SESSION", "trial")
    assert _hook.main(io.StringIO('["a", "list"]')) == 0
    assert not (oms_home / "bindings").exists()


def test_slash_in_session_id_refused(oms_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A '/' would escape the bindings dir (and violates the Bank's
    no-slash session-id constraint) — refuse to write anything."""
    monkeypatch.setenv("OMS_SESSION", "evil/../escape")
    assert _hook.main(io.StringIO(_payload())) == 0
    assert not (oms_home / "bindings").exists()


# --------------------------------------------------------------------------- #
# SessionStart additionalContext delivery (start-time inject offer, 2026-06-10)
# --------------------------------------------------------------------------- #


def _stash_injection(home: Path, sid: str) -> None:
    d = home / "inject"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(
        json.dumps({
            "packet_id": "curator/feedme01",
            "goal": "speed",
            "bundle": {"confirmed_constraints": ["`rtol=1e-10` before CFL tuning"]},
        }),
        encoding="utf-8",
    )


def test_session_start_delivers_stashed_injection(
    oms_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OMS_SESSION", "S-1")
    _stash_injection(oms_home, "S-1")
    assert _hook.main(io.StringIO(_payload())) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    spec = payload["hookSpecificOutput"]
    assert spec["hookEventName"] == "SessionStart"
    assert "rtol=1e-10" in spec["additionalContext"]
    assert "curator/feedme01" in spec["additionalContext"]
    # NOT consumed: /clear rolls a new harness session that deserves it too
    assert (oms_home / "inject" / "S-1.json").is_file()


def test_session_start_silent_without_stash(
    oms_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OMS_SESSION", "S-1")
    assert _hook.main(io.StringIO(_payload())) == 0
    assert capsys.readouterr().out == ""


def test_session_end_never_emits_context(
    oms_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OMS_SESSION", "S-1")
    _stash_injection(oms_home, "S-1")
    assert _hook.main(io.StringIO(_payload(hook_event_name="SessionEnd"))) == 0
    assert capsys.readouterr().out == ""


def test_corrupt_stash_swallowed(
    oms_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OMS_SESSION", "S-1")
    d = oms_home / "inject"
    d.mkdir(parents=True, exist_ok=True)
    (d / "S-1.json").write_text("{not json", encoding="utf-8")
    assert _hook.main(io.StringIO(_payload())) == 0  # never disturbs the host
    assert capsys.readouterr().out == ""
