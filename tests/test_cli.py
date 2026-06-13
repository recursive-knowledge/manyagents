"""M8 tests for manyagent.cli — the dumb orchestrator (manyagent.cli.md Verification).

Pure helpers (slash sniffer incl `/path` passthrough, ★/y-n prompts,
preview, argparse for every verb+flag), the two-stage SIGINT handler, and
async orchestration on a FakeBank with scripted ``input()``. The headline
case is **C1**: a rejected/parser-refused `/self-distill` post is NOT
persisted and the record never carries ``preference`` (the M8 third defense
that supersedes ``manyagent.cli.md:61``).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import pytest

from manyagent import cli
from manyagent.bank import FakeBank
from manyagent.utils import config


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_HOME", str(tmp_path / ".manyagent"))
    monkeypatch.delenv("MANYAGENT_NONINTERACTIVE", raising=False)
    # M11: `_do_run_agent` exports MANYAGENT_SESSION before PTY spawn; delenv at
    # setup records "was absent" so monkeypatch restores absence at teardown
    # (prevents MANYAGENT_SESSION pollution between tests).
    monkeypatch.delenv("MANYAGENT_SESSION", raising=False)
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "deny")  # tests never write to ~/.claude
    from manyagent.forum import clear_discuss_gate

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
# pure: argparse for every verb + goal positional/--id/--session
# --------------------------------------------------------------------------- #


def test_argparse_lifecycle_verbs_only() -> None:
    """M11.4: only the session-lifecycle verbs are CLI subcommands now.
    self-distill / discuss / cross-distill / inject are no longer here —
    they're installed as in-agent skills + MCP tools by ``manyagent <name>``.
    Programmatic callers use ``manyagent._handlers.do_*`` directly."""
    a = _args("init", "--bank-url", "https://db.example", "--trusted-key", "K")
    assert (a.verb, a.bank_url, a.trusted_key) == ("init", "https://db.example", "K")
    assert _args("preflight").verb == "preflight"
    a = _args("start", "speed", "--id", "S1")
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
    raise RuntimeError("Bank identity 'trusted' has no key (MANYAGENT_BANK_TRUSTED_KEY unset)")


def test_guard_translates_bank_error_no_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MANYAGENT_DEBUG", raising=False)
    rc = cli._guard(_boom_runtime())
    assert rc == 1
    err = capsys.readouterr().err
    assert "no key" in err  # the real cause is shown
    assert "ma init" in err and "ma preflight" in err and "MANYAGENT_DEBUG=1" in err  # actionable


def test_guard_debug_env_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_DEBUG", "1")  # developer escape hatch
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
        raise SystemExit("no active session: run `manyagent start` or pass --session <id>")

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
    """The reported scenario: `manyagent start` with no Bank key must fail cleanly."""

    class _NoKeyBank:
        async def put_session(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("Bank identity 'trusted' has no key (MANYAGENT_BANK_TRUSTED_KEY unset)")

    monkeypatch.delenv("MANYAGENT_DEBUG", raising=False)
    monkeypatch.setattr(cli, "get_bank", lambda *a, **k: _NoKeyBank())
    rc = cli.main(["start", "demo", "--id", "DEMO-0001"])
    assert rc == 1  # not an unhandled traceback
    assert "ma preflight" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# two-stage SIGINT
# --------------------------------------------------------------------------- #


def test_sigint_two_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    forces: list[bool] = []
    monkeypatch.setattr("manyagent.adapters.terminate_all_agents", lambda *, force=False: forces.append(force))

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
# init: first-run setup writes the user-level env file
# --------------------------------------------------------------------------- #


def _clear_bank_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the carry-forward seam deterministic: a dev machine's manyagent.env
    (loaded at import) may have populated these in the process env."""
    for var in (
        "MANYAGENT_BANK_ANON_KEY",
        "MANYAGENT_BANK_CF_ACCESS_CLIENT_ID",
        "MANYAGENT_BANK_CF_ACCESS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)


async def test_init_flags_write_user_env(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """`manyagent init --bank-url … --trusted-key …` writes $MANYAGENT_HOME/env with
    exactly the non-empty values, 0600, no prompts (Scripted is empty)."""
    _clear_bank_env(monkeypatch)
    s = Scripted()
    rc = await cli._do_init(
        _args("init", "--bank-url", "https://db.example", "--trusted-key", "JWT"), bank=fake_bank, io=s.io()
    )
    assert rc == 0
    p = cli._user_env_path()
    text = p.read_text(encoding="utf-8")
    assert "MANYAGENT_BANK_URL=https://db.example\n" in text
    assert "MANYAGENT_BANK_TRUSTED_KEY=JWT\n" in text
    assert "ANON" not in text and "CF_ACCESS" not in text  # empty values not written
    if sys.platform != "win32":
        assert (p.stat().st_mode & 0o777) == 0o600  # the key is a credential
    assert any("wrote" in line for line in s.out)
    # The written file is what `manyagent.setup_environment` loads from any cwd.


async def test_init_carries_forward_resolved_anon_and_cf(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-run (e.g. rotating the trusted key) must not silently drop the
    stored anon key / CF Access pair: flags win, else the resolved config is
    carried forward into the rewritten file."""
    monkeypatch.setenv("MANYAGENT_BANK_ANON_KEY", "ANON-1")
    monkeypatch.setenv("MANYAGENT_BANK_CF_ACCESS_CLIENT_ID", "CF-ID")
    monkeypatch.setenv("MANYAGENT_BANK_CF_ACCESS_CLIENT_SECRET", "CF-SECRET")
    s = Scripted()
    rc = await cli._do_init(
        _args("init", "--bank-url", "https://db.example", "--trusted-key", "K2"), bank=fake_bank, io=s.io()
    )
    assert rc == 0
    text = cli._user_env_path().read_text(encoding="utf-8")
    assert "MANYAGENT_BANK_ANON_KEY=ANON-1\n" in text
    assert "MANYAGENT_BANK_CF_ACCESS_CLIENT_ID=CF-ID\n" in text
    assert "MANYAGENT_BANK_CF_ACCESS_CLIENT_SECRET=CF-SECRET\n" in text


async def test_init_values_round_trip_through_dotenv(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """Values outside the unquoted-safe charset (spaces, `#`, quotes, a pasted
    `NAME=` prefix) are quoted/escaped so dotenv reads back EXACTLY what was
    given — the old verbatim writer silently truncated at ` # ` and dropped
    unparseable keys on load."""
    import dotenv

    _clear_bank_env(monkeypatch)
    tricky = 'pa ss"word # not-a-comment'
    s = Scripted()
    rc = await cli._do_init(
        _args("init", "--bank-url", "https://db.example", "--trusted-key", tricky), bank=fake_bank, io=s.io()
    )
    assert rc == 0
    parsed = dotenv.dotenv_values(cli._user_env_path())
    assert parsed["MANYAGENT_BANK_TRUSTED_KEY"] == tricky
    # A newline cannot be represented losslessly — refused, nothing written.
    cli._user_env_path().unlink()
    with pytest.raises(SystemExit, match="newline"):
        await cli._do_init(
            _args("init", "--bank-url", "https://db.example", "--trusted-key", "a\nb"), bank=fake_bank, io=s.io()
        )
    assert not cli._user_env_path().exists()


async def test_init_key_prompt_strips_pasted_assignment(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pasting the full `MANYAGENT_BANK_TRUSTED_KEY=eyJ…` line at the key prompt
    must store the key, not a `NAME=`-prefixed wrong value."""
    _clear_bank_env(monkeypatch)
    monkeypatch.delenv("MANYAGENT_BANK_TRUSTED_KEY", raising=False)
    s = Scripted("", "MANYAGENT_BANK_TRUSTED_KEY=eyJWT")  # URL prompt, key prompt
    rc = await cli._do_init(_args("init"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert "MANYAGENT_BANK_TRUSTED_KEY=eyJWT\n" in cli._user_env_path().read_text(encoding="utf-8")


async def test_init_overwrite_detail_masks_credentials(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """`d` at the overwrite gate shows WHAT would be replaced with every
    KEY/SECRET value redacted — the inspect affordance must not leak the
    stored credential into scrollback."""
    _clear_bank_env(monkeypatch)
    p = cli._user_env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("MANYAGENT_BANK_URL=http://old.example\nMANYAGENT_BANK_TRUSTED_KEY=SUPERSECRETJWT\n", encoding="utf-8")
    s = Scripted("d", "n")  # inspect, then decline
    rc = await cli._do_init(
        _args("init", "--bank-url", "https://db.example", "--trusted-key", "K"), bank=fake_bank, io=s.io()
    )
    assert rc == 1
    shown = "\n".join(s.out)
    assert "http://old.example" in shown  # non-secret values stay legible
    assert "<redacted>" in shown and "SUPERSECRETJWT" not in shown


async def test_init_prompts_default_to_resolved_config(fake_bank: FakeBank) -> None:
    """Interactive path: Enter at both prompts keeps the resolved defaults; an
    empty key gets the yellow still-needed note."""
    s = Scripted("", "")  # URL prompt, key prompt
    rc = await cli._do_init(_args("init"), bank=fake_bank, io=s.io())
    assert rc == 0
    text = cli._user_env_path().read_text(encoding="utf-8")
    assert f"MANYAGENT_BANK_URL={config.resolve('MANYAGENT_BANK_URL', config.MANYAGENT_BANK_URL)}\n" in text
    if not config.resolve("MANYAGENT_BANK_TRUSTED_KEY", ""):
        assert any("MANYAGENT_BANK_TRUSTED_KEY" in line for line in s.out)  # the no-key warning


async def test_init_overwrite_gate_declined_keeps_file(fake_bank: FakeBank) -> None:
    p = cli._user_env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("MANYAGENT_BANK_URL=http://keep.me\n", encoding="utf-8")
    s = Scripted("n")  # the single overwrite allowance gate
    rc = await cli._do_init(
        _args("init", "--bank-url", "https://db.example", "--trusted-key", "K"), bank=fake_bank, io=s.io()
    )
    assert rc == 1
    assert p.read_text(encoding="utf-8") == "MANYAGENT_BANK_URL=http://keep.me\n"


async def test_init_noninteractive_never_overwrites(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deny-by-default (Open-Q §B5): an unattended run never clobbers an
    existing env file; a missing one is written without prompting."""
    monkeypatch.setenv("MANYAGENT_NONINTERACTIVE", "1")
    s = Scripted()  # zero prompts allowed — Scripted raises on any pop
    rc = await cli._do_init(
        _args("init", "--bank-url", "https://db.example", "--trusted-key", "K"), bank=fake_bank, io=s.io()
    )
    assert rc == 0 and cli._user_env_path().is_file()
    rc = await cli._do_init(_args("init", "--bank-url", "https://other.example"), bank=fake_bank, io=s.io())
    assert rc == 1  # overwrite denied
    assert "db.example" in cli._user_env_path().read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# orchestration: start/end + active-session round-trip
# --------------------------------------------------------------------------- #


async def test_start_writes_active_and_end_clears_it(fake_bank: FakeBank) -> None:
    s = Scripted()
    rc = await cli._do_start(_args("start", "g", "--id", "SESS-0001"), bank=fake_bank, io=s.io())
    assert rc == 0 and cli._read_active() == "SESS-0001"
    assert (await fake_bank.get_session("SESS-0001"))["goal"] == "g"
    # The viewer URL is the actionable artifact (bare ID is dead-on-arrival).
    # `http` not `http://`: the default base is the hosted viewer (https).
    assert any(line.startswith("open: http") and "/s/SESS-0001" in line for line in s.out)

    end = Scripted("skip")
    rc = await cli._do_end(_args("end", "--session", "SESS-0001"), bank=fake_bank, io=end.io())
    assert rc == 0 and cli._read_active() is None
    assert (await fake_bank.get_session("SESS-0001"))["status"] == "ended"


def test_session_url_defaults_to_hosted_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's `open:` links point at the hosted viewer by default — the
    deployment may move, so the base is the MANYAGENT_WEB_PUBLIC_URL tunable, never
    a hardcoded string at the call sites."""
    monkeypatch.delenv("MANYAGENT_WEB_PUBLIC_URL", raising=False)
    assert cli._session_url("X-1") == f"{config.MANYAGENT_WEB_PUBLIC_URL}/s/X-1"
    assert config.MANYAGENT_WEB_PUBLIC_URL == "https://swarms.formulacode.org"


def test_session_url_public_base_override_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_URL", "https://example.org/viewer/")
    assert cli._session_url("X-1") == "https://example.org/viewer/s/X-1"


def test_session_url_empty_public_base_falls_back_to_web_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_URL", "")
    monkeypatch.setenv("MANYAGENT_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("MANYAGENT_WEB_PORT", "9001")
    assert cli._session_url("ABCD-1234") == "http://127.0.0.1:9001/s/ABCD-1234"


def test_session_url_maps_wildcard_bind_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.0.0.0 is a bind wildcard, not a reachable address — must render as 127.0.0.1.
    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_URL", "")
    monkeypatch.setenv("MANYAGENT_WEB_HOST", "0.0.0.0")  # noqa: S104 — testing the defensive rewrite of the wildcard, not a real bind
    monkeypatch.setenv("MANYAGENT_WEB_PORT", "8000")
    assert cli._session_url("X-1").startswith("http://127.0.0.1:8000/")


def test_agent_url_round_trips_canonical_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_URL", "")
    monkeypatch.setenv("MANYAGENT_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("MANYAGENT_WEB_PORT", "9001")
    assert cli._agent_url("ABCD-1234/agent-001-claude") == "http://127.0.0.1:9001/s/ABCD-1234/a/agent-001-claude"


async def test_register_prints_open_link(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_WEB_PUBLIC_URL", "")
    monkeypatch.setenv("MANYAGENT_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("MANYAGENT_WEB_PORT", "8580")
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
# manyagent end — ★ lands on the most recent unrated reflection post
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
# manyagent <name> — PTY spawn (stdlib call monkeypatched) + skill install + capture
# --------------------------------------------------------------------------- #


async def test_run_agent_spawns_pty_installs_skills_and_captures_raw_packet(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M11.6: the PTY path now tees the master output and persists it as a
    `raw` packet via `manyagent.capture.persist` (closes the M8 deferral). The
    monkeypatched `_pty_spawn` accepts the `tee` kwarg but never writes to
    it, so the captured trace is empty bytes — `persist` still creates the
    raw packet (validate → scrub → bound → put_packet all accept it)."""
    await fake_bank.put_session("S1", goal="g")
    cli._write_active("S1")
    spawned: list[list[str]] = []
    # The real ``_pty_spawn(argv, tee=...)`` takes a tee kwarg now; the
    # monkeypatch must accept it (it just ignores writes — no tty in tests).
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: spawned.append(argv))
    from manyagent import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: FakeAdapter())
    s = Scripted()
    rc = await cli._do_run_agent("claude", ["--help"], None, bank=fake_bank, io=s.io())
    assert rc == 0 and spawned == [["claude", "--help"]]
    # A raw packet was persisted (empty trace bytes, but still a valid record).
    raws = await fake_bank.list_packets(session_id="S1", type="raw")
    assert len(raws) == 1
    assert any("trace:" in line for line in s.out)  # the run prints the viewer link


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="_pty_spawn is POSIX-only by contract (its win32 guard raises before "
    "the non-TTY fallback, and _pipe_spawn's select() on a pipe fd is POSIX too)",
)
def test_pty_spawn_non_tty_tees_instead_of_exec_replacing(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The M11 non-TTY hole: redirected stdin used to hit an ``os.execvp``
    that REPLACED the manyagent process — no tee was written, and `_do_run_agent`
    never reached capture/persist. The fallback must instead spawn a piped
    child, tee its merged stdout+stderr, and RETURN control to the caller."""
    import sys as _sys
    import types

    monkeypatch.setattr(cli.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    tee = tmp_path / "tee.log"
    code = "import sys; print('out-marker'); print('err-marker', file=sys.stderr); sys.exit(7)"
    rc = cli._pty_spawn([_sys.executable, "-c", code], tee=tee)  # returns ⇒ not exec-replaced
    assert rc == 7  # the agent's exit code propagates (the execvp-era contract)
    data = tee.read_bytes()
    assert b"out-marker" in data
    assert b"err-marker" in data  # stderr lands in the capture, as a PTY would merge it
    # M12.1: the timing sidecar pairs every read with a monotonic offset, and
    # its byte counts account for the tee exactly (the _timed_capture
    # contract). Size records (`<off> r <cols>x<rows>`) appear only when
    # stdout/stderr is a real terminal — not under pytest capture.
    timing = (tmp_path / "tee.log.timing").read_text().splitlines()
    assert timing, "timing sidecar missing"
    reads = [parts for parts in (line.split() for line in timing) if len(parts) == 2]
    assert sum(int(n) for _t, n in reads) == len(data)
    offsets = [float(t) for t, _n in reads]
    assert offsets == sorted(offsets)


# --------------------------------------------------------------------------- #
# _timed_capture — timing sidecar → timestamped TraceEvents + geometry
# --------------------------------------------------------------------------- #


def test_timed_capture_builds_timestamped_chunks_and_survives_split_glyphs() -> None:
    """Real sidecar → one event per read with its offset; a multi-byte glyph
    split across two reads decodes intact (incremental decoder)."""
    star = "✶".encode()  # 3 bytes
    raw = b"hello " + star[:1], star[1:] + b" world"
    tee = b"".join(raw)
    timing = f"0.5 {len(raw[0])}\n2.25 {len(raw[1])}\n"
    events, term = cli._timed_capture(tee, timing)
    assert [e.ts for e in events] == [0.5, 2.25]
    assert "".join(e.text for e in events) == "hello ✶ world"
    assert "�" not in "".join(e.text for e in events)  # no shredded glyph
    assert term is None  # no size records in this sidecar


def test_timed_capture_parses_geometry_records() -> None:
    """`<off> r <cols>x<rows>` lines become the envelope's term: the first is
    the initial size, the rest are resizes (M12.2 — the formatting-mess fix:
    a cast replayed at a guessed width wraps every box border)."""
    tee = b"abcd"
    timing = "0.0 r 159x37\n0.5 2\n3.0 r 100x37\n3.2 2\n"
    events, term = cli._timed_capture(tee, timing)
    assert [e.ts for e in events] == [0.5, 3.2]
    assert term == {"cols": 159, "rows": 37, "resizes": [[3.0, 100, 37]]}


def test_timed_capture_falls_back_to_single_event_without_sidecar() -> None:
    events, term = cli._timed_capture(b"plain bytes", "")
    assert len(events) == 1 and events[0].ts == 0.0 and events[0].text == "plain bytes"
    assert term is None


def test_timed_capture_falls_back_on_byte_count_mismatch_or_garbage() -> None:
    events, term = cli._timed_capture(b"abcdef", "0.0 r 100x30\n0.1 2\n0.2 2\n")
    assert len(events) == 1  # 4 != 6 — timing dropped...
    assert term == {"cols": 100, "rows": 30, "resizes": []}  # ...geometry kept
    events, term = cli._timed_capture(b"abcdef", "not numbers\n")
    assert len(events) == 1 and term is None


def test_timed_capture_collapses_when_joined_text_holds_a_secret() -> None:
    """A credential split across two reads would defeat per-event scrub —
    any joined-text hit sacrifices timing so persist() scrubs it whole."""
    secret = b"sk-ant-api03-" + b"A" * 24
    half = len(secret) // 2
    tee = b"before " + secret + b" after"
    cut = 7 + half  # split point lands mid-secret
    timing = f"0.1 {cut}\n0.2 {len(tee) - cut}\n"
    events, _term = cli._timed_capture(tee, timing)
    assert len(events) == 1 and events[0].ts == 0.0  # collapsed; scrub sees it whole


async def test_run_agent_mines_harness_rendition(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M13.1: after capture, the adapter's mine() artifact is persisted as
    the `harness` rendition keyed to the raw packet, and the run summary
    line reports it. The MineContext carries this run's bindings."""
    import json as _json

    await fake_bank.put_session("S1", goal="g")
    cli._write_active("S1")
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: None)
    seen_ctx: list[Any] = []

    class MiningAdapter(FakeAdapter):
        def mine(self, ctx: Any) -> dict[str, Any] | None:
            seen_ctx.append(ctx)
            return {
                "miner_version": "claude-v1",
                "binding": "hook",
                "completeness": "full",
                "run_started": ctx.window[0],
                "segments": [{"harness_session_id": "hs-1", "turns": [{"role": "user", "ts": None, "text": "hi"}]}],
            }

    from manyagent import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: MiningAdapter())
    s = Scripted()
    rc = await cli._do_run_agent("claude", [], None, bank=fake_bank, io=s.io())
    assert rc == 0
    raws = await fake_bank.list_packets(session_id="S1", type="raw")
    assert len(raws) == 1
    rend = await fake_bank.get_rendition(raws[0]["id"], "harness")
    assert rend is not None and rend["miner_version"] == "claude-v1"
    assert _json.loads(rend["body"])["segments"][0]["harness_session_id"] == "hs-1"
    assert seen_ctx and seen_ctx[0].window[0] <= seen_ctx[0].window[1]


async def test_run_agent_mining_failure_never_disturbs_the_run(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await fake_bank.put_session("S1", goal="g")
    cli._write_active("S1")
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: None)

    class ExplodingAdapter(FakeAdapter):
        def mine(self, ctx: Any) -> dict[str, Any] | None:
            raise RuntimeError("transcript parse exploded")

    from manyagent import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: ExplodingAdapter())
    s = Scripted()
    rc = await cli._do_run_agent("claude", [], None, bank=fake_bank, io=s.io())
    assert rc == 0  # the run completes
    assert len(await fake_bank.list_packets(session_id="S1", type="raw")) == 1  # capture unaffected
    assert any("mining skipped" in line for line in s.out)  # honest note, no crash


async def test_run_agent_passes_this_runs_bindings_to_the_miner(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M12/M13: binding records appended by ``manyagent._hook`` while the agent ran
    reach the miner via MineContext; stale records from an earlier run of the
    same session (and malformed lines) are filtered out by the run-start
    clock."""
    import json as _json
    import time as _time

    await fake_bank.put_session("S1", goal="g")
    cli._write_active("S1")

    def fake_spawn(argv: list[str], tee: Any = None) -> None:
        d = cli._manyagent_home() / "bindings"
        d.mkdir(parents=True, exist_ok=True)
        stale = {"manyagent_session": "S1", "harness_session_id": "stale-id", "ts": _time.time() - 3600}
        live = {
            "manyagent_session": "S1",
            "event": "SessionEnd",
            "harness_session_id": "abc-123",
            "transcript_path": "/tmp/t.jsonl",
            "ts": _time.time(),
        }
        (d / "S1.jsonl").write_text(_json.dumps(stale) + "\n" + _json.dumps(live) + "\nnot json{\n")

    seen_ctx: list[Any] = []

    class CapturingAdapter(FakeAdapter):
        def mine(self, ctx: Any) -> dict[str, Any] | None:
            seen_ctx.append(ctx)
            return None

    monkeypatch.setattr(cli, "_pty_spawn", fake_spawn)
    from manyagent import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: CapturingAdapter())
    s = Scripted()
    rc = await cli._do_run_agent("claude", [], None, bank=fake_bank, io=s.io())
    assert rc == 0
    assert seen_ctx, "miner was not called"
    ids = {str(b.get("harness_session_id")) for b in seen_ctx[0].bindings}
    assert "abc-123" in ids  # this run's binding reached the miner
    assert "stale-id" not in ids  # pre-run records filtered by the run-start clock


# --------------------------------------------------------------------------- #
# single-gate prompts (2026-06-10): ask_allow + ask_commit
# --------------------------------------------------------------------------- #


def test_ask_allow_enter_allows_n_declines() -> None:
    out: list[str] = []
    # Enter (empty input) allows — the affirmative default is the point.
    assert cli.ask_allow("go?", input_fn=lambda _: "", output_fn=out.append, noninteractive=False) is True
    for decline in ("n", "no", "esc", "q"):
        assert (
            cli.ask_allow("go?", input_fn=lambda _, d=decline: d, output_fn=out.append, noninteractive=False) is False
        )
    # Noninteractive stays deny-by-default (Open-Q §B5).
    assert cli.ask_allow("go?", input_fn=lambda _: "", output_fn=out.append, noninteractive=True) is False


def test_ask_commit_single_prompt_carries_star() -> None:
    out: list[str] = []
    assert cli.ask_commit(3, input_fn=lambda _: "", output_fn=out.append, noninteractive=False) == (True, 3)
    assert cli.ask_commit(3, input_fn=lambda _: "5", output_fn=out.append, noninteractive=False) == (True, 5)
    assert cli.ask_commit(3, input_fn=lambda _: "skip", output_fn=out.append, noninteractive=False) == (True, None)
    assert cli.ask_commit(3, input_fn=lambda _: "n", output_fn=out.append, noninteractive=False) == (False, None)
    # Noninteractive auto-commits unrated (parser already gated quality).
    assert cli.ask_commit(3, input_fn=lambda _: "junk", output_fn=out.append, noninteractive=True) == (True, None)


def test_gates_expand_truncated_preview_on_d() -> None:
    """With `detail` (the untruncated post), `d` prints it and the gate
    re-asks — for both ask_commit's typed fallback and ask_allow; without
    `detail`, `d` keeps its old meaning (unrecognized / not a decline)."""
    responses = ["d", ""]
    out: list[str] = []
    rc = cli.ask_commit(
        3, input_fn=lambda _: responses.pop(0), output_fn=out.append, noninteractive=False, detail="FULL POST"
    )
    assert rc == (True, 3) and out == ["FULL POST"]
    responses = ["d", "n"]
    out = []
    assert (
        cli.ask_allow(
            "commit reply?",
            input_fn=lambda _: responses.pop(0),
            output_fn=out.append,
            noninteractive=False,
            detail="FULL REPLY",
        )
        is False
    )
    assert out == ["FULL REPLY"]
    # no detail ⇒ `d` is not a decline and not an expansion
    assert cli.ask_allow("go?", input_fn=lambda _: "d", output_fn=out.append, noninteractive=False) is True


# --------------------------------------------------------------------------- #
# sensible default (2026-06-10): start-time inject offer for a knowing goal
# --------------------------------------------------------------------------- #


async def _seed_goal_knowledge(bank: FakeBank, goal: str = "speed") -> str:
    pid = "curator/feedme01"
    await bank.put_session("curator")
    await bank.put_packet({
        "id": pid,
        "session_id": "curator",
        "type": "distill",
        "agent_id": "curator",
        "goal": goal,
        "scope": "per_goal",
        "bundle": {"confirmed_constraints": ["`rtol=1e-10` before CFL tuning"]},
        "parents": [],
        "curator": "local",
    })
    return pid


async def test_start_offers_goal_context_enter_injects_and_stashes(fake_bank: FakeBank) -> None:
    pid = await _seed_goal_knowledge(fake_bank)
    s = Scripted("")  # Enter at the single allowance gate
    rc = await cli._do_start(_args("start", "speed", "--id", "S-INJ1"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert any(i["packet_id"] == pid and i["target_session_id"] == "S-INJ1" for i in await fake_bank.list_injections())
    stash = cli._inject_stash_path("S-INJ1")
    assert stash.is_file() and "rtol" in stash.read_text(encoding="utf-8")
    # the offer announced how much prior knowledge exists
    assert any("1 bundle" in line for line in s.out)


async def test_start_offer_declined_records_nothing(fake_bank: FakeBank) -> None:
    await _seed_goal_knowledge(fake_bank)
    s = Scripted("n")
    rc = await cli._do_start(_args("start", "speed", "--id", "S-INJ2"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert await fake_bank.list_injections() == []
    assert not cli._inject_stash_path("S-INJ2").is_file()


async def test_start_offer_skipped_when_goal_unknown_or_noninteractive(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch
) -> None:
    # no knowledge for the goal → no prompt at all (Scripted would raise on pop)
    rc = await cli._do_start(_args("start", "fresh", "--id", "S-INJ3"), bank=fake_bank, io=Scripted().io())
    assert rc == 0
    # knowledge exists but MANYAGENT_NONINTERACTIVE → silent skip, never auto-inject
    await _seed_goal_knowledge(fake_bank)
    monkeypatch.setenv("MANYAGENT_NONINTERACTIVE", "1")
    rc = await cli._do_start(_args("start", "speed", "--id", "S-INJ4"), bank=fake_bank, io=Scripted().io())
    assert rc == 0
    assert await fake_bank.list_injections() == []


# --------------------------------------------------------------------------- #
# sensible default (2026-06-10): end-time distill offers
# --------------------------------------------------------------------------- #


async def test_end_offers_self_distill_when_session_has_none(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fake_bank.put_session("S-END1", goal="speed")
    await fake_bank.put_agent("S-END1/agent-001-claude", session_id="S-END1", adapter="claude", seq=1)
    calls: list[dict[str, Any]] = []

    async def fake_self_distill(**kw: Any) -> int:
        calls.append(kw)
        return 0

    monkeypatch.setattr("manyagent._handlers.do_self_distill", fake_self_distill)
    s = Scripted("")  # Enter = yes, distill
    rc = await cli._do_end(_args("end", "--session", "S-END1"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert len(calls) == 1 and calls[0]["adapter"] == "claude" and calls[0]["session"] == "S-END1"
    assert calls[0]["since"] is None  # bare `manyagent end`: no run window to scope to


async def test_end_threads_run_window_into_self_distill(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent-exit close path hands `_do_end` the run-start clock so the
    reflection's trace context is scoped to THIS run's bound transcripts."""
    await fake_bank.put_session("S-END2", goal="speed")
    await fake_bank.put_agent("S-END2/agent-001-claude", session_id="S-END2", adapter="claude", seq=1)
    calls: list[dict[str, Any]] = []

    async def fake_self_distill(**kw: Any) -> int:
        calls.append(kw)
        return 0

    monkeypatch.setattr("manyagent._handlers.do_self_distill", fake_self_distill)
    s = Scripted("")
    rc = await cli._do_end(argparse.Namespace(session="S-END2", since=123.0), bank=fake_bank, io=s.io())
    assert rc == 0
    assert len(calls) == 1 and calls[0]["since"] == 123.0


async def test_start_nudges_cross_distill_when_goal_is_stale(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """≥ MANYAGENT_CROSS_NUDGE_MIN reflections newer than the goal's newest bundle
    → `manyagent start` offers cross-distillation (the moment a fresh bundle is
    about to be useful). Replaces the end-of-session cross offer."""
    pid = await _seed_goal_knowledge(fake_bank)  # bundle at T0
    for i in range(3):
        await fake_bank.put_session(f"OLD-{i}", goal="speed")
        await fake_bank.put_packet({
            "id": f"OLD-{i}/post{i:04d}",
            "session_id": f"OLD-{i}",
            "type": "post",
            "agent_id": f"OLD-{i}/agent-001-claude",
            "kind": "reflection",
            "goal": "speed",
            "structured": dict(_GOOD),
            "rating": 3,
        })
    calls: list[dict[str, Any]] = []

    async def fake_cross_distill(**kw: Any) -> int:
        calls.append(kw)
        return 0

    monkeypatch.setattr("manyagent._handlers.do_cross_distill", fake_cross_distill)
    s = Scripted("", "n")  # Enter = yes to the nudge; n = skip the inject offer
    rc = await cli._do_start(_args("start", "speed", "--id", "S-NUDGE"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert len(calls) == 1 and calls[0]["session"] == "S-NUDGE"
    assert pid  # the pre-existing bundle did not suppress the nudge (posts are newer)


async def test_start_nudge_quiet_right_after_cross_distill(fake_bank: FakeBank) -> None:
    """Reflections OLDER than the newest bundle never re-trigger the nudge —
    the guard against calling cross-distill twice in immediate succession."""
    for i in range(3):
        await fake_bank.put_session(f"OLD-{i}", goal="speed")
        await fake_bank.put_packet({
            "id": f"OLD-{i}/post{i:04d}",
            "session_id": f"OLD-{i}",
            "type": "post",
            "agent_id": f"OLD-{i}/agent-001-claude",
            "kind": "reflection",
            "goal": "speed",
            "structured": dict(_GOOD),
            "rating": 3,
        })
    await _seed_goal_knowledge(fake_bank)  # bundle created AFTER the posts
    s = Scripted("n")  # only the inject offer fires; no nudge prompt
    rc = await cli._do_start(_args("start", "speed", "--id", "S-NUDGE2"), bank=fake_bank, io=s.io())
    assert rc == 0


async def test_end_followup_when_bundle_was_injected(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    """A session that consumed an injected bundle gets the guided follow-up
    offer ("did it hold up?") whose guidance cites the bundle id."""
    await fake_bank.put_session("S-FUP", goal="speed")
    await fake_bank.put_agent("S-FUP/agent-001-claude", session_id="S-FUP", adapter="claude", seq=1)
    pid = await _seed_goal_knowledge(fake_bank)
    await fake_bank.record_injection(pid, "S-FUP")
    calls: list[dict[str, Any]] = []

    async def fake_self_distill(**kw: Any) -> int:
        calls.append(kw)
        return 0

    monkeypatch.setattr("manyagent._handlers.do_self_distill", fake_self_distill)
    s = Scripted("")
    rc = await cli._do_end(_args("end", "--session", "S-FUP"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert len(calls) == 1 and pid in (calls[0]["guidance"] or "")


async def test_start_goal_continuity_offer(fake_bank: FakeBank) -> None:
    """`manyagent start` without a goal offers the previous session's goal; Enter
    adopts it onto the new session; declining files the session under the
    default bucket."""
    await fake_bank.put_session("PREV-1", goal="speed")
    s = Scripted("")  # Enter = continue /speed (no further offers: goal has no bundles)
    rc = await cli._do_start(_args("start", "--id", "S-CONT"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert (await fake_bank.get_session("S-CONT"))["goal"] == "speed"
    declined = Scripted("n")
    rc = await cli._do_start(_args("start", "--id", "S-CONT2"), bank=fake_bank, io=declined.io())
    assert rc == 0
    assert (await fake_bank.get_session("S-CONT2"))["goal"] == "misc"
    assert any("/misc" in line for line in declined.out)


async def test_start_quarantine_note_is_informational(fake_bank: FakeBank) -> None:
    await fake_bank.put_session("Q-1", goal="speed")
    await fake_bank.put_packet({
        "id": "Q-1/quarantined1",
        "session_id": "Q-1",
        "type": "post",
        "agent_id": "Q-1/agent-001-claude",
        "kind": "reflection",
        "goal": "speed",
        "structured": dict(_GOOD),
        "quarantined": True,
    })
    s = Scripted()  # informational only — consumes NO input
    rc = await cli._do_start(_args("start", "speed", "--id", "S-QN"), bank=fake_bank, io=s.io())
    assert rc == 0
    assert any("quarantined" in line for line in s.out)


async def test_end_offers_silent_when_noninteractive(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_NONINTERACTIVE", "1")
    await fake_bank.put_session("S-END3", goal="speed")
    await fake_bank.put_agent("S-END3/agent-001-claude", session_id="S-END3", adapter="claude", seq=1)
    rc = await cli._do_end(_args("end", "--session", "S-END3"), bank=fake_bank, io=Scripted().io())
    assert rc == 0  # no prompts consumed, no offers fired


# --------------------------------------------------------------------------- #
# agent exit asks ONE question — end? — and `_do_end` owns the distill moment
# --------------------------------------------------------------------------- #


async def test_agent_exit_asks_only_end_then_do_end_offers_distill(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent exit asks a single question (end the session?); accepting runs
    the same `_do_end` close path, whose distill offer fires there. (The
    separate agent-exit distill ask double-prompted: a failed draft was
    re-offered moments later inside `_do_end`.)"""
    await fake_bank.put_session("S1", goal="g")
    cli._write_active("S1")
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: None)
    from manyagent import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: FakeAdapter())
    calls: list[dict[str, Any]] = []

    async def fake_self_distill(**kw: Any) -> int:
        calls.append(kw)
        return 0

    monkeypatch.setattr(h, "do_self_distill", fake_self_distill)
    s = Scripted("", "")  # Enter = yes, end the session; Enter = yes at the distill offer
    rc = await cli._do_run_agent("claude", [], None, bank=fake_bank, io=s.io())
    assert rc == 0
    assert len(calls) == 1 and calls[0]["session"] == "S1"  # asked ONCE, inside _do_end
    assert (await fake_bank.get_session("S1"))["status"] == "ended"
    assert cli._read_active() is None  # active cleared by the embedded `manyagent end`


async def test_agent_exit_decline_keeps_session_open_without_distill_ask(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the end offer leaves the session open and asks nothing else —
    the distill moment belongs to `_do_end`, not to every agent exit."""
    await fake_bank.put_session("S1", goal="g")
    cli._write_active("S1")
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: None)
    from manyagent import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: FakeAdapter())
    distilled: list[dict[str, Any]] = []

    async def fake_self_distill(**kw: Any) -> int:
        distilled.append(kw)
        return 0

    monkeypatch.setattr(h, "do_self_distill", fake_self_distill)
    s = Scripted("n")  # n = keep the session open; a second prompt would raise (list exhausted)
    rc = await cli._do_run_agent("claude", [], None, bank=fake_bank, io=s.io())
    assert rc == 0
    assert distilled == []
    assert (await fake_bank.get_session("S1")).get("status") != "ended"
    assert s._r == []  # the single scripted answer went to the end offer
    # an extra prompt would exhaust Scripted and be swallowed into this line:
    assert not any("offers skipped" in line for line in s.out)
