"""The single ``oms`` console-script entrypoint (M8; oms.cli.md).

A **dumb orchestrator**: it sequences ``oms.core`` + ``oms.adapters`` +
``oms.capture`` + ``oms.forum`` + ``oms.distill`` + ``oms.bank`` and owns no
domain logic — guards, schema, anti-meta, curator selection, reuse weighting
all live in modules so the CLI and a programmatic caller cannot diverge
(Design Principles §4). The human surface is one tap (Design Principles §11):
the *agent* produces the structured post and *proposes* the ★; the human only
accepts/rejects and may override the ★.

**C1 (oms.core.md:70/98; oms.forum.md:89):** a rejected ``/self-distill`` post
is **not persisted** — the agent is re-prompted. ``preference=accept|reject``
is distill-only (``/cross-distill``). This supersedes ``oms.cli.md:61``'s
"stores ``preference=reject``" text; M8 is the third defense (M3 model
validator + M6 parser + M8 never putting a post that carries ``preference``).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from rich.text import Text

from oms import __version__
from oms.bank import Bank, get_bank
from oms.utils import config, sid, ui

# --------------------------------------------------------------------------- #
# pure helpers (unit-testable in isolation, no I/O)
# --------------------------------------------------------------------------- #

# Session-lifecycle CLI verbs only. The four knowledge-loop verbs
# (self-distill / discuss / cross-distill / inject) are no longer CLI
# subcommands — they live exclusively as in-agent skills + MCP tools
# (oms._mcp + oms.adapters.skills.*). M11.4 ripped the bash surface;
# scripts/programmatic callers use ``oms._handlers.do_*`` directly.
_SUBCOMMANDS = {"start", "register", "end", "uninstall", "status"}


def preview_tokens(text: str, *, head: int, tail: int) -> str:
    """Head+tail token preview for the ``/inject`` human gate (the slice is
    load-bearing — the practitioner sees both ends, never a silent middle)."""
    toks = text.split()
    if len(toks) <= head + tail:
        return text
    elided = len(toks) - head - tail
    return f"{' '.join(toks[:head])} … [elided {elided} tokens] … {' '.join(toks[-tail:])}"


def _oms_home() -> Path:
    """``~/.oms`` (or ``OMS_HOME`` — tests point this at a tmp dir so the real
    home is never touched)."""
    return Path(os.environ.get("OMS_HOME", str(Path.home() / ".oms")))


def active_session_path() -> Path:
    return _oms_home() / "active"


def _read_active() -> str | None:
    p = active_session_path()
    return p.read_text(encoding="utf-8").strip() if p.is_file() else None


def _write_active(session_id: str) -> None:
    home = _oms_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "active").write_text(session_id, encoding="utf-8")


def _clear_active() -> None:
    p = active_session_path()
    if p.is_file():
        p.unlink()


def _resolve_sid(explicit: str | None) -> str:
    """``--session`` wins, else ``~/.oms/active``; error if neither."""
    s = explicit or _read_active()
    if not s:
        raise SystemExit("no active session: run `oms start` or pass --session <id>")
    return s


In = Callable[[str], str]
Out = Callable[[str], None]


def ask_rating(propose: int | None, *, input_fn: In, output_fn: Out, noninteractive: bool) -> int | None:
    """The ★ prompt. ``OMS_NONINTERACTIVE`` ⇒ unrated (no prompt). Else the
    agent's proposed value is the default; Enter accepts it, ``skip`` ⇒ unrated
    (a first-class valid state), a bare ``1``-``5`` overrides."""
    if noninteractive:
        return None
    prompt = (
        ui.render(
            Text.assemble(
                ("★ ", "bold yellow"),
                "rating 1-5 ",
                (f"[proposed={propose}]", "cyan"),
                (" (Enter=accept, 'skip'=unrated):", "dim"),
            )
        )
        + " "
    )
    raw = input_fn(prompt).strip().lower()
    if raw in ("skip", "s"):
        return None
    if raw == "":
        return propose
    if raw in ("1", "2", "3", "4", "5"):
        return int(raw)
    output_fn("  (unrecognized — leaving unrated)")
    return None


def ask_yn(prompt: str, *, input_fn: In, output_fn: Out, noninteractive: bool) -> bool:
    """A ``[y/n]`` gate. ``OMS_NONINTERACTIVE`` ⇒ deny-by-default (Open-Q §B5):
    no inject, no destructive confirm without a human present."""
    if noninteractive:
        output_fn(f"  (OMS_NONINTERACTIVE: '{prompt}' → denied)")
        return False
    styled = ui.render(Text.assemble((prompt, "bold"), (" [y/n]:", "dim"))) + " "
    return input_fn(styled).strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------- #
# two-stage SIGINT (datasmith precedent: oms wraps a live child agent)
# --------------------------------------------------------------------------- #

_sigint_count = 0


def _sigint_handler(signum: int, frame: object) -> None:
    """First Ctrl-C: SIGTERM tracked agents, raise KeyboardInterrupt. Second:
    SIGKILL and force-exit. Agents run in their own session
    (``start_new_session=True``), so without this they would not get the
    terminal's SIGINT and worker waits would hang."""
    global _sigint_count
    _sigint_count += 1
    from oms.adapters import terminate_all_agents

    if _sigint_count >= 2:
        print("oms: force-killing agent subprocesses", file=sys.stderr)
        terminate_all_agents(force=True)
        os._exit(1)
    print("oms: interrupted — terminating agents (Ctrl-C again to force-quit)", file=sys.stderr)
    terminate_all_agents(force=False)
    raise KeyboardInterrupt


# --------------------------------------------------------------------------- #
# verb handlers (async; main() drives them with asyncio.run per dispatch).
# Adapter / agent / headless-model helpers moved to ``oms._handlers``.
# --------------------------------------------------------------------------- #


def _session_url(session_id: str) -> str:
    """Build the viewer URL for a session id from the web bind config.

    `0.0.0.0` is a wildcard bind, not a reachable address — display as
    `127.0.0.1` so the line is clickable when copy/pasted."""
    host = config.resolve("OMS_WEB_HOST", config.OMS_WEB_HOST) or "127.0.0.1"
    if host == "0.0.0.0":  # noqa: S104 — defensive REWRITE of an unreachable wildcard, not a bind
        host = "127.0.0.1"
    port = int(config.resolve("OMS_WEB_PORT", config.OMS_WEB_PORT, cast=int))
    return f"http://{host}:{port}/s/{session_id}"


def _agent_url(agent_id: str) -> str:
    """Build the per-agent deep link URL. ``agent_id`` is the canonical
    ``{session}/agent-{NNN}-{adapter}``; the URL is ``…/s/{session}/a/{tail}``,
    matching the ``oms.web`` route convention (full id = ``{session}/{tail}``)."""
    session_id, _, tail = agent_id.partition("/")
    return f"{_session_url(session_id)}/a/{tail}"


async def _do_start(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    session_id = args.id or sid.new()
    await bank.put_session(session_id, goal=args.goal)
    _write_active(session_id)
    line = Text.assemble(("session ", "dim"), (session_id, "bold"))
    if args.goal:
        line.append(f"  goal={args.goal!r}", style="dim")
    io[1](ui.render(line))
    io[1](ui.render(Text.assemble(("open: ", "dim"), (_session_url(session_id), "underline cyan"))))
    return 0


async def _do_register(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    from oms._handlers import _resolve_agent

    sid_ = _resolve_sid(args.session)
    agent_id = await _resolve_agent(sid_, args.name, bank=bank)
    io[1](ui.render(Text.assemble(("registered ", "green"), (agent_id, "bold"))))
    io[1](ui.render(Text.assemble(("open: ", "dim"), (_agent_url(agent_id), "underline cyan"))))
    return 0


async def _do_run_agent(
    name: str, agent_args: list[str], session: str | None, *, bank: Bank, io: tuple[In, Out]
) -> int:
    from oms._handlers import _adapter_for, _resolve_agent

    sid_ = _resolve_sid(session)
    agent_id = await _resolve_agent(sid_, name, bank=bank)
    adapter = _adapter_for(name, session_id=sid_, agent_id=agent_id)

    # M11: install in-agent skills before spawning so /self-distill (etc.) is
    # available the moment the user lands in the agent UI. Idempotent; the
    # consent prompt fires once on first install, then runs silently. Skipped
    # cleanly if the adapter has no installer (default ABC: returns None).
    home = _oms_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        adapter.install_skills(session_id=sid_, oma_home=home, scope="user")
    except Exception as exc:
        io[1](
            ui.render(
                Text(f"oms: skill install skipped ({type(exc).__name__}: {exc}) — see `oms status`", style="yellow")
            )
        )

    # Thread OMS_SESSION into the child so the MCP server (spawned by the
    # agent) can resolve the active session even without ~/.oms/active.
    os.environ["OMS_SESSION"] = sid_

    argv = [adapter.binary, *agent_args]
    io[1](ui.render(Text.assemble(("oms: running ", "dim"), (" ".join(argv), "bold"), (f" (session {sid_})", "dim"))))

    # M11.6: tee the PTY master output to a tempfile, then build a
    # CanonicalTrace from it and run the M4 capture pipeline (validate →
    # scrub → bound → persist as a `raw` packet). This finally closes the
    # M8 "capture plumbing (trace tee) is M10 integration" deferral. The
    # adapter's own `capture()` was for the headless-subprocess path
    # (`adapter.invoke()` + pipe); the PTY path needs its own tee since the
    # master fd is the only thing carrying what the wrapped agent emitted.
    import tempfile

    tee_fd, tee_name = tempfile.mkstemp(prefix=f"oms-tee-{sid_}-", suffix=".log")
    os.close(tee_fd)
    tee_path = Path(tee_name)
    try:
        _pty_spawn(argv, tee=tee_path)
        tee_bytes = tee_path.read_bytes() if tee_path.is_file() else b""
    finally:
        tee_path.unlink(missing_ok=True)

    try:
        from oms.capture import persist
        from oms.capture.models import CanonicalTrace, TraceEvent

        trace = CanonicalTrace(
            session_id=sid_,
            agent_id=agent_id,
            adapter=name,
            events=[TraceEvent(ts=0.0, kind="system", text=tee_bytes.decode("utf-8", errors="replace"))],
            source_fidelity="pty",
            bytes_in=len(tee_bytes),
        )
        pid = await persist(trace, bank=bank)
        io[1](
            ui.render(
                Text.assemble(
                    ("oms: captured raw packet ", "green"), (pid, "bold"), (f" ({len(tee_bytes):,} bytes)", "dim")
                )
            )
        )
    except Exception as exc:
        io[1](ui.render(Text(f"oms: trace capture failed ({type(exc).__name__}: {exc})", style="red")))
    return 0


async def _do_uninstall(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``oms uninstall <adapter>`` — reverse the install via the saved manifest.
    Created files are removed iff still matching what we wrote; merged files
    have only our keys popped (third-party MCP servers etc. survive); any
    external CLI registrations (``claude mcp add`` etc.) are reversed by
    their recorded inverse command."""
    from oms._installer import uninstall

    rc = uninstall(args.adapter, _oms_home(), output_fn=io[1])
    if rc == 0:
        io[1]("")
        io[1](
            f"oms: skill files are gone from disk; if {args.adapter} is currently "
            "running, restart it so its slash menu refreshes (additions are live; "
            "removals are cached until session restart)."
        )
    return rc


async def _do_status(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``oms status`` — list every adapter that currently has an in-agent
    install (skills + MCP server entry) along with the files it owns."""
    from oms._installer import list_installed

    manifests = list_installed(_oms_home())
    if not manifests:
        io[1]("oms: no in-agent skills installed (run `oms <adapter>` to install)")
        return 0
    for m in manifests:
        io[1]("")
        io[1](
            ui.render(
                Text.assemble((m.adapter, "bold magenta"), (f"  scope {m.scope} · installed {m.installed_at}", "dim"))
            )
        )
        for e in m.entries:
            # the verb word, not a bare sigil: `~ ~/.codex/config.toml` would
            # read as two indistinguishable tildes, and status has no legend.
            verb = ("+ create", "bold green") if e.kind == "create" else ("~ merge ", "bold yellow")
            line = Text.assemble("  ", verb, " ", ui.tilde(e.path))
            if e.merge_keys:
                line.append(f"  keys={e.merge_keys}", style="dim")
            io[1](ui.render(line))
    return 0


def _pty_spawn(argv: list[str], *, tee: Path | None = None) -> None:  # noqa: C901 — a PTY bridge is irreducibly stateful
    """Run ``argv`` under a PTY that **inherits the parent terminal's size**
    and forwards ``SIGWINCH`` so live resizes flow through. The stdlib's
    ``pty.spawn`` leaves the child PTY at the kernel default 0x0 — TUI hosts
    (Claude Code's Ink/React-Terminal, Gemini's blessed) misread that as
    80x24 and render in a fixed 80-column window (the user-reported uncanny
    smallness). M11 reimplements with ``pty.fork`` + ``TIOCSWINSZ``.

    Monkeypatched in unit tests; the real path is exercised by ``oms <name>``.

    **POSIX only.** Windows has no ``pty``/``fcntl``/``termios``/``tty``; on
    Windows we raise a clear ``NotImplementedError`` rather than silently
    fall back to a sized-wrong subprocess. Native Windows PTY support
    (ConPTY via ``winpty`` / ``pywinpty``) is a future deliverable.
    """
    if sys.platform == "win32":  # pragma: no cover — checked on Windows CI
        raise NotImplementedError(
            "oms's PTY wrapper is POSIX-only (Windows lacks pty/fcntl/termios). "
            "Run the wrapped agent directly (e.g. `claude`); /self-distill etc. "
            "still work via the MCP server once `oms <name>` installed the skills."
        )

    import contextlib
    import fcntl
    import pty
    import select
    import termios
    import tty

    # Non-interactive callers (tests, redirected stdin) can't be a TTY host;
    # fall back to a direct exec so no PTY shenanigans break them.
    if not sys.stdin.isatty():
        os.execvp(argv[0], argv)  # noqa: S606 — exec'ing the user-named adapter binary is the point
        return  # unreachable, but mypy doesn't know

    pid, master_fd = pty.fork()
    if pid == 0:  # child
        os.execvp(argv[0], argv)  # noqa: S606 — same
        return  # unreachable

    # parent: copy our winsize to the child PTY, and re-sync on every SIGWINCH.
    def _sync_winsize(*_: object) -> None:
        try:
            sz = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, sz)
        except OSError:
            pass  # best-effort; a closed terminal shouldn't bring us down

    _sync_winsize()
    signal.signal(signal.SIGWINCH, _sync_winsize)

    # Optional tee: every byte the master fd emits is also written to ``tee``,
    # so ``_do_run_agent`` can persist a ``raw`` packet via the M4 capture
    # pipeline (closes the M8 "capture plumbing (trace tee) is M10 integration"
    # deferral — M11.6).
    tee_fd: int | None = None
    if tee is not None:
        with contextlib.suppress(OSError):
            tee_fd = os.open(str(tee), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    # Bridge stdin <-> master in raw mode so the child sees keystrokes (not
    # cooked lines). Restore terminal attrs on the way out no matter what.
    old_attrs = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            try:
                rfds, _, _ = select.select([master_fd, sys.stdin], [], [])
            except (OSError, InterruptedError):
                continue
            if master_fd in rfds:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                if tee_fd is not None:
                    with contextlib.suppress(OSError):
                        os.write(tee_fd, data)
            if sys.stdin in rfds:
                try:
                    data = os.read(sys.stdin.fileno(), 65536)
                except OSError:
                    break
                if not data:
                    break
                os.write(master_fd, data)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSANOW, old_attrs)
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        if tee_fd is not None:
            with contextlib.suppress(OSError):
                os.close(tee_fd)
        with contextlib.suppress(OSError):
            os.waitpid(pid, 0)


# M11.4: the four knowledge-loop verbs moved to ``oms._handlers``. They are
# no longer CLI subcommands — only the session-lifecycle verbs (start /
# register / <name> / end / status / uninstall) remain on this surface.
# The user-facing path is in-agent skills + the MCP server (oms._mcp).


async def _do_end(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    sid_ = _resolve_sid(args.session)
    await bank.put_session(sid_, status="ended")
    # ★ moment: oms.core has no sessions.rating column, so oms end's ★ lands on
    # the most recent UNRATED reflection post in the session (no-op if none) —
    # avoids an unneeded oms.bank migration (M8 Decision-log).
    posts = await bank.list_packets(session_id=sid_, type="post")
    unrated = [p for p in posts if p.get("kind") == "reflection" and p.get("rating") is None]
    if unrated:
        last = unrated[-1]
        rating = ask_rating(3, input_fn=io[0], output_fn=io[1], noninteractive=_noninteractive())
        if rating is not None:
            last["rating"] = rating
            last.pop("preference", None)  # C1
            await bank.put_packet(last)
            io[1](
                ui.render(Text.assemble(("rated ", "green"), (str(last["id"]), "bold"), (f" ★{rating}", "bold yellow")))
            )
    _clear_active()
    io[1](ui.render(Text.assemble(("session ", "dim"), (sid_, "bold"), (" ended", "dim"))))
    return 0


def _noninteractive() -> bool:
    return config.resolve("OMS_NONINTERACTIVE", False, cast=config.as_bool)


# --------------------------------------------------------------------------- #
# argparse + sniffing dispatch
# --------------------------------------------------------------------------- #

_EPILOG = """\
session lifecycle (the only verbs on this CLI):
  oms start [id] [--goal "..."]   start/join a session (writes ~/.oms/active)
  oms register <name>             register an adapter as an Agent
  oms <name> [args...]            install in-agent skills + run the wrapped agent under a PTY
  oms end [--session id]          end the session (optional ★ prompt)
  oms status                      list installed in-agent skill bundles
  oms uninstall <adapter>         reverse the install via the saved manifest

knowledge-loop verbs (typed INSIDE the wrapped agent, not on this CLI):
  /self-distill                   (Claude Code, Gemini CLI)
  /discuss [@packet] [stance]
  /cross-distill
  /inject [@packet]
  $self-distill, $discuss, ...    (Codex CLI — `/` is reserved for built-ins)

Skills + MCP server install on `oms <name>`; the human surface stays one tap
(Design Principles §11): the agent generates the structured post, proposes
the ★, and your in-agent permission prompt is the accept gate (C1).
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oms",
        description="Wrap installed coding-agent CLIs; curate cross-session knowledge.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"oms {__version__}")
    sub = p.add_subparsers(dest="verb")

    s = sub.add_parser("start")
    s.add_argument("id", nargs="?")
    s.add_argument("--goal")

    r = sub.add_parser("register")
    r.add_argument("name")
    r.add_argument("--session")

    # M11.4: self-distill / discuss / cross-distill / inject are NOT CLI
    # subcommands — they're installed as in-agent skills + MCP tools by
    # ``oms <name>``. Programmatic callers use ``oms._handlers.do_*`` directly.

    e = sub.add_parser("end")
    e.add_argument("--session")

    # M11: in-agent install lifecycle. `oms <name>` runs the install
    # implicitly; these expose the inspect + reverse paths.
    us = sub.add_parser("uninstall")
    us.add_argument("adapter")

    sub.add_parser("status")
    return p


_DISPATCH: dict[str, Callable[..., Coroutine[Any, Any, int]]] = {
    "start": _do_start,
    "register": _do_register,
    "uninstall": _do_uninstall,
    "status": _do_status,
    "end": _do_end,
}


def _guard(coro: Coroutine[Any, Any, int]) -> int:
    """Run a verb coroutine and translate operational failures into a concise,
    actionable message — the dumb CLI must never dump a Python traceback for an
    expected misconfiguration (a missing Bank key, an unreachable Bank). That
    is what ``python -m oms.preflight`` diagnoses; here we just fail cleanly and
    point at it. ``OMS_DEBUG=1`` re-raises for developers."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        print("oms: interrupted", file=sys.stderr)
        return 130
    except SystemExit as exc:
        # Our own clean one-liners (e.g. _resolve_sid / _adapter_for) carry a
        # string; argparse/explicit numeric exits are preserved verbatim.
        if isinstance(exc.code, int) or exc.code is None:
            raise
        print(f"oms: {exc.code}", file=sys.stderr)
        return 1
    except Exception as exc:
        if config.resolve("OMS_DEBUG", False, cast=config.as_bool):
            raise
        print(f"oms: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "oms: run `python -m oms.preflight` to check env/Bank/keys — set "
            "OMS_BANK_TRUSTED_KEY in oms.env, or start a local Bank with "
            "`make bank-up`. Set OMS_DEBUG=1 for a full traceback.",
            file=sys.stderr,
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    """Console-script entrypoint. Returns a process exit code."""
    raw = list(argv) if argv is not None else sys.argv[1:]
    io: tuple[In, Out] = (input, print)

    if not raw:
        _build_parser().print_help()
        return 0

    signal.signal(signal.SIGINT, _sigint_handler)
    first = raw[0]

    # Not a known verb and not a flag → `oms <name> [args]` (run an agent).
    if first not in _SUBCOMMANDS and not first.startswith("-"):
        return _guard(_do_run_agent(first, raw[1:], None, bank=get_bank(), io=io))

    parser = _build_parser()
    args = parser.parse_args(raw)
    if not getattr(args, "verb", None):
        parser.print_help()
        return 0
    return _guard(_DISPATCH[args.verb](args, bank=get_bank(), io=io))


if __name__ == "__main__":
    raise SystemExit(main())
