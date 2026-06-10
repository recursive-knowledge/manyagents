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
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from rich.text import Text

from oms import __version__
from oms.bank import Bank, get_bank
from oms.utils import config, messages, sid, ui

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
    return Path(os.environ.get("OMS_HOME", str(Path.home() / ".oms"))).expanduser()


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
                (" " + messages.RATING_HINT, "dim"),
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
    output_fn(messages.RATING_UNRECOGNIZED)
    return None


def ask_yn(prompt: str, *, input_fn: In, output_fn: Out, noninteractive: bool) -> bool:
    """A ``[y/n]`` gate. ``OMS_NONINTERACTIVE`` ⇒ deny-by-default (Open-Q §B5):
    no inject, no destructive confirm without a human present."""
    if noninteractive:
        output_fn(messages.NONINTERACTIVE_DENIED.format(prompt=prompt))
        return False
    styled = ui.render(Text.assemble((prompt, "bold"), (" [y/n]:", "dim"))) + " "
    return input_fn(styled).strip().lower() in ("y", "yes")


_DECLINE = ("n", "no", "q", "esc", "escape")


def ask_allow(prompt: str, *, input_fn: In, output_fn: Out, noninteractive: bool) -> bool:
    """A single allowance gate: **Enter allows**, ``n``/``esc`` declines.
    Replaces accept/reject two-way questions (user decision 2026-06-10:
    every gate is one binary allowance, affirmative by default). In
    ``OMS_NONINTERACTIVE`` it stays deny-by-default like :func:`ask_yn`
    (Open-Q §B5) — affirmative defaults are for present humans only."""
    if noninteractive:
        output_fn(messages.NONINTERACTIVE_DENIED.format(prompt=prompt))
        return False
    styled = ui.render(Text.assemble((prompt, "bold"), (messages.ALLOW_SUFFIX, "dim"))) + " "
    return input_fn(styled).strip().lower() not in _DECLINE


def ask_commit(propose: int | None, *, input_fn: In, output_fn: Out, noninteractive: bool) -> tuple[bool, int | None]:
    """The single commit gate for a parser-validated post: allowance and ★ in
    one prompt. Enter ⇒ commit with the proposed ★; a bare ``1``-``5`` ⇒
    commit with that ★; ``skip`` ⇒ commit unrated; ``n``/``esc`` ⇒ discard
    (C1: nothing persisted). ``OMS_NONINTERACTIVE`` ⇒ auto-commit unrated
    (the mechanical parser already gated quality — oms.cli.md)."""
    if noninteractive:
        return True, None
    # On a real terminal the gate is the ★ number-line picker (arrows / 1-5 /
    # Enter / s / n-Esc). Scripted callers (tests, Simulation) pass their own
    # input_fn and get the typed one-liner instead.
    if input_fn is input and sys.stdin.isatty() and sys.stdout.isatty():
        return ui.pick_star(propose or 3)
    prompt = (
        ui.render(
            Text.assemble(
                (messages.COMMIT_QUESTION + " ", "bold"),
                (messages.COMMIT_TYPED_HINT.format(propose=propose), "dim"),
            )
        )
        + " "
    )
    raw = input_fn(prompt).strip().lower()
    if raw in _DECLINE:
        return False, None
    if raw in ("skip", "s"):
        return True, None
    if raw in ("1", "2", "3", "4", "5"):
        return True, int(raw)
    if raw == "":
        return True, propose
    output_fn(messages.COMMIT_UNRECOGNIZED)
    return True, None


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
    """Build the viewer URL for a session id.

    The hosted viewer's base URL (`OMS_WEB_PUBLIC_URL`, default
    swarms.formulacode.org) wins; set it EMPTY to fall back to the local bind
    config (`OMS_WEB_HOST`/`OMS_WEB_PORT`) for local dev. In the fallback,
    `0.0.0.0` is a wildcard bind, not a reachable address — display as
    `127.0.0.1` so the line is clickable when copy/pasted."""
    base = config.resolve("OMS_WEB_PUBLIC_URL", config.OMS_WEB_PUBLIC_URL).strip().rstrip("/")
    if base:
        return f"{base}/s/{session_id}"
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
    # Sensible defaults (2026-06-10): the session-start moments — goal
    # continuity, quarantine visibility, the stale-goal cross-distill nudge,
    # and the inject offer. All best-effort: never block `oms start`.
    try:
        default_goal = config.resolve("OMS_DEFAULT_GOAL", config.OMS_DEFAULT_GOAL)
        goal = args.goal or await _offer_goal_continuity(session_id, bank=bank, io=io)
        if not goal:
            # Every session carries a goal: no goal given and continuity
            # declined (or noninteractive) files the session under the
            # default bucket (2026-06-10: goal-first positional).
            goal = default_goal
            await bank.put_session(session_id, goal=goal)
            io[1](ui.render(Text(messages.START_DEFAULT_GOAL_NOTE.format(goal=goal), style="dim")))
        if goal != default_goal:
            # The default bucket is the catch-all, not a curated goal — the
            # goal-scoped offers would never converge for it (its cross-goal
            # bundles carry no goal, so e.g. the cross nudge would re-fire
            # on every start).
            await _note_quarantine(goal, bank=bank, io=io)
            await _offer_cross_nudge(session_id, goal, bank=bank, io=io)
            await _offer_goal_context(session_id, goal, bank=bank, io=io)
    except Exception as exc:
        io[1](ui.render(Text(f"oms: start-time offers skipped ({type(exc).__name__}: {exc})", style="yellow")))
    return 0


async def _offer_goal_continuity(session_id: str, *, bank: Bank, io: tuple[In, Out]) -> str | None:
    """`oms start` without a goal argument: when the most recent other session
    carried a real goal (not the default bucket), offer to continue it (one
    allowance gate). Returns the adopted goal, or None."""
    if _noninteractive():
        return None
    default_goal = config.resolve("OMS_DEFAULT_GOAL", config.OMS_DEFAULT_GOAL)
    sessions = [
        s
        for s in await bank.list_sessions()
        if s.get("id") != session_id and s.get("goal") and s.get("goal") != default_goal
    ]
    if not sessions:
        return None
    sessions.sort(key=lambda s: str(s.get("created_at") or ""))
    last_goal = str(sessions[-1]["goal"])
    if not ask_allow(
        messages.START_CONTINUE_GOAL_OFFER.format(goal=last_goal),
        input_fn=io[0],
        output_fn=io[1],
        noninteractive=False,
    ):
        return None
    await bank.put_session(session_id, goal=last_goal)
    io[1](ui.render(Text.assemble(("goal ", "dim"), (f"/{last_goal}", "bold"), (" adopted", "dim"))))
    return last_goal


async def _note_quarantine(goal: str, *, bank: Bank, io: tuple[In, Out]) -> None:
    """One informational line (no gate) when the goal has quarantined packets
    awaiting review."""
    packets = await bank.list_packets(goal=goal, include_quarantined=True)
    n = sum(1 for p in packets if p.get("quarantined"))
    if n:
        io[1](
            ui.render(
                Text(
                    "⚠ " + messages.START_QUARANTINE_NOTE.format(n=n, n_s="s" if n != 1 else "", goal=goal),
                    style="yellow",
                )
            )
        )


async def _offer_cross_nudge(session_id: str, goal: str, *, bank: Bank, io: tuple[In, Out]) -> None:
    """Stale-goal nudge (replaces the end-of-session cross-distill offer):
    when ≥ OMS_CROSS_NUDGE_MIN reflections accumulated under the goal SINCE
    its newest bundle (or with no bundle at all), offer cross-distillation at
    start — the moment the fresh bundle is about to be useful. Counting only
    newer-than-bundle reflections is what prevents back-to-back re-runs."""
    if _noninteractive():
        return
    reflections = [
        p
        for p in await bank.list_packets(type="post", goal=goal, include_quarantined=False)
        if p.get("kind") == "reflection"
    ]
    bundles = [p for p in await bank.list_packets(type="distill", include_quarantined=False) if p.get("goal") == goal]
    newest_bundle_ts = max((str(b.get("created_at") or "") for b in bundles), default="")
    fresh = [r for r in reflections if str(r.get("created_at") or "") > newest_bundle_ts]
    if len(fresh) < config.OMS_CROSS_NUDGE_MIN:
        return
    if ask_allow(
        messages.START_CROSS_NUDGE_OFFER.format(goal=goal, n=len(fresh), n_s="s" if len(fresh) != 1 else ""),
        input_fn=io[0],
        output_fn=io[1],
        noninteractive=False,
    ):
        from oms._handlers import do_cross_distill

        await do_cross_distill(session=session_id, bank=bank, io=io)


def _inject_stash_path(session_id: str) -> Path:
    return _oms_home() / "inject" / f"{session_id}.json"


def _distill_declined_path(session_id: str) -> Path:
    """Marker: the user declined the session's distill offer once — don't ask
    again for this session (it would otherwise re-fire at `oms end` after an
    agent-exit decline). Cleared with the session."""
    return _oms_home() / "offers" / f"{session_id}.distill-declined"


async def _offer_goal_context(session_id: str, goal: str, *, bank: Bank, io: tuple[In, Out]) -> None:
    """Session-start inject offer: when the goal already has curated bundles,
    show how much prior knowledge exists and ask once (Enter=inject). On
    allow: write the injection-ledger row AND stash the bundle under
    ``$OMS_HOME/inject/<sid>.json`` so the SessionStart harness hook
    (``oms._hook``) can deliver it into the agent's context. Silent no-op in
    ``OMS_NONINTERACTIVE`` (deny-by-default; never auto-inject — Open-Q §B5)."""
    if _noninteractive():
        return
    distills = [
        p for p in await bank.list_packets(type="distill", include_quarantined=False) if (p.get("goal") or None) == goal
    ]
    if not distills:
        return
    posts = [p for p in await bank.list_packets(type="post") if (p.get("goal") or None) == goal]
    latest = distills[-1]
    io[1](
        ui.render(
            Text.assemble(
                ("◆ ", "green"),
                (f"/{goal}", "bold"),
                (
                    f" already has {len(distills)} bundle{'s' if len(distills) != 1 else ''}"
                    f" · {len(posts)} post{'s' if len(posts) != 1 else ''}",
                    "dim",
                ),
            )
        )
    )
    if not ask_allow(
        messages.START_INJECT_OFFER.format(packet_id=latest["id"]),
        input_fn=io[0],
        output_fn=io[1],
        noninteractive=False,
    ):
        return
    pid = str(latest["id"])
    await bank.record_injection(pid, session_id)
    bundle_text = json.dumps(latest.get("bundle", {}), indent=2)
    stash = _inject_stash_path(session_id)
    stash.parent.mkdir(parents=True, exist_ok=True)
    stash.write_text(
        json.dumps({"packet_id": pid, "goal": goal, "bundle": latest.get("bundle", {})}, indent=2),
        encoding="utf-8",
    )
    io[1](
        preview_tokens(
            bundle_text,
            head=config.OMS_INJECT_PREVIEW_HEAD_TOKENS,
            tail=config.OMS_INJECT_PREVIEW_TAIL_TOKENS,
        )
    )
    io[1](ui.render(Text(messages.START_INJECTED_NOTE.format(packet_id=pid), style="green")))


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
    run_started = time.time()
    try:
        # Monkeypatched test/Simulation stubs return None — treat as 0.
        agent_rc = _pty_spawn(argv, tee=tee_path) or 0
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

    # M12 groundwork: surface what the lifecycle hooks bound during this run
    # (harness session ids + transcript paths appended by `oms._hook`). One
    # PTY run can span several harness sessions (`/clear` rolls a fresh id).
    # Consumed for real by Adapter.mine() in M13.
    bindings = _harness_bindings(sid_, since=run_started)
    if bindings:
        ids = sorted({str(b.get("harness_session_id") or "?") for b in bindings})
        io[1](
            ui.render(
                Text.assemble(
                    ("oms: harness session(s) bound: ", "dim"),
                    (", ".join(ids), "bold"),
                    (f" ({len(bindings)} hook event(s))", "dim"),
                )
            )
        )
    # The session-close moments fire HERE (2026-06-10): the wrapped agent
    # exiting is the natural "I'm done working" point — first the distill
    # offer, then the offer to end the session itself, so the whole loop can
    # close without anyone remembering `oms end` (which remains the fallback
    # and does the same things). Clean exits only (after a crash or Ctrl-C
    # the user is dealing with the failure, not reflecting on it).
    if agent_rc == 0 and not _noninteractive():
        try:
            await _offer_end_distill(sid_, bank=bank, io=io)
            if ask_allow(
                messages.AGENT_EXIT_END_OFFER.format(session_id=sid_),
                input_fn=io[0],
                output_fn=io[1],
                noninteractive=False,
            ):
                await _do_end(argparse.Namespace(session=sid_), bank=bank, io=io)
        except Exception as exc:
            io[1](ui.render(Text(f"oms: session-close offers skipped ({type(exc).__name__}: {exc})", style="yellow")))
    # The agent's own exit code is the run's exit code (the pre-M12 execvp
    # contract for scripted/CI callers) — capture happens either way above.
    return agent_rc


def _harness_bindings(sid_: str, *, since: float) -> list[dict[str, object]]:
    """Binding records ``oms._hook`` appended to
    ``$OMS_HOME/bindings/<sid>.jsonl`` during this run (``ts >= since``).
    Defensive parse: a malformed line never breaks the wrapper."""
    p = _oms_home() / "bindings" / f"{sid_}.jsonl"
    if not p.is_file():
        return []
    out: list[dict[str, object]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and float(rec.get("ts") or 0.0) >= since:
                out.append(rec)
        except (ValueError, TypeError):
            continue
    return out


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


def _shell_exit_code(rc: int) -> int:
    """Map a negative signal status (the ``Popen``/``waitstatus`` convention)
    to the shell's ``128+N`` so ``oms`` exits like the agent did."""
    return rc if rc >= 0 else 128 - rc


def _pipe_spawn(argv: list[str], *, tee: Path | None = None) -> int:  # noqa: C901 — pump + cleanup are irreducibly stateful
    """Non-PTY fallback for redirected stdin: run ``argv`` as a plain
    subprocess — stdin and the controlling terminal are inherited so piped
    prompts flow to the child naturally — and pump its merged stdout+stderr
    to our stdout, tee'd to ``tee`` exactly like the PTY path. The child sees
    a pipe (not a TTY), which is what a headless ``claude -p``-style caller
    expects; merging stderr mirrors what a PTY would have done (both streams
    land on the same terminal, and both belong in the captured trace).
    Returns the child's exit code — the old ``execvp`` fallback's contract;
    scripted/CI callers (exactly who hits this branch) read it.

    Two non-obvious choices: the pump is **poll-aware** (EOF on the merged
    pipe requires every inherited copy of the write end to close, so an
    agent that backgrounds a daemon would otherwise pin us long after it
    exited — once the child is gone and the pipe has drained, we stop), and
    the child runs in its own session, registered with the adapters' proc
    registry so the two-stage SIGINT handler can reach it; the ``finally``
    reaps it on every exit path (a first Ctrl-C raises KeyboardInterrupt
    out of the pump mid-read)."""
    import contextlib
    import select
    import subprocess
    from typing import cast

    from oms.adapters.base import _register_proc, _unregister_proc

    tee_fd: int | None = None
    if tee is not None:
        with contextlib.suppress(OSError):
            tee_fd = os.open(str(tee), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _register_proc(cast("subprocess.Popen[str]", proc))  # the registry only uses .pid
        assert proc.stdout is not None  # noqa: S101 — invariant of stdout=PIPE
        out_fd = proc.stdout.fileno()
        gone_at: float | None = None
        while True:
            try:
                ready, _, _ = select.select([out_fd], [], [], 0.25)
            except OSError:
                break
            if ready:
                try:
                    data = os.read(out_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                # Keep pumping into the tee even if our own stdout goes away
                # (downstream pipe closed) — the capture should survive.
                with contextlib.suppress(OSError):
                    os.write(sys.stdout.fileno(), data)
                if tee_fd is not None:
                    with contextlib.suppress(OSError):
                        os.write(tee_fd, data)
            elif gone_at is not None:
                break  # child exited and the pipe has drained
            if gone_at is None:
                if proc.poll() is not None:
                    gone_at = time.monotonic()
            elif time.monotonic() - gone_at > 5.0:
                break  # bound post-exit draining (a chatty backgrounded daemon)
        return _shell_exit_code(proc.wait())
    finally:
        if proc is not None:
            if proc.poll() is None:  # interrupted mid-pump — don't leak the child
                with contextlib.suppress(OSError):
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    with contextlib.suppress(OSError):
                        proc.kill()
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        proc.wait(timeout=5)
            _unregister_proc(cast("subprocess.Popen[str]", proc))
        if tee_fd is not None:
            with contextlib.suppress(OSError):
                os.close(tee_fd)


def _pty_spawn(argv: list[str], *, tee: Path | None = None) -> int:  # noqa: C901 — a PTY bridge is irreducibly stateful
    """Run ``argv`` under a PTY that **inherits the parent terminal's size**
    and forwards ``SIGWINCH`` so live resizes flow through. The stdlib's
    ``pty.spawn`` leaves the child PTY at the kernel default 0x0 — TUI hosts
    (Claude Code's Ink/React-Terminal, Gemini's blessed) misread that as
    80x24 and render in a fixed 80-column window (the user-reported uncanny
    smallness). M11 reimplements with ``pty.fork`` + ``TIOCSWINSZ``.

    Returns the child's exit code (0 when it can't be determined).

    Monkeypatched in unit tests (the stubs return ``None``; the caller
    treats that as 0); the real path is exercised by ``oms <name>``.

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

    # Non-interactive callers (redirected stdin: pipes, CI, headless `-p`
    # runs) can't host a PTY bridge — but they still get capture. The old
    # fallback here was an `os.execvp` that REPLACED the oms process: the
    # wrapper vanished, nothing was tee'd, and `_do_run_agent` never reached
    # its capture/persist step, so wrapped non-interactive runs silently
    # produced no trace (the M11 non-TTY hole). Spawn-and-tee instead.
    if not sys.stdin.isatty():
        return _pipe_spawn(argv, tee=tee)

    pid, master_fd = pty.fork()
    if pid == 0:  # child
        os.execvp(argv[0], argv)  # noqa: S606 — exec'ing the user-named adapter binary is the point
        return 0  # unreachable, but mypy doesn't know

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
    exit_code = 0
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
            _, wait_status = os.waitpid(pid, 0)
            exit_code = _shell_exit_code(os.waitstatus_to_exitcode(wait_status))
    return exit_code


# M11.4: the four knowledge-loop verbs moved to ``oms._handlers``. They are
# no longer CLI subcommands — only the session-lifecycle verbs (start /
# register / <name> / end / status / uninstall) remain on this surface.
# The user-facing path is in-agent skills + the MCP server (oms._mcp).


async def _do_end(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    sid_ = _resolve_sid(args.session)
    # Sensible default (2026-06-10): before the session closes, offer the
    # distillation moments instead of relying on the human remembering the
    # verbs mid-session. One allowance gate each; never blocks `oms end`.
    try:
        await _offer_end_distill(sid_, bank=bank, io=io)
    except Exception as exc:
        io[1](ui.render(Text(f"oms: distill offer skipped ({type(exc).__name__}: {exc})", style="yellow")))
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
    _inject_stash_path(sid_).unlink(missing_ok=True)  # the hook stash dies with the session
    _distill_declined_path(sid_).unlink(missing_ok=True)
    io[1](ui.render(Text.assemble(("session ", "dim"), (sid_, "bold"), (" ended", "dim"))))
    return 0


async def _offer_end_distill(sid_: str, *, bank: Bank, io: tuple[In, Out]) -> None:
    """End-of-session self-distill offer (sensible default, 2026-06-10).

    Fires only when the session did work (an agent registered) but committed
    no reflection. When a bundle was INJECTED into this session, the offer
    becomes the injected-bundle follow-up: the drafted reflection is guided
    to evaluate whether the bundle held up, citing it via ``evidence_ref`` —
    the v1 of conflict-driven discussion (oms.procedures.md §9.2; a true
    cross-session ``disagree`` reply needs goal-scoped retrieve(), recorded
    there as the upgrade path).

    The cross-distill moment moved to ``oms start`` (`_offer_cross_nudge`) —
    a fresh bundle is useful at the START of the next session, and the
    newer-than-bundle counting there avoids immediate back-to-back re-runs.

    Silent no-op in ``OMS_NONINTERACTIVE`` — unattended runs never spend an
    LLM call or commit on a default."""
    if _noninteractive():
        return
    from oms._handlers import do_self_distill

    if _distill_declined_path(sid_).is_file():
        return  # asked at agent exit, declined — once per session is enough
    posts = await bank.list_packets(session_id=sid_, type="post")
    reflections = [p for p in posts if p.get("kind") == "reflection"]
    agents = await bank.list_agents(sid_)
    if reflections or not agents:
        return
    injections = await bank.list_injections(target_session_id=sid_)
    guidance: str | None = None
    if injections:
        injected_pid = str(injections[-1]["packet_id"])
        offer = messages.END_INJECT_FOLLOWUP_OFFER.format(packet_id=injected_pid)
        guidance = messages.END_INJECT_FOLLOWUP_GUIDANCE.format(packet_id=injected_pid)
    else:
        offer = messages.END_SELF_DISTILL_OFFER
    if ask_allow(offer, input_fn=io[0], output_fn=io[1], noninteractive=False):
        adapter = str(agents[-1].get("adapter") or "")
        if adapter:
            await do_self_distill(adapter=adapter, guidance=guidance, session=sid_, bank=bank, io=io)
    else:
        marker = _distill_declined_path(sid_)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()


def _noninteractive() -> bool:
    return config.resolve("OMS_NONINTERACTIVE", False, cast=config.as_bool)


# --------------------------------------------------------------------------- #
# argparse + sniffing dispatch
# --------------------------------------------------------------------------- #

_EPILOG = """\
session lifecycle (the only verbs on this CLI):
  oms start [goal] [--id XXXX-XXXX]  start/join a session (goal defaults to 'misc'; writes ~/.oms/active)
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
    s.add_argument("goal", nargs="?")
    s.add_argument("--id")

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
