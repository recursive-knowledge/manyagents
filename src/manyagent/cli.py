"""The single ``manyagent`` console-script entrypoint (M8; manyagent.cli.md).

A **dumb orchestrator**: it sequences ``manyagent.core`` + ``manyagent.adapters`` +
``manyagent.capture`` + ``manyagent.forum`` + ``manyagent.distill`` + ``manyagent.bank`` and owns no
domain logic — guards, schema, anti-meta, curator selection, reuse weighting
all live in modules so the CLI and a programmatic caller cannot diverge
(Design Principles §4). The human surface is one tap (Design Principles §11):
the *agent* produces the structured post and *proposes* the ★; the human only
accepts/rejects and may override the ★.

**C1 (manyagent.core.md:70/98; manyagent.forum.md:89):** a rejected ``/self-distill`` post
is **not persisted** — the agent is re-prompted. ``preference=accept|reject``
is distill-only (``/cross-distill``). This supersedes ``manyagent.cli.md:61``'s
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

from manyagent import __version__
from manyagent.bank import Bank, get_bank
from manyagent.utils import config, messages, sid, ui

# --------------------------------------------------------------------------- #
# pure helpers (unit-testable in isolation, no I/O)
# --------------------------------------------------------------------------- #

# Session-lifecycle CLI verbs only. The four knowledge-loop verbs
# (self-distill / discuss / cross-distill / inject) are no longer CLI
# subcommands — they live exclusively as in-agent skills + MCP tools
# (manyagent._mcp + manyagent.adapters.skills.*). M11.4 ripped the bash surface;
# scripts/programmatic callers use ``manyagent._handlers.do_*`` directly.
_SUBCOMMANDS = {"start", "register", "end", "uninstall", "status"}


def preview_tokens(text: str, *, head: int, tail: int) -> str:
    """Head+tail token preview for the ``/inject`` human gate (the slice is
    load-bearing — the practitioner sees both ends, never a silent middle)."""
    toks = text.split()
    if len(toks) <= head + tail:
        return text
    elided = len(toks) - head - tail
    return f"{' '.join(toks[:head])} … [elided {elided} tokens] … {' '.join(toks[-tail:])}"


def _manyagent_home() -> Path:
    """``~/.manyagent`` (or ``MANYAGENT_HOME`` — tests point this at a tmp dir so the real
    home is never touched)."""
    return Path(os.environ.get("MANYAGENT_HOME", str(Path.home() / ".manyagent"))).expanduser()


def active_session_path() -> Path:
    return _manyagent_home() / "active"


def _read_active() -> str | None:
    p = active_session_path()
    return p.read_text(encoding="utf-8").strip() if p.is_file() else None


def _write_active(session_id: str) -> None:
    home = _manyagent_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "active").write_text(session_id, encoding="utf-8")


def _clear_active() -> None:
    p = active_session_path()
    if p.is_file():
        p.unlink()


def _resolve_sid(explicit: str | None) -> str:
    """``--session`` wins, else ``~/.manyagent/active``; error if neither."""
    s = explicit or _read_active()
    if not s:
        raise SystemExit("no active session: run `manyagent start` or pass --session <id>")
    return s


In = Callable[[str], str]
Out = Callable[[str], None]


def ask_rating(propose: int | None, *, input_fn: In, output_fn: Out, noninteractive: bool) -> int | None:
    """The ★ prompt. ``MANYAGENT_NONINTERACTIVE`` ⇒ unrated (no prompt). Else the
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
    """A ``[y/n]`` gate. ``MANYAGENT_NONINTERACTIVE`` ⇒ deny-by-default (Open-Q §B5):
    no inject, no destructive confirm without a human present."""
    if noninteractive:
        output_fn(messages.NONINTERACTIVE_DENIED.format(prompt=prompt))
        return False
    styled = ui.render(Text.assemble((prompt, "bold"), (" [y/n]:", "dim"))) + " "
    return input_fn(styled).strip().lower() in ("y", "yes")


_DECLINE = ("n", "no", "q", "esc", "escape")


def ask_allow(prompt: str, *, input_fn: In, output_fn: Out, noninteractive: bool, detail: str | None = None) -> bool:
    """A single allowance gate: **Enter allows**, ``n``/``esc`` declines;
    when ``detail`` is given (a truncated preview's full text), ``d`` prints
    it and re-asks. Replaces accept/reject two-way questions (user decision
    2026-06-10: every gate is one binary allowance, affirmative by default).
    In ``MANYAGENT_NONINTERACTIVE`` it stays deny-by-default like :func:`ask_yn`
    (Open-Q §B5) — affirmative defaults are for present humans only."""
    if noninteractive:
        output_fn(messages.NONINTERACTIVE_DENIED.format(prompt=prompt))
        return False
    suffix = messages.ALLOW_SUFFIX_DETAIL if detail is not None else messages.ALLOW_SUFFIX
    styled = ui.render(Text.assemble((prompt, "bold"), (suffix, "dim"))) + " "
    while True:
        raw = input_fn(styled).strip().lower()
        if raw == "d" and detail is not None:
            output_fn(detail)
            continue
        return raw not in _DECLINE


def ask_commit(
    propose: int | None, *, input_fn: In, output_fn: Out, noninteractive: bool, detail: str | None = None
) -> tuple[bool, int | None]:
    """The single commit gate for a parser-validated post: allowance and ★ in
    one prompt. Enter ⇒ commit with the proposed ★; a bare ``1``-``5`` ⇒
    commit with that ★; ``skip`` ⇒ commit unrated; ``n``/``esc`` ⇒ discard
    (C1: nothing persisted); when ``detail`` is given (the post's preview was
    truncated), ``d`` ⇒ print the full post and re-ask. ``MANYAGENT_NONINTERACTIVE``
    ⇒ auto-commit unrated (the mechanical parser already gated quality —
    manyagent.cli.md)."""
    if noninteractive:
        return True, None
    # On a real terminal the gate is the ★ number-line picker (arrows / 1-5 /
    # Enter / s / d / n-Esc). Scripted callers (tests, Simulation) pass their
    # own input_fn and get the typed one-liner instead.
    if input_fn is input and sys.stdin.isatty() and sys.stdout.isatty():
        return ui.pick_star(propose or 3, detail=detail)
    hint = messages.COMMIT_TYPED_HINT_DETAIL if detail is not None else messages.COMMIT_TYPED_HINT
    prompt = (
        ui.render(
            Text.assemble(
                (messages.COMMIT_QUESTION + " ", "bold"),
                (hint.format(propose=propose), "dim"),
            )
        )
        + " "
    )
    while True:
        raw = input_fn(prompt).strip().lower()
        if raw == "d" and detail is not None:
            output_fn(detail)
            continue
        break
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
# two-stage SIGINT (datasmith precedent: manyagent wraps a live child agent)
# --------------------------------------------------------------------------- #

_sigint_count = 0


def _sigint_handler(signum: int, frame: object) -> None:
    """First Ctrl-C: SIGTERM tracked agents, raise KeyboardInterrupt. Second:
    SIGKILL and force-exit. Agents run in their own session
    (``start_new_session=True``), so without this they would not get the
    terminal's SIGINT and worker waits would hang."""
    global _sigint_count
    _sigint_count += 1
    from manyagent.adapters import terminate_all_agents

    if _sigint_count >= 2:
        print("manyagent: force-killing agent subprocesses", file=sys.stderr)
        terminate_all_agents(force=True)
        os._exit(1)
    print("manyagent: interrupted — terminating agents (Ctrl-C again to force-quit)", file=sys.stderr)
    terminate_all_agents(force=False)
    raise KeyboardInterrupt


# --------------------------------------------------------------------------- #
# verb handlers (async; main() drives them with asyncio.run per dispatch).
# Adapter / agent / headless-model helpers moved to ``manyagent._handlers``.
# --------------------------------------------------------------------------- #


def _session_url(session_id: str) -> str:
    """Build the viewer URL for a session id.

    The hosted viewer's base URL (`MANYAGENT_WEB_PUBLIC_URL`, default
    swarms.formulacode.org) wins; set it EMPTY to fall back to the local bind
    config (`MANYAGENT_WEB_HOST`/`MANYAGENT_WEB_PORT`) for local dev. In the fallback,
    `0.0.0.0` is a wildcard bind, not a reachable address — display as
    `127.0.0.1` so the line is clickable when copy/pasted."""
    base = config.resolve("MANYAGENT_WEB_PUBLIC_URL", config.MANYAGENT_WEB_PUBLIC_URL).strip().rstrip("/")
    if base:
        return f"{base}/s/{session_id}"
    host = config.resolve("MANYAGENT_WEB_HOST", config.MANYAGENT_WEB_HOST) or "127.0.0.1"
    if host == "0.0.0.0":  # noqa: S104 — defensive REWRITE of an unreachable wildcard, not a bind
        host = "127.0.0.1"
    port = int(config.resolve("MANYAGENT_WEB_PORT", config.MANYAGENT_WEB_PORT, cast=int))
    return f"http://{host}:{port}/s/{session_id}"


def _agent_url(agent_id: str) -> str:
    """Build the per-agent deep link URL. ``agent_id`` is the canonical
    ``{session}/agent-{NNN}-{adapter}``; the URL is ``…/s/{session}/a/{tail}``,
    matching the ``manyagent.web`` route convention (full id = ``{session}/{tail}``)."""
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
    # and the inject offer. All best-effort: never block `manyagent start`.
    try:
        default_goal = config.resolve("MANYAGENT_DEFAULT_GOAL", config.MANYAGENT_DEFAULT_GOAL)
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
        io[1](ui.render(Text(f"manyagent: start-time offers skipped ({type(exc).__name__}: {exc})", style="yellow")))
    return 0


async def _offer_goal_continuity(session_id: str, *, bank: Bank, io: tuple[In, Out]) -> str | None:
    """`manyagent start` without a goal argument: when the most recent other session
    carried a real goal (not the default bucket), offer to continue it (one
    allowance gate). Returns the adopted goal, or None."""
    if _noninteractive():
        return None
    default_goal = config.resolve("MANYAGENT_DEFAULT_GOAL", config.MANYAGENT_DEFAULT_GOAL)
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
    when ≥ MANYAGENT_CROSS_NUDGE_MIN reflections accumulated under the goal SINCE
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
    if len(fresh) < config.MANYAGENT_CROSS_NUDGE_MIN:
        return
    if ask_allow(
        messages.START_CROSS_NUDGE_OFFER.format(goal=goal, n=len(fresh), n_s="s" if len(fresh) != 1 else ""),
        input_fn=io[0],
        output_fn=io[1],
        noninteractive=False,
    ):
        from manyagent._handlers import do_cross_distill

        await do_cross_distill(session=session_id, bank=bank, io=io)


def _inject_stash_path(session_id: str) -> Path:
    return _manyagent_home() / "inject" / f"{session_id}.json"


async def _offer_goal_context(session_id: str, goal: str, *, bank: Bank, io: tuple[In, Out]) -> None:
    """Session-start inject offer: when the goal already has curated bundles,
    show how much prior knowledge exists and ask once (Enter=inject). On
    allow: write the injection-ledger row AND stash the bundle under
    ``$MANYAGENT_HOME/inject/<sid>.json`` so the SessionStart harness hook
    (``manyagent._hook``) can deliver it into the agent's context. Silent no-op in
    ``MANYAGENT_NONINTERACTIVE`` (deny-by-default; never auto-inject — Open-Q §B5)."""
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
            head=config.MANYAGENT_INJECT_PREVIEW_HEAD_TOKENS,
            tail=config.MANYAGENT_INJECT_PREVIEW_TAIL_TOKENS,
        )
    )
    io[1](ui.render(Text(messages.START_INJECTED_NOTE.format(packet_id=pid), style="green")))


async def _do_register(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    from manyagent._handlers import _resolve_agent

    sid_ = _resolve_sid(args.session)
    agent_id = await _resolve_agent(sid_, args.name, bank=bank)
    io[1](ui.render(Text.assemble(("registered ", "green"), (agent_id, "bold"))))
    io[1](ui.render(Text.assemble(("open: ", "dim"), (_agent_url(agent_id), "underline cyan"))))
    return 0


async def _do_run_agent(
    name: str, agent_args: list[str], session: str | None, *, bank: Bank, io: tuple[In, Out]
) -> int:
    from manyagent._handlers import _adapter_for, _resolve_agent

    sid_ = _resolve_sid(session)
    agent_id = await _resolve_agent(sid_, name, bank=bank)
    adapter = _adapter_for(name, session_id=sid_, agent_id=agent_id)

    # M11: install in-agent skills before spawning so /self-distill (etc.) is
    # available the moment the user lands in the agent UI. Idempotent; the
    # consent prompt fires once on first install, then runs silently. Skipped
    # cleanly if the adapter has no installer (default ABC: returns None).
    home = _manyagent_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        adapter.install_skills(session_id=sid_, oma_home=home, scope="user")
    except Exception as exc:
        io[1](
            ui.render(
                Text(
                    f"manyagent: skill install skipped ({type(exc).__name__}: {exc}) — see `manyagent status`",
                    style="yellow",
                )
            )
        )

    # Thread MANYAGENT_SESSION into the child so the MCP server (spawned by the
    # agent) can resolve the active session even without ~/.manyagent/active.
    os.environ["MANYAGENT_SESSION"] = sid_

    argv = [adapter.binary, *agent_args]
    io[1](
        ui.render(
            Text.assemble(("manyagent: running ", "dim"), (" ".join(argv), "bold"), (f" (session {sid_})", "dim"))
        )
    )

    # M11.6: tee the PTY master output to a tempfile, then build a
    # CanonicalTrace from it and run the M4 capture pipeline (validate →
    # scrub → bound → persist as a `raw` packet). This finally closes the
    # M8 "capture plumbing (trace tee) is M10 integration" deferral. The
    # adapter's own `capture()` was for the headless-subprocess path
    # (`adapter.invoke()` + pipe); the PTY path needs its own tee since the
    # master fd is the only thing carrying what the wrapped agent emitted.
    import tempfile

    tee_fd, tee_name = tempfile.mkstemp(prefix=f"manyagent-tee-{sid_}-", suffix=".log")
    os.close(tee_fd)
    tee_path = Path(tee_name)
    # M12.1: the spawn loops also write a timing sidecar next to the tee
    # (derived path — no signature change, so monkeypatched spawn stubs that
    # never write it just land on the untimed fallback in _timed_events).
    timing_path = Path(tee_name + ".timing")
    run_started = time.time()
    try:
        # Monkeypatched test/Simulation stubs return None — treat as 0.
        agent_rc = _pty_spawn(argv, tee=tee_path) or 0
        tee_bytes = tee_path.read_bytes() if tee_path.is_file() else b""
        timing_text = timing_path.read_text(encoding="utf-8") if timing_path.is_file() else ""
    finally:
        tee_path.unlink(missing_ok=True)
        timing_path.unlink(missing_ok=True)

    pid: str | None = None  # the raw packet id; mining (below) hangs off it
    # Cast-timing facts the Conversation tab needs to align/seek the replay:
    # whether the cast is real-timed (vs synthetic pacing → no markers), and
    # the wall-clock instant the cast's t0 (first event) maps to. Defaults are
    # the safe "untimed" values used when capture fails.
    cast_timed = False
    cast_t0 = run_started
    try:
        from manyagent.capture import persist
        from manyagent.capture.models import CanonicalTrace

        events, term = _timed_capture(tee_bytes, timing_text)
        cast_timed = len(events) > 1 and len({e.ts for e in events}) > 1
        cast_t0 = run_started + min((e.ts for e in events), default=0.0)
        trace = CanonicalTrace(
            session_id=sid_,
            agent_id=agent_id,
            adapter=name,
            events=events,
            source_fidelity="pty",
            bytes_in=len(tee_bytes),
            term=term,
        )
        pid = await persist(trace, bank=bank)
    except Exception as exc:
        io[1](ui.render(Text(f"manyagent: trace capture failed ({type(exc).__name__}: {exc})", style="red")))

    # M12 groundwork: surface what the lifecycle hooks bound during this run
    # (harness session ids + transcript paths appended by `manyagent._hook`). One
    # PTY run can span several harness sessions (`/clear` rolls a fresh id).
    bindings = _harness_bindings(sid_, since=run_started)

    # M13.1: mine the harness's own transcript of this run into the `harness`
    # rendition (the viewer's Conversation tab). Bindings first, mtime-window
    # scan as fallback — both inside the adapter's mine(). Never-fail, like
    # capture: a mining problem must not disturb the session close.
    if pid is not None and callable(getattr(adapter, "mine", None)):
        try:
            from manyagent.adapters.base import MineContext

            mined = adapter.mine(MineContext(cwd=Path.cwd(), window=(run_started, time.time()), bindings=bindings))
            if mined:
                # The cli owns the raw-trace↔rendition relationship, so it
                # stamps the cast-timing alignment the viewer can't derive
                # from the conversation alone (markers/seek gate on `timed`).
                mined["timed"] = cast_timed
                mined["cast_t0"] = cast_t0
                await bank.put_rendition(
                    pid,
                    "harness",
                    json.dumps(mined, ensure_ascii=False),
                    miner_version=str(mined.get("miner_version") or "") or None,
                )
        except Exception as exc:
            io[1](
                ui.render(Text(f"manyagent: conversation mining skipped ({type(exc).__name__}: {exc})", style="yellow"))
            )

    # Show the trace link to the user
    io[1](ui.render(Text.assemble(("trace: ", "dim"), (_session_url(sid_), "underline cyan"))))
    # The session-close moment fires HERE (2026-06-10; revised same day): the
    # wrapped agent exiting asks ONE question — end the session? — and
    # `_do_end` owns the distill offer + ★, so the identical close path runs
    # whether the user accepts here or types `manyagent end` later. (A separate
    # distill ask before this gate double-prompted: a failed draft was
    # re-offered moments later inside `_do_end`.) Clean exits only (after a
    # crash or Ctrl-C the user is dealing with the failure, not reflecting
    # on it).
    if agent_rc == 0 and not _noninteractive():
        try:
            if ask_allow(
                messages.AGENT_EXIT_END_OFFER.format(session_id=sid_),
                input_fn=io[0],
                output_fn=io[1],
                noninteractive=False,
            ):
                # `since` scopes the end-offer reflection's trace context to
                # the harness sessions bound during THIS run — a `--resume`d
                # or earlier run's transcript must not be what gets distilled.
                await _do_end(argparse.Namespace(session=sid_, since=run_started), bank=bank, io=io)
        except Exception as exc:
            io[1](
                ui.render(
                    Text(f"manyagent: session-close offers skipped ({type(exc).__name__}: {exc})", style="yellow")
                )
            )
    # The agent's own exit code is the run's exit code (the pre-M12 execvp
    # contract for scripted/CI callers) — capture happens either way above.
    return agent_rc


def _harness_bindings(sid_: str, *, since: float) -> list[dict[str, object]]:
    """Binding records ``manyagent._hook`` appended to
    ``$MANYAGENT_HOME/bindings/<sid>.jsonl`` during this run (``ts >= since``).
    Defensive parse: a malformed line never breaks the wrapper."""
    p = _manyagent_home() / "bindings" / f"{sid_}.jsonl"
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
    """``manyagent uninstall <adapter>`` — reverse the install via the saved manifest.
    Created files are removed iff still matching what we wrote; merged files
    have only our keys popped (third-party MCP servers etc. survive); any
    external CLI registrations (``claude mcp add`` etc.) are reversed by
    their recorded inverse command."""
    from manyagent._installer import uninstall

    rc = uninstall(args.adapter, _manyagent_home(), output_fn=io[1])
    if rc == 0:
        io[1]("")
        io[1](
            f"manyagent: skill files are gone from disk; if {args.adapter} is currently "
            "running, restart it so its slash menu refreshes (additions are live; "
            "removals are cached until session restart)."
        )
    return rc


async def _do_status(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``manyagent status`` — list every adapter that currently has an in-agent
    install (skills + MCP server entry) along with the files it owns."""
    from manyagent._installer import list_installed

    manifests = list_installed(_manyagent_home())
    if not manifests:
        io[1]("manyagent: no in-agent skills installed (run `manyagent <adapter>` to install)")
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
    to the shell's ``128+N`` so ``manyagent`` exits like the agent did."""
    return rc if rc >= 0 else 128 - rc


def _timed_capture(  # noqa: C901 — one linear parse with four documented fallback exits; splitting it would scatter the contract
    tee_bytes: bytes, timing_text: str
) -> tuple[list[Any], dict[str, Any] | None]:
    """Build the CanonicalTrace event list + terminal geometry for a run
    (M12.1 timing, M12.2 geometry).

    The spawn loops write a timing sidecar alongside the byte-exact tee.
    Lines are either ``"<offset_s> <n_bytes>"`` (one per master/pipe read) or
    ``"<offset_s> r <cols>x<rows>"`` (the initial terminal size + every
    SIGWINCH). When present and consistent, the trace carries one timestamped
    event per read chunk — the viewer's asciinema rendition replays the
    session's REAL cadence — and ``term`` carries the geometry the TUI
    actually laid itself out for (without it the replay guesses a width and
    every box border wraps). Chunks are decoded with one incremental UTF-8
    decoder so a multi-byte glyph split across reads never shreds into
    U+FFFD.

    Returns ``(events, term)``; ``term`` is ``{"cols", "rows", "resizes":
    [[offset_s, cols, rows], …]}`` or ``None`` when the sidecar carried no
    size records. Fallbacks keep the pre-M12 event shape (one event at ts=0):
    a missing/garbled sidecar (monkeypatched test stubs never write one), a
    byte-count mismatch, or a credential hit in the *joined* text — a secret
    split across two read chunks would defeat ``manyagent.capture``'s per-event
    scrub regexes, so any joined-text hit collapses the trace to a single
    event and lets ``persist()`` scrub it whole. Timing is sacrificed for
    safety on exactly the traces that need redaction; geometry is kept (it
    is never secret-bearing).
    """
    import codecs

    from manyagent.capture.models import CanonicalTrace, TraceEvent
    from manyagent.capture.scrub import scrub

    whole = tee_bytes.decode("utf-8", errors="replace")
    single: list[Any] = [TraceEvent(ts=0.0, kind="system", text=whole)]
    if not tee_bytes or not timing_text:
        return single, None
    chunks: list[tuple[float, int]] = []
    sizes: list[tuple[float, int, int]] = []
    try:
        for line in timing_text.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] == "r":
                cols_s, _, rows_s = parts[2].partition("x")
                sizes.append((float(parts[0]), int(cols_s), int(rows_s)))
            elif len(parts) == 2:
                chunks.append((float(parts[0]), int(parts[1])))
            else:  # unrecognized line shape — same handling as a parse failure
                return single, None
    except ValueError:
        return single, None  # garbled sidecar — distrust it wholesale

    term: dict[str, Any] | None = None
    if sizes:
        term = {
            "cols": sizes[0][1],
            "rows": sizes[0][2],
            "resizes": [[round(off, 6), c, r] for off, c, r in sizes[1:]],
        }
    if sum(n for _off, n in chunks) != len(tee_bytes):
        return single, term  # torn/partial sidecar — trust the bytes, drop timing
    probe = CanonicalTrace(
        session_id="probe", agent_id="probe/a", adapter="probe", events=single, source_fidelity="pty"
    )
    _scrubbed, report = scrub(probe)
    if report.counts:
        return single, term
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    events: list[Any] = []
    pos = 0
    for off, n in chunks:
        text = dec.decode(tee_bytes[pos : pos + n])
        pos += n
        if text:
            events.append(TraceEvent(ts=round(off, 6), kind="system", text=text))
    tail = dec.decode(b"", True)
    if tail and chunks:
        events.append(TraceEvent(ts=round(chunks[-1][0], 6), kind="system", text=tail))
    return events or single, term


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

    from manyagent.adapters.base import _register_proc, _unregister_proc

    tee_fd: int | None = None
    timing_fd: int | None = None
    if tee is not None:
        with contextlib.suppress(OSError):
            tee_fd = os.open(str(tee), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            # M12.1 timing sidecar: one "<offset_s> <n_bytes>" line per read;
            # _timed_events turns it into timestamped TraceEvents so the
            # viewer replays real cadence.
            timing_fd = os.open(str(tee) + ".timing", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    t0 = time.monotonic()
    # M12.2 best-effort geometry: stdin is a pipe here, but stdout/stderr is
    # often still the terminal (`echo prompt | manyagent claude`). Without a size
    # record the cast rendition has to guess a width.
    if timing_fd is not None:
        with contextlib.suppress(Exception):
            import fcntl
            import struct
            import termios

            for stream in (sys.stdout, sys.stderr):
                if stream.isatty():
                    sz = fcntl.ioctl(stream.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
                    rows, cols = struct.unpack("HHHH", sz)[:2]
                    os.write(timing_fd, f"0.000000 r {cols}x{rows}\n".encode())
                    break
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
                if timing_fd is not None:
                    with contextlib.suppress(OSError):
                        os.write(timing_fd, f"{time.monotonic() - t0:.6f} {len(data)}\n".encode())
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
        for fd in (tee_fd, timing_fd):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)


def _pty_spawn(argv: list[str], *, tee: Path | None = None) -> int:  # noqa: C901 — a PTY bridge is irreducibly stateful
    """Run ``argv`` under a PTY that **inherits the parent terminal's size**
    and forwards ``SIGWINCH`` so live resizes flow through. The stdlib's
    ``pty.spawn`` leaves the child PTY at the kernel default 0x0 — TUI hosts
    (Claude Code's Ink/React-Terminal, Gemini's blessed) misread that as
    80x24 and render in a fixed 80-column window (the user-reported uncanny
    smallness). M11 reimplements with ``pty.fork`` + ``TIOCSWINSZ``.

    Returns the child's exit code (0 when it can't be determined).

    Monkeypatched in unit tests (the stubs return ``None``; the caller
    treats that as 0); the real path is exercised by ``manyagent <name>``.

    **POSIX only.** Windows has no ``pty``/``fcntl``/``termios``/``tty``; on
    Windows we raise a clear ``NotImplementedError`` rather than silently
    fall back to a sized-wrong subprocess. Native Windows PTY support
    (ConPTY via ``winpty`` / ``pywinpty``) is a future deliverable.
    """
    if sys.platform == "win32":  # pragma: no cover — checked on Windows CI
        raise NotImplementedError(
            "manyagent's PTY wrapper is POSIX-only (Windows lacks pty/fcntl/termios). "
            "Run the wrapped agent directly (e.g. `claude`); /self-distill etc. "
            "still work via the MCP server once `manyagent <name>` installed the skills."
        )

    import contextlib
    import fcntl
    import pty
    import select
    import termios
    import tty

    # Non-interactive callers (redirected stdin: pipes, CI, headless `-p`
    # runs) can't host a PTY bridge — but they still get capture. The old
    # fallback here was an `os.execvp` that REPLACED the manyagent process: the
    # wrapper vanished, nothing was tee'd, and `_do_run_agent` never reached
    # its capture/persist step, so wrapped non-interactive runs silently
    # produced no trace (the M11 non-TTY hole). Spawn-and-tee instead.
    if not sys.stdin.isatty():
        return _pipe_spawn(argv, tee=tee)

    pid, master_fd = pty.fork()
    if pid == 0:  # child
        os.execvp(argv[0], argv)  # noqa: S606 — exec'ing the user-named adapter binary is the point
        return 0  # unreachable, but mypy doesn't know

    # Optional tee: every byte the master fd emits is also written to ``tee``,
    # so ``_do_run_agent`` can persist a ``raw`` packet via the M4 capture
    # pipeline (closes the M8 "capture plumbing (trace tee) is M10 integration"
    # deferral — M11.6). Opened BEFORE the winsize sync so the sidecar's first
    # record is the initial terminal size.
    tee_fd: int | None = None
    timing_fd: int | None = None
    if tee is not None:
        with contextlib.suppress(OSError):
            tee_fd = os.open(str(tee), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            # M12.1 timing sidecar (see _pipe_spawn): real per-read offsets so
            # the stored trace replays the session's actual cadence.
            timing_fd = os.open(str(tee) + ".timing", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    t0 = time.monotonic()

    # parent: copy our winsize to the child PTY, re-sync on every SIGWINCH,
    # and record each size into the sidecar (`<offset> r <cols>x<rows>`) —
    # M12.2: the cast rendition needs the REAL geometry the TUI laid itself
    # out for, or the replay wraps every box border.
    def _sync_winsize(*_: object) -> None:
        try:
            sz = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, sz)
        except OSError:
            return  # best-effort; a closed terminal shouldn't bring us down
        if timing_fd is not None:
            import struct

            rows, cols = struct.unpack("HHHH", sz)[:2]
            with contextlib.suppress(OSError):
                os.write(timing_fd, f"{time.monotonic() - t0:.6f} r {cols}x{rows}\n".encode())

    _sync_winsize()
    signal.signal(signal.SIGWINCH, _sync_winsize)

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
                if timing_fd is not None:
                    with contextlib.suppress(OSError):
                        os.write(timing_fd, f"{time.monotonic() - t0:.6f} {len(data)}\n".encode())
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
        for fd in (tee_fd, timing_fd):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
        with contextlib.suppress(OSError):
            _, wait_status = os.waitpid(pid, 0)
            exit_code = _shell_exit_code(os.waitstatus_to_exitcode(wait_status))
    return exit_code


# M11.4: the four knowledge-loop verbs moved to ``manyagent._handlers``. They are
# no longer CLI subcommands — only the session-lifecycle verbs (start /
# register / <name> / end / status / uninstall) remain on this surface.
# The user-facing path is in-agent skills + the MCP server (manyagent._mcp).


async def _do_end(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    sid_ = _resolve_sid(args.session)
    # Sensible default (2026-06-10): before the session closes, offer the
    # distillation moments instead of relying on the human remembering the
    # verbs mid-session. One allowance gate each; never blocks `manyagent end`.
    try:
        await _offer_end_distill(sid_, since=getattr(args, "since", None), bank=bank, io=io)
    except Exception as exc:
        io[1](ui.render(Text(f"manyagent: distill offer skipped ({type(exc).__name__}: {exc})", style="yellow")))
    await bank.put_session(sid_, status="ended")
    # ★ moment: manyagent.core has no sessions.rating column, so manyagent end's ★ lands on
    # the most recent UNRATED reflection post in the session (no-op if none) —
    # avoids an unneeded manyagent.bank migration (M8 Decision-log).
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
    io[1](ui.render(Text.assemble(("session ", "dim"), (sid_, "bold"), (" ended", "dim"))))
    return 0


async def _offer_end_distill(sid_: str, *, since: float | None = None, bank: Bank, io: tuple[In, Out]) -> None:
    """End-of-session self-distill offer (sensible default, 2026-06-10).

    Fires only when the session did work (an agent registered) but committed
    no reflection. When a bundle was INJECTED into this session, the offer
    becomes the injected-bundle follow-up: the drafted reflection is guided
    to evaluate whether the bundle held up, citing it via ``evidence_ref`` —
    the v1 of conflict-driven discussion (manyagent.procedures.md §9.2; a true
    cross-session ``disagree`` reply needs goal-scoped retrieve(), recorded
    there as the upgrade path).

    The cross-distill moment moved to ``manyagent start`` (`_offer_cross_nudge`) —
    a fresh bundle is useful at the START of the next session, and the
    newer-than-bundle counting there avoids immediate back-to-back re-runs.

    Silent no-op in ``MANYAGENT_NONINTERACTIVE`` — unattended runs never spend an
    LLM call or commit on a default."""
    if _noninteractive():
        return
    from manyagent._handlers import do_self_distill

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
            await do_self_distill(adapter=adapter, guidance=guidance, session=sid_, since=since, bank=bank, io=io)


def _noninteractive() -> bool:
    return config.resolve("MANYAGENT_NONINTERACTIVE", False, cast=config.as_bool)


# --------------------------------------------------------------------------- #
# argparse + sniffing dispatch
# --------------------------------------------------------------------------- #

_EPILOG = """\
session lifecycle (the only verbs on this CLI):
  manyagent start [goal] [--id XXXX-XXXX]  start/join a session (goal defaults to 'misc'; writes ~/.manyagent/active)
  manyagent register <name>             register an adapter as an Agent
  manyagent <name> [args...]            install in-agent skills + run the wrapped agent under a PTY
  manyagent end [--session id]          end the session (optional ★ prompt)
  manyagent status                      list installed in-agent skill bundles
  manyagent uninstall <adapter>         reverse the install via the saved manifest

knowledge-loop verbs (typed INSIDE the wrapped agent, not on this CLI):
  /self-distill                   (Claude Code, Gemini CLI)
  /discuss [@packet] [stance]
  /cross-distill
  /inject [@packet]
  $self-distill, $discuss, ...    (Codex CLI — `/` is reserved for built-ins)

Skills + MCP server install on `manyagent <name>`; the human surface stays one tap
(Design Principles §11): the agent generates the structured post, proposes
the ★, and your in-agent permission prompt is the accept gate (C1).
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ma",
        description="Wrap installed coding-agent CLIs; curate cross-session knowledge.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"ma {__version__}")
    sub = p.add_subparsers(dest="verb")

    s = sub.add_parser("start")
    s.add_argument("goal", nargs="?")
    s.add_argument("--id")

    r = sub.add_parser("register")
    r.add_argument("name")
    r.add_argument("--session")

    # M11.4: self-distill / discuss / cross-distill / inject are NOT CLI
    # subcommands — they're installed as in-agent skills + MCP tools by
    # ``manyagent <name>``. Programmatic callers use ``manyagent._handlers.do_*`` directly.

    e = sub.add_parser("end")
    e.add_argument("--session")

    # M11: in-agent install lifecycle. `manyagent <name>` runs the install
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
    is what ``python -m manyagent.preflight`` diagnoses; here we just fail cleanly and
    point at it. ``MANYAGENT_DEBUG=1`` re-raises for developers."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        print("manyagent: interrupted", file=sys.stderr)
        return 130
    except SystemExit as exc:
        # Our own clean one-liners (e.g. _resolve_sid / _adapter_for) carry a
        # string; argparse/explicit numeric exits are preserved verbatim.
        if isinstance(exc.code, int) or exc.code is None:
            raise
        print(f"manyagent: {exc.code}", file=sys.stderr)
        return 1
    except Exception as exc:
        if config.resolve("MANYAGENT_DEBUG", False, cast=config.as_bool):
            raise
        print(f"manyagent: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "manyagent: run `python -m manyagent.preflight` to check env/Bank/keys — set "
            "MANYAGENT_BANK_TRUSTED_KEY in manyagent.env, or start a local Bank with "
            "`make bank-up`. Set MANYAGENT_DEBUG=1 for a full traceback.",
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

    # Not a known verb and not a flag → `manyagent <name> [args]` (run an agent).
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
