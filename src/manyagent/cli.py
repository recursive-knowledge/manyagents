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
import contextlib
import json
import os
import re
import signal
import sys
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rich.text import Text

from manyagent import __version__
from manyagent.bank import Bank, get_bank
from manyagent.utils import config, messages, sid, slug, ui

# --------------------------------------------------------------------------- #
# pure helpers (unit-testable in isolation, no I/O)
# --------------------------------------------------------------------------- #

# The reserved top-level group names. Everything else on the command line is an
# AGENT to run (``ma <agent> …``) — the product's core ergonomic
# (``ma claude`` ≈ ``claude``). An agent therefore may not be named one of these
# (clig.dev: a catch-all dispatch must reserve its namespace). The knowledge-loop
# verbs (self-distill / discuss / cross-distill / inject) are NOT CLI subcommands
# — they live as in-agent skills + MCP tools (manyagent._mcp); programmatic
# callers use ``manyagent._handlers.do_*`` directly.
RESERVED = {"session", "dev", "agent"}

# Removed top-level verbs → where they live now. Typed at the top level they no
# longer resolve as agents; we map them to a one-line redirect rather than the
# generic "no agent" error (clig.dev: "if the user did something wrong and you
# can guess what they meant, suggest it").
_MOVED = {
    "start": "session start",
    "end": "session end",
    "init": "dev init",
    "preflight": "dev preflight",
    "status": "agent list",
    "register": "agent register",
    "uninstall": "agent unregister",
}


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


def _clear_active(session_id: str | None = None) -> None:
    """Remove the sticky-session marker. When ``session_id`` is given, only clear
    it if it points at that session — so auto-ending an *ephemeral* run never
    wipes a different sticky session's marker (``ma session start X`` then
    ``ma "y" claude`` must leave X active)."""
    p = active_session_path()
    if not p.is_file():
        return
    if session_id is not None and _read_active() != session_id:
        return
    p.unlink()


def principals_path() -> Path:
    """``~/.manyagent/principals.json`` — the adapter→principal map (00011)."""
    return _manyagent_home() / "principals.json"


def _principal_for(adapter: str) -> str:
    """Stable per-(machine, adapter) principal id for cross-goal agent identity.

    Minted once on first register, persisted to ``~/.manyagent/principals.json``
    as an ``{adapter: uuid4}`` map, and read back thereafter (00011). First-party
    automatic — no user-typed handle; the id is a real UUID4 (``sid.new()``),
    never derived from the adapter name (datasmith identity rule). Per-machine:
    the same operator on two machines gets two principals. Corruption or a bad
    value re-mints rather than crashing ``register``; the write is atomic so a
    concurrent adapter spawn can at worst duplicate a (harmless) mint.
    """
    p = principals_path()
    data: dict[str, str] = {}
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = {str(k): str(v) for k, v in loaded.items()}
        except (ValueError, OSError):
            data = {}  # corrupt file → re-mint, never crash register
    existing = data.get(adapter)
    if existing and sid.is_valid(existing):
        return existing
    pid = sid.new()
    data[adapter] = pid
    home = _manyagent_home()
    home.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)  # atomic swap; last-writer-wins on the map
    return pid


def _resolve_sid(explicit: str | None) -> str:
    """``--session`` wins, else ``~/.manyagent/active``; error if neither."""
    s = explicit or _read_active()
    if not s:
        raise SystemExit("no active session: run `ma session start` or pass --session <id>")
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


def _web_base() -> str:
    """The viewer's base URL, no path.

    The hosted viewer's base URL (`MANYAGENT_WEB_PUBLIC_URL`, default
    swarms.formulacode.org) wins; set it EMPTY to fall back to the local bind
    config (`MANYAGENT_WEB_HOST`/`MANYAGENT_WEB_PORT`) for local dev. In the fallback,
    `0.0.0.0` is a wildcard bind, not a reachable address — display as
    `127.0.0.1` so the line is clickable when copy/pasted."""
    base = config.resolve("MANYAGENT_WEB_PUBLIC_URL", config.MANYAGENT_WEB_PUBLIC_URL).strip().rstrip("/")
    if base:
        return base
    host = config.resolve("MANYAGENT_WEB_HOST", config.MANYAGENT_WEB_HOST) or "127.0.0.1"
    if host == "0.0.0.0":  # noqa: S104 — defensive REWRITE of an unreachable wildcard, not a bind
        host = "127.0.0.1"
    port = int(config.resolve("MANYAGENT_WEB_PORT", config.MANYAGENT_WEB_PORT, cast=int))
    return f"http://{host}:{port}"


def _session_url(session_id: str) -> str:
    """The session deep-link (`…/s/{session}`) — used for agent/trace links."""
    return f"{_web_base()}/s/{session_id}"


def _goal_url(goal: str) -> str:
    """The goal board (`…/g/{slug}`) — the durable, shareable surface. The slug
    is the URL-normalized goal (manyagent.utils.slug); ids are UUIDs and not
    meant for humans to read in a URL."""
    return f"{_web_base()}/g/{slug.slugify(goal)}"


def _open_url(session_id: str, goal: str | None) -> str:
    """The `open:` link `ma start` prints: the goal board when the session has a
    goal, else the session deep-link (an ungoaled session has no goal board)."""
    return _goal_url(goal) if goal else _session_url(session_id)


def _agent_url(agent_id: str) -> str:
    """Build the per-agent deep link URL. ``agent_id`` is the canonical
    ``{session}/agent-{NNN}-{adapter}``; the URL is ``…/s/{session}/a/{tail}``,
    matching the ``manyagent.web`` route convention (full id = ``{session}/{tail}``)."""
    session_id, _, tail = agent_id.partition("/")
    return f"{_session_url(session_id)}/a/{tail}"


def _user_env_path() -> Path:
    """``$MANYAGENT_HOME/env`` — the ONE config file the package loads from any
    working directory (``manyagent.setup_environment``). The onboarding target for
    installed-wheel users (``uv tool install manyagent``), who have no repo
    checkout, no ``./manyagent.env``, and no ``make bank-up``."""
    return _manyagent_home() / "env"


def _read_secret(prompt: str, *, input_fn: In) -> str:
    """Echo-free read on a real terminal — a typed/pasted Bank key must not land
    in scrollback or session recordings. Scripted callers (tests, Simulation)
    pass their own ``input_fn`` and get the plain read (the ``ask_commit``
    real-terminal-vs-seam branch pattern)."""
    if input_fn is input and sys.stdin.isatty() and sys.stdout.isatty():
        import getpass

        return getpass.getpass(prompt)
    return input_fn(prompt)


# Unquoted-safe charset for an env-file value; anything else is double-quoted.
_ENV_SAFE = re.compile(r"[A-Za-z0-9_.\-/:@+~]*")


def _env_line(name: str, value: str) -> str:
    """One ``NAME=value`` line that survives the dotenv format. Values outside
    the safe charset (spaces, ``#``, quotes, ``=``…) are double-quoted with
    ``\\``/``"`` escaped; a newline can't be represented losslessly, so it is
    refused outright. ``_do_init`` parse-back-verifies the whole file after
    rendering, so anything this misses fails loudly, never silently."""
    if "\n" in value or "\r" in value:
        raise SystemExit(f"{name} value contains a newline — pass a single-line value")
    if _ENV_SAFE.fullmatch(value):
        return f"{name}={value}\n"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{name}="{escaped}"\n'


def _mask_secrets(env_text: str) -> str:
    """The overwrite gate's ``d`` detail shows WHAT would be replaced, never the
    stored credentials themselves (manyagent.capture.scrub's rule: matched secret
    text reaches no output surface)."""
    masked = []
    for line in env_text.splitlines():
        name, sep, value = line.partition("=")
        secretish = any(t in name.upper() for t in ("KEY", "SECRET", "TOKEN"))
        if sep and value and secretish and not line.lstrip().startswith("#"):
            masked.append(f"{name}=<redacted>")
        else:
            masked.append(line)
    return "\n".join(masked)


def _fetch_published_config() -> dict[str, str] | None:
    """GET ``{MANYAGENT_WEB_PUBLIC_URL}/.well-known/manyagent.json`` — the
    deployment's CURRENT public Bank connection (manyagent.web). Fetching at
    init-time and caching into the user env file is what lets the hosted
    stack rotate keys without a package release; the package itself ships no
    key literals, only the derived demo fallback (config._demo_jwt). Returns
    None on any failure — an offline init falls back to built-in defaults."""
    base = config.resolve("MANYAGENT_WEB_PUBLIC_URL", config.MANYAGENT_WEB_PUBLIC_URL).strip().rstrip("/")
    if not base:
        return None
    try:
        import httpx

        resp = httpx.get(f"{base}/.well-known/manyagent.json", timeout=5.0)
        if resp.status_code != 200:
            return None
        doc = resp.json()
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    out = {k: v.strip() for k, v in doc.items() if isinstance(k, str) and isinstance(v, str)}
    return out or None


def _init_published(resolved_url: str, io: tuple[In, Out]) -> dict[str, str]:
    """The deployment's published connection, when it applies. A custom-Bank
    user is never fetched for — `ma init` must not repoint them at the public
    deployment; the three outcomes each narrate one dim/yellow line."""
    if resolved_url != config.MANYAGENT_BANK_URL_DEFAULT:
        io[1](ui.render(Text(messages.INIT_CUSTOM_BANK_NOTE, style="dim")))
        return {}
    fetched = _fetch_published_config()
    if fetched is None:
        io[1](ui.render(Text(messages.INIT_OFFLINE_NOTE, style="yellow")))
        return {}
    io[1](ui.render(Text(messages.INIT_FETCHED_NOTE, style="dim")))
    return fetched


def _init_url_value(
    args: argparse.Namespace, published: dict[str, str], resolved_url: str, *, noninteractive: bool, io: tuple[In, Out]
) -> str:
    if args.bank_url is not None:
        return str(args.bank_url)
    default_url = published.get("bank_url") or resolved_url
    if noninteractive:
        return default_url
    prompt = (
        ui.render(
            Text.assemble(
                (messages.INIT_URL_PROMPT + " ", "bold"),
                (messages.INIT_DEFAULT_HINT.format(default=default_url), "dim"),
            )
        )
        + " "
    )
    return io[0](prompt).strip() or default_url


def _init_key_value(
    args: argparse.Namespace, published: dict[str, str], *, noninteractive: bool, io: tuple[In, Out]
) -> str:
    if args.trusted_key is not None:
        return str(args.trusted_key)
    # Published (rotation-fresh) wins over the resolved env; the empty
    # fallback keeps the derived demo default OUT of the written file —
    # it lives in code only, applied when nothing else is configured.
    current = published.get("trusted_key") or config.resolve("MANYAGENT_BANK_TRUSTED_KEY", "")
    if noninteractive:
        return current
    hint = messages.INIT_KEEP_HINT if current else messages.INIT_SKIP_HINT
    prompt = ui.render(Text.assemble((messages.INIT_KEY_PROMPT + " ", "bold"), (hint, "dim"))) + " "
    # A pasted `MANYAGENT_BANK_TRUSTED_KEY=eyJ…` assignment would round-trip
    # as the wrong key (the `NAME=` prefix becomes part of the value).
    raw = _read_secret(prompt, input_fn=io[0]).strip().removeprefix(messages.INIT_KEY_PROMPT + "=")
    return raw or current


async def _do_init(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``manyagent init`` — write the user-level env file. Defaults come from the
    deployment's published well-known document when reachable (so a re-run
    picks up rotated keys), else the currently-resolved config; flags win over
    both, and a re-run never silently drops a stored anon key / CF Access
    pair. The URL and trusted key additionally prompt interactively. Only
    non-empty values are written; the rendered file is parse-back-verified
    through dotenv before it lands. An open-corpus disclosure is always
    printed; the interactive confirm tap is skipped under
    ``MANYAGENT_NONINTERACTIVE`` (automation must not be blocked, but the
    disclosure is still shown). Overwriting an existing file sits behind one
    allowance gate (``d`` shows the file it would replace, credentials
    masked; deny-by-default under ``MANYAGENT_NONINTERACTIVE``, Open-Q §B5)."""
    noninteractive = _noninteractive()
    resolved_url = config.resolve("MANYAGENT_BANK_URL", config.MANYAGENT_BANK_URL)
    published = _init_published(resolved_url, io)
    url = _init_url_value(args, published, resolved_url, noninteractive=noninteractive, io=io)
    key = _init_key_value(args, published, noninteractive=noninteractive, io=io)
    pairs = (
        ("MANYAGENT_BANK_URL", url.strip()),
        ("MANYAGENT_BANK_TRUSTED_KEY", key.strip()),
        # Flag wins, else published, else carry forward what's already
        # resolvable — a key rotation via `ma init` must not silently discard
        # the stored anon key or the CF Access pair the db tunnel needs.
        (
            "MANYAGENT_BANK_ANON_KEY",
            (args.anon_key or published.get("anon_key", "") or config.resolve("MANYAGENT_BANK_ANON_KEY", "")).strip(),
        ),
        (
            "MANYAGENT_BANK_CF_ACCESS_CLIENT_ID",
            (args.cf_access_client_id or config.resolve("MANYAGENT_BANK_CF_ACCESS_CLIENT_ID", "")).strip(),
        ),
        (
            "MANYAGENT_BANK_CF_ACCESS_CLIENT_SECRET",
            (args.cf_access_client_secret or config.resolve("MANYAGENT_BANK_CF_ACCESS_CLIENT_SECRET", "")).strip(),
        ),
    )
    content = (
        "# Written by `ma init` (manyagent.cli). Loaded at import from ANY working\n"
        "# directory; live env vars and ./manyagent.env win on overlap.\n"
        + "".join(_env_line(name, value) for name, value in pairs if value)
    )
    # Parse-back verification: what dotenv will read tomorrow must be what the
    # user gave today (an unparseable line silently DROPS its key on load).
    import io as _io

    import dotenv

    parsed = dotenv.dotenv_values(stream=_io.StringIO(content))
    for name, value in pairs:
        if value and parsed.get(name) != value:
            raise SystemExit(f"{name} value does not survive the env-file format — remove special characters")
    # Open-corpus disclosure (decision #3): the user must be told, at setup time,
    # that traces are stored in a shared public-by-default Bank. Always printed.
    # Under MANYAGENT_NONINTERACTIVE: print the disclosure but skip the confirm tap
    # (don't block automation).
    io[1](ui.render(Text(messages.INIT_DISCLOSURE, style="dim")))
    if not noninteractive and not ask_allow(
        messages.INIT_DISCLOSURE_CONFIRM,
        input_fn=io[0],
        output_fn=io[1],
        noninteractive=False,
    ):
        return 1
    path = _user_env_path()
    shown = ui.tilde(path)
    if path.is_file() and not ask_allow(
        messages.INIT_OVERWRITE_OFFER.format(path=shown),
        input_fn=io[0],
        output_fn=io[1],
        noninteractive=noninteractive,
        detail=_mask_secrets(path.read_text(encoding="utf-8")),
    ):
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o600)  # tighten BEFORE the new key is written into it
    # O_CREAT with 0o600 — never a umask-default window with the key on disk
    # (same pattern as the tee/timing fds below).
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    io[1](ui.render(Text(messages.INIT_WRITTEN_NOTE.format(path=shown), style="green")))
    if not key:
        io[1](ui.render(Text(messages.INIT_NO_KEY_NOTE.format(path=shown), style="yellow")))
    return 0


async def _do_preflight(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``manyagent preflight`` — the env/Bank/keys validator, reachable from the
    installed binary. ``python -m manyagent.preflight`` still works in a checkout,
    but a `uv tool install` user has no usable ``python`` for the module form —
    the hints must name a command that copy-pastes for everyone."""
    from manyagent.preflight import run_preflight

    return run_preflight()


async def _session_start_offers(
    session_id: str, explicit_goal: str | None, *, bank: Bank, io: tuple[In, Out], allow_continuity: bool
) -> str:
    """Resolve the session's goal and fire the best-effort session-start moments
    (2026-06-10): goal continuity, quarantine visibility, the stale-goal
    cross-distill nudge, and the inject offer. Returns the resolved goal.

    Shared by ``ma session start`` (``allow_continuity=True``) and the ephemeral
    ``ma <agent>`` run path (``allow_continuity=False`` — a bare ``ma claude``
    must not prompt "continue last goal?"). All offers are best-effort: a Bank
    hiccup here never blocks the session opening."""
    goal = explicit_goal
    default_goal = config.resolve("MANYAGENT_DEFAULT_GOAL", config.MANYAGENT_DEFAULT_GOAL)
    try:
        if not goal and allow_continuity:
            goal = await _offer_goal_continuity(session_id, bank=bank, io=io)
        if not goal:
            # Every session carries a goal: no goal given (and continuity
            # declined / disallowed) files the session under the default bucket.
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
        goal = goal or default_goal
    return goal


async def _do_start(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    session_id = args.id or sid.new()
    await bank.put_session(session_id, goal=args.goal)
    _write_active(session_id)  # the sticky-session marker (only `ma session start` writes it)
    line = Text.assemble(("session ", "dim"), (session_id, "bold"))
    if args.goal:
        line.append(f"  goal={args.goal!r}", style="dim")
    io[1](ui.render(line))
    goal = await _session_start_offers(session_id, args.goal, bank=bank, io=io, allow_continuity=True)
    # The viewer URL is the actionable artifact — point it at the goal board (the
    # durable, shareable surface) once the goal is resolved; ungoaled → /s/{id}.
    io[1](ui.render(Text.assemble(("open: ", "dim"), (_open_url(session_id, goal), "underline cyan"))))
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


async def _do_agent_register(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``ma agent register <name>`` — install the in-agent skills + MCP server for
    an agent. This is the machine-level setup that otherwise happens implicitly
    the first time you run ``ma <name>``; exposing it lets you install once,
    up front. Idempotent. Registering the agent as an Agent inside a session
    stays automatic at run time (``_resolve_agent`` in ``_do_run_agent``)."""
    from manyagent._handlers import _adapter_for

    name = args.name
    # The install is user-scoped and session-independent — bind the adapter to
    # empty ids purely to reach install_skills() (session_id=None is accepted).
    adapter = _adapter_for(name, session_id="", agent_id="")
    home = _manyagent_home()
    home.mkdir(parents=True, exist_ok=True)
    manifest = adapter.install_skills(session_id=None, oma_home=home, scope="user")
    if manifest is None:
        io[1](ui.render(Text.assemble(("manyagent: ", "dim"), (f"no skills installed for {name}", "yellow"))))
        return 1
    io[1](ui.render(Text.assemble(("registered ", "green"), (name, "bold"), (" — skills + MCP installed", "dim"))))
    io[1](ui.render(Text(f"run it with `ma {name}`; inspect with `ma agent list`", style="dim")))
    return 0


async def _resolve_run_session(goal: str | None, *, bank: Bank, io: tuple[In, Out]) -> tuple[str, bool]:
    """Decide which session ``ma <agent>`` attaches to, and whether it is
    *ephemeral* (auto-ends on clean exit). The sticky marker (``~/.manyagent/active``,
    written only by ``ma session start``) is the only state:

    - an explicit goal → always a FRESH ephemeral session under that goal (never
      hijacks the sticky one — an explicit goal means "new work");
    - else a sticky active session exists → ATTACH to it, NOT ephemeral;
    - else → a fresh ephemeral session under the default goal.

    Ephemeral sessions never write the marker. Fresh sessions fire the
    session-start offers (inject/context), minus the goal-continuity prompt."""
    if not goal:
        active = _read_active()
        if active:
            return active, False
    session_id = sid.new()
    await bank.put_session(session_id, goal=goal)
    await _session_start_offers(session_id, goal, bank=bank, io=io, allow_continuity=False)
    return session_id, True


async def _do_run_agent(name: str, agent_args: list[str], goal: str | None, *, bank: Bank, io: tuple[In, Out]) -> int:
    from manyagent._handlers import _adapter_for, _resolve_agent

    sid_, ephemeral = await _resolve_run_session(goal, bank=bank, io=io)
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
    # Session close (sticky-marker model, 2026-06-22). An EPHEMERAL session — one
    # manyagent minted for this run (no `ma session start` sticky marker) — closes
    # WITH the agent window: auto-end on clean exit, no gate. A STICKY session
    # (the user ran `ma session start`) is left open; they end it explicitly with
    # `ma session end`. Clean exits only — after a crash or Ctrl-C the user is
    # dealing with the failure, not reflecting. `_do_end` owns the distill + ★
    # offers, so the identical close path runs whether it fires here (auto) or via
    # `ma session end` later; `since` scopes the end reflection's trace context to
    # the harness sessions bound during THIS run.
    if ephemeral and agent_rc == 0:
        io[1](ui.render(Text(messages.AGENT_EXIT_AUTO_END_NOTE.format(session_id=sid_), style="dim")))
        try:
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


async def _do_agent_unregister(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``ma agent unregister <name>`` — reverse the skill install via the saved
    manifest. Created files are removed iff still matching what we wrote; merged
    files have only our keys popped (third-party MCP servers etc. survive); any
    external CLI registrations (``claude mcp add`` etc.) are reversed by their
    recorded inverse command."""
    from manyagent._installer import uninstall

    rc = uninstall(args.name, _manyagent_home(), output_fn=io[1])
    if rc == 0:
        io[1]("")
        io[1](
            f"manyagent: skill files are gone from disk; if {args.name} is currently "
            "running, restart it so its slash menu refreshes (additions are live; "
            "removals are cached until session restart)."
        )
    return rc


async def _do_agent_list(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``ma agent list [-v]`` — list every agent that currently has an in-agent
    install (skills + MCP server entry). Plain shows one line per agent; ``-v``
    expands the per-file manifest it owns (the old ``ma status`` detail)."""
    from manyagent._installer import list_installed

    manifests = list_installed(_manyagent_home())
    if not manifests:
        io[1]("manyagent: no agents registered (run `ma agent register <name>`, or just `ma <name>`)")
        return 0
    for m in manifests:
        io[1]("")
        io[1](
            ui.render(
                Text.assemble(
                    (m.adapter, "bold magenta"),
                    (f"  scope {m.scope} · installed {m.installed_at}", "dim"),
                    ("" if getattr(args, "verbose", False) else f"  · {len(m.entries)} file(s)", "dim"),
                )
            )
        )
        if not getattr(args, "verbose", False):
            continue
        for e in m.entries:
            # the verb word, not a bare sigil: `~ ~/.codex/config.toml` would
            # read as two indistinguishable tildes, and the list has no legend.
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
    # record the cast rendition has to guess a width. The ``!= "win32"`` guard
    # narrows fcntl/termios away for mypy on Windows (where this path is never
    # reached — `_pty_spawn` raises NotImplementedError before calling here).
    if timing_fd is not None and sys.platform != "win32":
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
# no longer CLI subcommands — the surface is the `ma <agent>` run path plus the
# reserved groups (`ma agent` / `ma session` / `ma dev`). The user-facing
# knowledge loop is in-agent skills + the MCP server (manyagent._mcp).


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
    _clear_active(sid_)
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
# `ma session list` — browse recent sessions
# --------------------------------------------------------------------------- #

_REL_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _to_dt(iso_str: str) -> datetime | None:
    """Parse a Bank ``created_at`` into an aware UTC datetime (``Z`` and naive
    timestamps both normalized), or ``None`` if unparseable."""
    s = iso_str.strip().replace("Z", "+00:00")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _parse_when(value: str) -> datetime:
    """A ``--since``/``--until`` value → aware UTC datetime. Accepts a relative
    offset ``<N><unit>`` (s/m/h/d/w, e.g. ``7d``/``24h``/``2w``) meaning "N ago",
    or an ISO date/datetime (``2026-06-01``). SystemExit with a hint otherwise."""
    s = value.strip()
    m = re.fullmatch(r"(\d+)([smhdw])", s)
    if m:
        return datetime.now(UTC) - timedelta(**{_REL_UNITS[m.group(2)]: int(m.group(1))})
    dt = _to_dt(s)
    if dt is None:
        raise SystemExit(f"can't parse time {value!r} — use an ISO date (2026-06-01) or an offset like 7d/24h/2w")
    return dt


def _short_sid(s: str) -> str:
    """A browse-friendly session id: the leading uuid segment, abbreviated."""
    return (s[:8] + "…") if len(s) > 9 else s


def _fmt_ts(iso: object) -> str:
    """Render a timestamp as local ``YYYY-MM-DD HH:MM`` for the list table."""
    dt = _to_dt(str(iso or ""))
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else (str(iso or "—")[:16])


async def _do_session_list(args: argparse.Namespace, *, bank: Bank, io: tuple[In, Out]) -> int:
    """``ma session list [N] [--since W] [--until W] [--goal G]`` — the local
    session browser. Defaults to the 10 most recent; filters by goal and a
    created-at window before slicing. Renders a rich table (★ marks the sticky
    active session). Post counts + last-updated are computed only for the rows
    actually shown."""
    from rich.table import Table

    since = _parse_when(args.since) if getattr(args, "since", None) else None
    until = _parse_when(args.until) if getattr(args, "until", None) else None
    goal_filter = getattr(args, "goal", None)
    rows = []
    for s in await bank.list_sessions():
        if goal_filter and (s.get("goal") or "") != goal_filter:
            continue
        created = _to_dt(str(s.get("created_at") or ""))
        if since and (created is None or created < since):
            continue
        if until and (created is None or created > until):
            continue
        rows.append(s)
    rows.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
    rows = rows[: (getattr(args, "n", None) or 10)]
    if not rows:
        io[1]("manyagent: no sessions match")
        return 0
    active = _read_active()
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col, just in (
        ("", "left"),
        ("goal", "left"),
        ("session", "left"),
        ("status", "left"),
        ("posts", "right"),
        ("created", "left"),
        ("updated", "left"),
    ):
        table.add_column(col, justify=just)  # type: ignore[arg-type]
    for s in rows:
        sid_s = str(s.get("id") or "")
        packets = await bank.list_packets(session_id=sid_s)
        posts = sum(1 for p in packets if p.get("type") == "post")
        last = max((str(p.get("created_at") or "") for p in packets), default=str(s.get("created_at") or ""))
        table.add_row(
            "★" if sid_s == active else "",
            str(s.get("goal") or "—"),
            _short_sid(sid_s),
            str(s.get("status") or ""),
            str(posts),
            _fmt_ts(s.get("created_at")),
            _fmt_ts(last),
        )
    io[1](ui.render(table))
    return 0


# --------------------------------------------------------------------------- #
# argparse + sniffing dispatch
# --------------------------------------------------------------------------- #

_EPILOG = """\
examples:
  ma claude                        run claude in a quick session (auto-ends when it closes)
  ma "fix the parser bug" claude   run claude in a new session named by that goal
  ma --goal "ship v2" codex        same, with the goal as a flag (unambiguous)
  ma session start "ship v2"       open a session that stays active across runs
  ma session list                  browse your recent sessions

run an agent:
  ma [--goal G] <agent> [args...]  run a wrapped agent; with a sticky session
                                   active it attaches, else it auto-ends on exit

agent tooling (skills + MCP server):
  ma agent register <name>         install the in-agent skills + MCP server
  ma agent unregister <name>       reverse the install via the saved manifest
  ma agent list [-v]               list registered agents (-v: the file manifest)

sessions:
  ma session start [goal] [--id]   start a sticky session (writes ~/.manyagent/active)
  ma session end [--session id]    end the active (or given) session
  ma session list [N] [--since W] [--until W] [--goal G]   browse recent sessions

setup / diagnostics:
  ma dev init                      first-run setup: write ~/.manyagent/env (Bank URL + key)
  ma dev preflight                 validate env / Bank reachability / keys

knowledge-loop verbs (typed INSIDE the wrapped agent, not on this CLI):
  /self-distill                   (Claude Code, Gemini CLI)
  /discuss [@packet] [stance]
  /cross-distill
  /inject [@packet]
  $self-distill, $discuss, ...    (Codex CLI — `/` is reserved for built-ins)

Skills + MCP server install on `ma <agent>` (or up front with `ma agent
register`); the human surface stays one tap (Design Principles §11): the agent
generates the structured post, proposes the ★, and your in-agent permission
prompt is the accept gate (C1).
"""


def _build_parser() -> argparse.ArgumentParser:
    """Build the full argparse tree. Only the reserved groups (``agent`` /
    ``session`` / ``dev``) live here; ``ma <agent> …`` is sniffed in ``main()``
    before argparse sees it (clig.dev: a catch-all dispatch with a reserved
    namespace). Each group's bare form prints that group's help."""
    p = argparse.ArgumentParser(
        prog="ma",
        description="Run your coding agents through manyagent; curate cross-session knowledge.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"ma {__version__}")
    group = p.add_subparsers(dest="group")

    # --- ma agent … : the in-agent tooling (skills + MCP) surface ------------
    agent = group.add_parser("agent", help="install/list the in-agent skills + MCP server")
    agent_sub = agent.add_subparsers(dest="verb")
    ar = agent_sub.add_parser("register", help="install in-agent skills + MCP for an agent")
    ar.add_argument("name")
    au = agent_sub.add_parser("unregister", help="reverse the skill install via the saved manifest")
    au.add_argument("name")
    al = agent_sub.add_parser("list", help="list registered agents")
    al.add_argument("-v", "--verbose", action="store_true", help="show the per-file manifest")

    # --- ma session … : session lifecycle + local browsing ------------------
    session = group.add_parser("session", help="start/end/browse sessions")
    session_sub = session.add_subparsers(dest="verb")
    ss = session_sub.add_parser("start", help="start a sticky session")
    ss.add_argument("goal", nargs="?")
    ss.add_argument("--id")
    se = session_sub.add_parser("end", help="end the active (or given) session")
    se.add_argument("--session")
    sl = session_sub.add_parser("list", help="browse recent sessions")
    sl.add_argument("n", nargs="?", type=int, help="how many to show (default 10)")
    sl.add_argument("--since", help="only sessions created after (ISO date or offset like 7d)")
    sl.add_argument("--until", help="only sessions created before (ISO date or offset like 7d)")
    sl.add_argument("--goal", help="only sessions filed under this goal")

    # --- ma dev … : first-run setup + diagnostics ---------------------------
    dev = group.add_parser("dev", help="first-run setup + diagnostics")
    dev_sub = dev.add_subparsers(dest="verb")
    i = dev_sub.add_parser("init", help="write ~/.manyagent/env (Bank URL + key)")
    i.add_argument("--bank-url")
    i.add_argument("--trusted-key")
    i.add_argument("--anon-key")
    i.add_argument("--cf-access-client-id")
    i.add_argument("--cf-access-client-secret")
    dev_sub.add_parser("preflight", help="validate env / Bank reachability / keys")

    return p


# group → {verb → handler}. ``main()`` resolves ``args.group``/``args.verb``;
# a group with no verb prints that group's help.
_DISPATCH: dict[str, dict[str, Callable[..., Coroutine[Any, Any, int]]]] = {
    "agent": {"register": _do_agent_register, "unregister": _do_agent_unregister, "list": _do_agent_list},
    "session": {"start": _do_start, "end": _do_end, "list": _do_session_list},
    "dev": {"init": _do_init, "preflight": _do_preflight},
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
            f"manyagent: {messages.GUARD_BANK_NOTE.format(env_path=ui.tilde(_user_env_path()))}",
            file=sys.stderr,
        )
        return 1


def _is_agent_token(tok: str) -> bool:
    """True if ``tok`` names a known agent (installed local / builtin / hub) and
    is not a reserved group word — the anchor ``_split_run_args`` uses to locate
    where the goal ends and the agent begins. Never raises (resolve failures and
    the offline hub seam both mean "not an agent here")."""
    if tok in RESERVED:
        return False
    from manyagent.adapters import registry

    try:
        registry.resolve(tok)
        return True
    except Exception:
        return False


def _split_run_args(raw: list[str]) -> tuple[str | None, str, list[str]]:
    """Parse a run line ``ma [--goal G] [GOAL words…] <agent> [agent args…]`` into
    ``(goal, agent, agent_args)``. The agent is the first token that names a known
    agent; tokens before it form the goal (or pass ``--goal``/``-g``, the
    unambiguous escape hatch — clig.dev prefers flags). SystemExit with a helpful
    hint when nothing resolves to an agent, mapping a removed top-level verb to
    its new home where we can."""
    goal_flag: str | None = None
    goal_words: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        t = raw[i]
        if t in ("--goal", "-g"):
            if i + 1 >= n:
                raise SystemExit('`--goal` needs a value, e.g. `ma --goal "fix bug" claude`')
            goal_flag, i = raw[i + 1], i + 2
            continue
        if t.startswith("--goal="):
            goal_flag, i = t.split("=", 1)[1], i + 1
            continue
        if not t.startswith("-") and _is_agent_token(t):
            goal = goal_flag if goal_flag is not None else (" ".join(goal_words) or None)
            return goal, t, raw[i + 1 :]
        if t.startswith("-"):
            raise SystemExit(f"unknown option {t!r} before an agent name — run `ma -h` for usage")
        # A bare non-agent word: a removed top-level verb (only as the first
        # token) gets a redirect; otherwise it's part of the goal.
        if not goal_words and goal_flag is None and t in _MOVED:
            raise SystemExit(messages.RUN_VERB_MOVED.format(old=t, new=_MOVED[t]))
        goal_words.append(t)
        i += 1
    raise SystemExit(messages.RUN_NO_AGENT.format(line=" ".join(raw)))


def _dispatch_parsed(parser: argparse.ArgumentParser, args: argparse.Namespace, io: tuple[In, Out]) -> int:
    """Route a parsed reserved-group command. A bare group (``ma agent``) prints
    that group's help; a group+verb runs its handler under ``_guard``."""
    group = getattr(args, "group", None)
    if not group:
        parser.print_help()
        return 0
    verb = getattr(args, "verb", None)
    if not verb:
        with contextlib.suppress(SystemExit):
            parser.parse_args([group, "--help"])  # argparse prints the group's help
        return 0
    return _guard(_DISPATCH[group][verb](args, bank=get_bank(), io=io))


def main(argv: list[str] | None = None) -> int:
    """Console-script entrypoint. Returns a process exit code."""
    raw = list(argv) if argv is not None else sys.argv[1:]
    io: tuple[In, Out] = (input, print)

    if not raw:
        _build_parser().print_help()
        return 0

    signal.signal(signal.SIGINT, _sigint_handler)
    first = raw[0]

    # Reserved group, or a help/version flag → argparse. Everything else is a
    # run line: `ma [--goal G] [goal…] <agent> [args…]` (the default path).
    if first in RESERVED or first in ("-h", "--help", "--version"):
        parser = _build_parser()
        return _dispatch_parsed(parser, parser.parse_args(raw), io)

    goal, agent, agent_args = _split_run_args(raw)
    return _guard(_do_run_agent(agent, agent_args, goal, bank=get_bank(), io=io))


if __name__ == "__main__":
    raise SystemExit(main())
