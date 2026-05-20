"""M8 tests for oms.cli — the dumb orchestrator (oms.cli.md Verification).

Pure helpers (slash sniffer incl `/path` passthrough, ★/y-n prompts,
preview, argparse for every verb+flag), the two-stage SIGINT handler, and
async orchestration on a FakeBank with scripted ``input()``. The headline
case is **C1**: a rejected/parser-refused `/self-distill` post is NOT
persisted and the record never carries ``preference`` (the M8 third defense
that supersedes ``oms.cli.md:61``).
"""

from __future__ import annotations

from typing import Any

import pytest

from oms import cli
from oms.bank import FakeBank


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_HOME", str(tmp_path / ".oms"))
    monkeypatch.delenv("OMS_NONINTERACTIVE", raising=False)
    # M11: `_do_run_agent` exports OMS_SESSION before PTY spawn; delenv at
    # setup records "was absent" so monkeypatch restores absence at teardown
    # (prevents OMS_SESSION pollution between tests).
    monkeypatch.delenv("OMS_SESSION", raising=False)
    monkeypatch.setenv("OMS_INSTALL_SKILLS", "deny")  # tests never write to ~/.claude
    from oms.forum import clear_discuss_gate

    clear_discuss_gate()


class Scripted:
    def __init__(self, *responses: str) -> None:
        self._r = list(responses)
        self.out: list[str] = []

    def __call__(self, _prompt: str = "") -> str:
        return self._r.pop(0)

    def io(self) -> tuple[Any, Any]:
        return (self, self.out.append)


_GOOD = {
    "load_bearing_assumption": "the tokenize() hot loop recompiled the regex per call; precompiling fixed it",
    "evidence": "verbatim from trace: 'cumtime 4.2s in tokenize()'",
    "evidence_ref": None,
    "proposed_next": "hoist the compiled pattern to scanner.py module scope",
    "predicted_outcome": "parse throughput ~1.8x; test_parse_speed passes",
    "confidence": "medium",
}


class FakeModel:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, _prompt: str, *, max_tokens: int | None = None) -> str:
        return self.payload


class FakeAdapter:
    name = "claude"
    binary = "claude"

    def __init__(self, payload: str = "") -> None:
        self._model = FakeModel(payload) if payload else None
        self.injected: list[str] = []

    def distill_model(self) -> Any:
        return self._model

    def inject(self, context: str) -> None:
        self.injected.append(context)

    def is_available(self) -> bool:
        return True


def _patch_adapter(monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr(cli, "_adapter_for", lambda *a, **k: adapter)


def _args(*argv: str) -> Any:
    return cli._build_parser().parse_args(list(argv))


# --------------------------------------------------------------------------- #
# pure: prompts + preview
# --------------------------------------------------------------------------- #


def test_preview_tokens_short_unchanged_long_elided() -> None:
    assert cli.preview_tokens("a b c", head=10, tail=10) == "a b c"
    out = cli.preview_tokens(" ".join(str(i) for i in range(50)), head=3, tail=2)
    assert out.startswith("0 1 2 ") and out.endswith(" 48 49") and "elided 45 tokens" in out


def test_ask_rating_paths() -> None:
    s = Scripted()
    assert cli.ask_rating(4, input_fn=s, output_fn=s.out.append, noninteractive=True) is None
    assert cli.ask_rating(3, input_fn=lambda _: "skip", output_fn=s.out.append, noninteractive=False) is None
    assert cli.ask_rating(3, input_fn=lambda _: "", output_fn=s.out.append, noninteractive=False) == 3
    assert cli.ask_rating(3, input_fn=lambda _: "5", output_fn=s.out.append, noninteractive=False) == 5
    assert cli.ask_rating(3, input_fn=lambda _: "junk", output_fn=s.out.append, noninteractive=False) is None


def test_ask_yn_deny_by_default_when_noninteractive() -> None:
    out: list[str] = []
    assert cli.ask_yn("go?", input_fn=lambda _: "y", output_fn=out.append, noninteractive=True) is False
    assert out and "denied" in out[0]
    assert cli.ask_yn("go?", input_fn=lambda _: "yes", output_fn=out.append, noninteractive=False) is True
    assert cli.ask_yn("go?", input_fn=lambda _: "n", output_fn=out.append, noninteractive=False) is False


# --------------------------------------------------------------------------- #
# pure: argparse for every verb + --goal/--server/--stance/--session
# --------------------------------------------------------------------------- #


def test_argparse_lifecycle_verbs_only() -> None:
    """M11.4: only the 5 session-lifecycle verbs are CLI subcommands now.
    self-distill / discuss / cross-distill / inject are no longer here —
    they're installed as in-agent skills + MCP tools by ``oms <name>``.
    Programmatic callers use ``oms._handlers.do_*`` directly."""
    a = _args("start", "S1", "--goal", "speed")
    assert (a.verb, a.id, a.goal) == ("start", "S1", "speed")
    a = _args("register", "claude", "--session", "S2")
    assert (a.verb, a.name, a.session) == ("register", "claude", "S2")
    assert _args("end", "--session", "S9").verb == "end"
    assert _args("status").verb == "status"
    assert _args("uninstall", "claude").verb == "uninstall"
    # Ripped subcommands raise SystemExit (argparse: invalid choice).
    for ripped in ("self-distill", "discuss", "cross-distill", "inject"):
        with pytest.raises(SystemExit):
            _args(ripped)


def test_version_action_exits_zero() -> None:
    with pytest.raises(SystemExit) as ei:
        _args("--version")
    assert ei.value.code == 0


# --------------------------------------------------------------------------- #
# CLI boundary: operational failures translate, never traceback
# --------------------------------------------------------------------------- #


async def _boom_runtime() -> int:
    raise RuntimeError("Bank identity 'trusted' has no key (OMS_BANK_TRUSTED_KEY unset)")


def test_guard_translates_bank_error_no_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OMS_DEBUG", raising=False)
    rc = cli._guard(_boom_runtime())
    assert rc == 1
    err = capsys.readouterr().err
    assert "no key" in err  # the real cause is shown
    assert "python -m oms.preflight" in err and "OMS_DEBUG=1" in err  # actionable


def test_guard_debug_env_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_DEBUG", "1")  # developer escape hatch
    with pytest.raises(RuntimeError, match="no key"):
        cli._guard(_boom_runtime())


def test_guard_keyboardinterrupt_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    async def _interrupted() -> int:
        raise KeyboardInterrupt

    assert cli._guard(_interrupted()) == 130
    assert "interrupted" in capsys.readouterr().err


def test_guard_systemexit_string_is_clean_but_numeric_preserved(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def _no_session() -> int:
        raise SystemExit("no active session: run `oms start` or pass --session <id>")

    assert cli._guard(_no_session()) == 1
    assert "no active session" in capsys.readouterr().err

    async def _numeric() -> int:
        raise SystemExit(2)  # argparse-style — must propagate untouched

    with pytest.raises(SystemExit) as ei:
        cli._guard(_numeric())
    assert ei.value.code == 2


async def test_main_missing_bank_key_returns_1_not_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported scenario: `oms start` with no Bank key must fail cleanly."""

    class _NoKeyBank:
        async def put_session(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("Bank identity 'trusted' has no key (OMS_BANK_TRUSTED_KEY unset)")

    monkeypatch.delenv("OMS_DEBUG", raising=False)
    monkeypatch.setattr(cli, "get_bank", lambda *a, **k: _NoKeyBank())
    rc = cli.main(["start", "DEMO-0001"])
    assert rc == 1  # not an unhandled traceback
    assert "python -m oms.preflight" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# two-stage SIGINT
# --------------------------------------------------------------------------- #


def test_sigint_two_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    forces: list[bool] = []
    monkeypatch.setattr("oms.adapters.terminate_all_agents", lambda *, force=False: forces.append(force))

    class _Exit(SystemExit):
        pass

    monkeypatch.setattr(cli.os, "_exit", lambda code: (_ for _ in ()).throw(_Exit(code)))
    monkeypatch.setattr(cli, "_sigint_count", 0)

    with pytest.raises(KeyboardInterrupt):
        cli._sigint_handler(2, None)
    with pytest.raises(_Exit):
        cli._sigint_handler(2, None)
    assert forces == [False, True]  # 1st: SIGTERM, 2nd: SIGKILL+force-exit


# --------------------------------------------------------------------------- #
# orchestration: start/end + active-session round-trip
# --------------------------------------------------------------------------- #


async def test_start_writes_active_and_end_clears_it(fake_bank: FakeBank) -> None:
    s = Scripted()
    rc = await cli._do_start(_args("start", "SESS-0001", "--goal", "g"), bank=fake_bank, io=s.io())
    assert rc == 0 and cli._read_active() == "SESS-0001"
    assert (await fake_bank.get_session("SESS-0001"))["goal"] == "g"
    # The viewer URL is the actionable artifact (bare ID is dead-on-arrival).
    assert any(line.startswith("open: http://") and "/s/SESS-0001" in line for line in s.out)

    end = Scripted("skip")
    rc = await cli._do_end(_args("end", "--session", "SESS-0001"), bank=fake_bank, io=end.io())
    assert rc == 0 and cli._read_active() is None
    assert (await fake_bank.get_session("SESS-0001"))["status"] == "ended"


def test_session_url_uses_web_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("OMS_WEB_PORT", "9001")
    assert cli._session_url("ABCD-1234") == "http://127.0.0.1:9001/s/ABCD-1234"


def test_session_url_maps_wildcard_bind_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.0.0.0 is a bind wildcard, not a reachable address — must render as 127.0.0.1.
    monkeypatch.setenv("OMS_WEB_HOST", "0.0.0.0")  # noqa: S104 — testing the defensive rewrite of the wildcard, not a real bind
    monkeypatch.setenv("OMS_WEB_PORT", "8000")
    assert cli._session_url("X-1").startswith("http://127.0.0.1:8000/")


def test_agent_url_round_trips_canonical_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("OMS_WEB_PORT", "9001")
    assert cli._agent_url("ABCD-1234/agent-001-claude") == "http://127.0.0.1:9001/s/ABCD-1234/a/agent-001-claude"


async def test_register_prints_open_link(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("OMS_WEB_PORT", "8580")
    await fake_bank.put_session("SESS-AAAA")
    s = Scripted()
    rc = await cli._do_register(_args("register", "claude", "--session", "SESS-AAAA"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert any(line == "registered SESS-AAAA/agent-001-claude" for line in s.out)
    assert any(line == "open: http://127.0.0.1:8580/s/SESS-AAAA/a/agent-001-claude" for line in s.out)


async def test_resolve_sid_errors_without_session(fake_bank: FakeBank) -> None:
    with pytest.raises(SystemExit, match="no active session"):
        await cli._do_register(_args("register", "claude"), bank=fake_bank, io=Scripted().io())


# --------------------------------------------------------------------------- #
# /self-distill, /discuss, /cross-distill, /inject — moved to tests/test_handlers.py
# in M11.4 (they're no longer CLI subcommands).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# oms end — ★ lands on the most recent unrated reflection post
# --------------------------------------------------------------------------- #


async def test_end_star_rates_last_unrated_reflection(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("S1", goal="g")
    await fake_bank.put_packet({
        "id": "S1/r1",
        "session_id": "S1",
        "type": "post",
        "agent_id": "S1/agent-001-claude",
        "kind": "reflection",
        "goal": "g",
        "structured": dict(_GOOD),
        "rating": None,
    })
    s = Scripted("5")
    rc = await cli._do_end(_args("end", "--session", "S1"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert (await fake_bank.get_packet("S1/r1"))["rating"] == 5
    assert (await fake_bank.get_session("S1"))["status"] == "ended"


# --------------------------------------------------------------------------- #
# oms <name> — PTY spawn (stdlib call monkeypatched) + skill install + capture
# --------------------------------------------------------------------------- #


async def test_run_agent_spawns_pty_installs_skills_and_captures_raw_packet(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M11.6: the PTY path now tees the master output and persists it as a
    `raw` packet via `oms.capture.persist` (closes the M8 deferral). The
    monkeypatched `_pty_spawn` accepts the `tee` kwarg but never writes to
    it, so the captured trace is empty bytes — `persist` still creates the
    raw packet (validate → scrub → bound → put_packet all accept it)."""
    await fake_bank.put_session("S1", goal="g")
    cli._write_active("S1")
    spawned: list[list[str]] = []
    # The real ``_pty_spawn(argv, tee=...)`` takes a tee kwarg now; the
    # monkeypatch must accept it (it just ignores writes — no tty in tests).
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: spawned.append(argv))
    from oms import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: FakeAdapter())
    s = Scripted()
    rc = await cli._do_run_agent("claude", ["--help"], None, bank=fake_bank, io=s.io())
    assert rc == 0 and spawned == [["claude", "--help"]]
    # A raw packet was persisted (empty trace bytes, but still a valid record).
    raws = await fake_bank.list_packets(session_id="S1", type="raw")
    assert len(raws) == 1
    assert any("captured raw packet" in line for line in s.out)
