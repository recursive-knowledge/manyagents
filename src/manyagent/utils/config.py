"""Config / env loading and the tunable-constant convention.

datasmith's rule, adopted verbatim as ``MANYAGENT_``: any module-level knob (timeout,
retry, cap, window, threshold) is overridable from the environment without a
code change, ``MANYAGENT_``-prefixed and greppable (Design Principles §8).

``manyagent.__init__.setup_environment()`` loads ``manyagent.env`` into ``os.environ`` via
``dotenv.load_dotenv`` (which does not overwrite already-set process vars), so
reading ``os.environ`` here yields the precedence:

    CLI flag  >  process env  >  manyagent.env  >  built-in default

implemented once in :func:`resolve` so the CLI and the API agree.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


def as_bool(raw: str) -> bool:
    """Truthy unless the value is one of ``0``, ``false``, ``no``, `` `` (ci)."""
    return raw.strip().lower() not in ("0", "false", "no", "")


def resolve(
    name: str,
    default: _T,
    *,
    cast: Callable[[str], _T] = str,  # type: ignore[assignment]
    cli_value: str | None = None,
) -> _T:
    """Resolve one tunable with the canonical precedence.

    ``cli_value`` (when not ``None``) is the CLI override and wins outright;
    otherwise ``os.environ[name]`` (process env or manyagent.env) is cast; otherwise
    the built-in ``default`` is returned unchanged.
    """
    if cli_value is not None:
        return cast(cli_value)
    raw = os.environ.get(name)
    if raw is not None:
        return cast(raw)
    return default


# --- The canonical MANYAGENT_ tunables (snapshot at import; resolve() for dynamic) ---
# Implemented here once; consumed by manyagent.bank/capture/distill/cli per milestone.

MANYAGENT_DISTILL_TIMEOUT_S: int = resolve("MANYAGENT_DISTILL_TIMEOUT_S", 600, cast=int)
# Byte budget for the session-trace excerpt rendered into the /self-distill
# and /discuss post prompts (manyagent._handlers `_trace_context`): once the wrapped
# agent exits, the conversation lives in the bound transcript / raw packet,
# not in any model's head, so the prompt must carry it. Kept well under the
# POSIX per-arg limit (the headless shell-out passes the prompt via argv).
MANYAGENT_DISTILL_CONTEXT_MAX_BYTES: int = resolve("MANYAGENT_DISTILL_CONTEXT_MAX_BYTES", 60_000, cast=int)
MANYAGENT_TRACE_MAX_BYTES: int = resolve("MANYAGENT_TRACE_MAX_BYTES", 2 * 1024 * 1024, cast=int)
MANYAGENT_CROSSDISTILL_WINDOW_DAYS: int = resolve("MANYAGENT_CROSSDISTILL_WINDOW_DAYS", 30, cast=int)
# Stale-goal nudge: offer /cross-distill at `manyagent start` once this many
# reflections accumulated under the goal SINCE its newest bundle (manyagent.cli).
MANYAGENT_CROSS_NUDGE_MIN: int = resolve("MANYAGENT_CROSS_NUDGE_MIN", 3, cast=int)
MANYAGENT_INJECT_PREVIEW_HEAD_TOKENS: int = resolve("MANYAGENT_INJECT_PREVIEW_HEAD_TOKENS", 100, cast=int)
MANYAGENT_INJECT_PREVIEW_TAIL_TOKENS: int = resolve("MANYAGENT_INJECT_PREVIEW_TAIL_TOKENS", 100, cast=int)
# Commit-gate post preview: per-field character cap before truncation (the
# full text stays one `d` keypress away); 0 disables truncation (manyagent.utils.ui).
MANYAGENT_POST_PREVIEW_FIELD_CHARS: int = resolve("MANYAGENT_POST_PREVIEW_FIELD_CHARS", 280, cast=int)
MANYAGENT_CURATOR_MODE: str = resolve("MANYAGENT_CURATOR_MODE", "auto")  # local | server | auto
MANYAGENT_CURATOR_SERVER_URL: str = resolve("MANYAGENT_CURATOR_SERVER_URL", "")
MANYAGENT_RATING_PROMPT: bool = resolve("MANYAGENT_RATING_PROMPT", True, cast=as_bool)
MANYAGENT_REUSE_WEIGHT: float = resolve("MANYAGENT_REUSE_WEIGHT", 1.0, cast=float)
MANYAGENT_NONINTERACTIVE: bool = resolve("MANYAGENT_NONINTERACTIVE", False, cast=as_bool)
# Goal bucket assigned by `manyagent start` when no goal is given and the
# continuity offer is declined — every session carries a goal (manyagent.cli).
MANYAGENT_DEFAULT_GOAL: str = resolve("MANYAGENT_DEFAULT_GOAL", "misc")
MANYAGENT_COLOR: str = resolve("MANYAGENT_COLOR", "auto")  # auto | always | never (manyagent.utils.ui)

# manyagent.web read API (M9): default + max page size for cursor pagination, and
# the `manyagent.web.server.serve()` bind address.
MANYAGENT_WEB_PAGE_LIMIT: int = resolve("MANYAGENT_WEB_PAGE_LIMIT", 50, cast=int)
MANYAGENT_WEB_MAX_PAGE_LIMIT: int = resolve("MANYAGENT_WEB_MAX_PAGE_LIMIT", 200, cast=int)
MANYAGENT_WEB_HOST: str = resolve("MANYAGENT_WEB_HOST", "127.0.0.1")
MANYAGENT_WEB_PORT: int = resolve("MANYAGENT_WEB_PORT", 8000, cast=int)
# The hosted viewer's base URL — what the CLI prints in `open:` links. The
# deployment may move, so it is a tunable, not a constant; set it empty to
# fall back to the local bind (`MANYAGENT_WEB_HOST`/`MANYAGENT_WEB_PORT`) for local dev.
MANYAGENT_WEB_PUBLIC_URL: str = resolve("MANYAGENT_WEB_PUBLIC_URL", "https://swarms.formulacode.org")
# Pre-alpha (2026-06-10; manyagent.web.md Decision log): scrubbed raw trace bodies
# are PUBLIC in the viewer (asciinema replay + plain-text inspection on the
# /t/ trace pages). Set 0 to restore the trusted-only gate; the DB-side
# rollback is revoking migration 00008's anon grant on `traces`.
MANYAGENT_WEB_PUBLIC_RAW: str = resolve("MANYAGENT_WEB_PUBLIC_RAW", "1")

# Local-LLM fallback (OpenAI-compatible); manyagent ships no keys.
MANYAGENT_LLM_BASE_URL: str = resolve("MANYAGENT_LLM_BASE_URL", "")
MANYAGENT_LLM_API_KEY: str = resolve("MANYAGENT_LLM_API_KEY", "")
MANYAGENT_LLM_MODEL: str = resolve("MANYAGENT_LLM_MODEL", "")

# Bank (Supabase) connection + the three write identities (manyagent.bank, M2).
# Local Bank ports are 544xx (not the Supabase 543xx defaults) so manyagent's stack
# coexists with a sibling datasmith Supabase on the same host.
MANYAGENT_BANK_URL: str = resolve("MANYAGENT_BANK_URL", "http://127.0.0.1:54421")
MANYAGENT_BANK_ANON_KEY: str = resolve("MANYAGENT_BANK_ANON_KEY", "")
MANYAGENT_BANK_TRUSTED_KEY: str = resolve("MANYAGENT_BANK_TRUSTED_KEY", "")
