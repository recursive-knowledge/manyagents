"""Config / env loading and the tunable-constant convention.

datasmith's rule, adopted verbatim as ``OMS_``: any module-level knob (timeout,
retry, cap, window, threshold) is overridable from the environment without a
code change, ``OMS_``-prefixed and greppable (Design Principles §8).

``oms.__init__.setup_environment()`` loads ``oms.env`` into ``os.environ`` via
``dotenv.load_dotenv`` (which does not overwrite already-set process vars), so
reading ``os.environ`` here yields the precedence:

    CLI flag  >  process env  >  oms.env  >  built-in default

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
    otherwise ``os.environ[name]`` (process env or oms.env) is cast; otherwise
    the built-in ``default`` is returned unchanged.
    """
    if cli_value is not None:
        return cast(cli_value)
    raw = os.environ.get(name)
    if raw is not None:
        return cast(raw)
    return default


# --- The canonical OMS_ tunables (snapshot at import; resolve() for dynamic) ---
# Implemented here once; consumed by oms.bank/capture/distill/cli per milestone.

OMS_DISTILL_TIMEOUT_S: int = resolve("OMS_DISTILL_TIMEOUT_S", 600, cast=int)
# Byte budget for the session-trace excerpt rendered into the /self-distill
# and /discuss post prompts (oms._handlers `_trace_context`): once the wrapped
# agent exits, the conversation lives in the bound transcript / raw packet,
# not in any model's head, so the prompt must carry it. Kept well under the
# POSIX per-arg limit (the headless shell-out passes the prompt via argv).
OMS_DISTILL_CONTEXT_MAX_BYTES: int = resolve("OMS_DISTILL_CONTEXT_MAX_BYTES", 60_000, cast=int)
OMS_TRACE_MAX_BYTES: int = resolve("OMS_TRACE_MAX_BYTES", 2 * 1024 * 1024, cast=int)
OMS_CROSSDISTILL_WINDOW_DAYS: int = resolve("OMS_CROSSDISTILL_WINDOW_DAYS", 30, cast=int)
# Stale-goal nudge: offer /cross-distill at `oms start` once this many
# reflections accumulated under the goal SINCE its newest bundle (oms.cli).
OMS_CROSS_NUDGE_MIN: int = resolve("OMS_CROSS_NUDGE_MIN", 3, cast=int)
OMS_INJECT_PREVIEW_HEAD_TOKENS: int = resolve("OMS_INJECT_PREVIEW_HEAD_TOKENS", 100, cast=int)
OMS_INJECT_PREVIEW_TAIL_TOKENS: int = resolve("OMS_INJECT_PREVIEW_TAIL_TOKENS", 100, cast=int)
# Commit-gate post preview: per-field character cap before truncation (the
# full text stays one `d` keypress away); 0 disables truncation (oms.utils.ui).
OMS_POST_PREVIEW_FIELD_CHARS: int = resolve("OMS_POST_PREVIEW_FIELD_CHARS", 280, cast=int)
OMS_CURATOR_MODE: str = resolve("OMS_CURATOR_MODE", "auto")  # local | server | auto
OMS_CURATOR_SERVER_URL: str = resolve("OMS_CURATOR_SERVER_URL", "")
OMS_RATING_PROMPT: bool = resolve("OMS_RATING_PROMPT", True, cast=as_bool)
OMS_REUSE_WEIGHT: float = resolve("OMS_REUSE_WEIGHT", 1.0, cast=float)
OMS_NONINTERACTIVE: bool = resolve("OMS_NONINTERACTIVE", False, cast=as_bool)
# Goal bucket assigned by `oms start` when no goal is given and the
# continuity offer is declined — every session carries a goal (oms.cli).
OMS_DEFAULT_GOAL: str = resolve("OMS_DEFAULT_GOAL", "misc")
OMS_COLOR: str = resolve("OMS_COLOR", "auto")  # auto | always | never (oms.utils.ui)

# oms.web read API (M9): default + max page size for cursor pagination, and
# the `oms.web.server.serve()` bind address.
OMS_WEB_PAGE_LIMIT: int = resolve("OMS_WEB_PAGE_LIMIT", 50, cast=int)
OMS_WEB_MAX_PAGE_LIMIT: int = resolve("OMS_WEB_MAX_PAGE_LIMIT", 200, cast=int)
OMS_WEB_HOST: str = resolve("OMS_WEB_HOST", "127.0.0.1")
OMS_WEB_PORT: int = resolve("OMS_WEB_PORT", 8000, cast=int)
# The hosted viewer's base URL — what the CLI prints in `open:` links. The
# deployment may move, so it is a tunable, not a constant; set it empty to
# fall back to the local bind (`OMS_WEB_HOST`/`OMS_WEB_PORT`) for local dev.
OMS_WEB_PUBLIC_URL: str = resolve("OMS_WEB_PUBLIC_URL", "https://swarms.formulacode.org")
# Pre-alpha (2026-06-10; oms.web.md Decision log): scrubbed raw trace bodies
# are PUBLIC in the viewer (asciinema replay + plain-text inspection on the
# /t/ trace pages). Set 0 to restore the trusted-only gate; the DB-side
# rollback is revoking migration 00008's anon grant on `traces`.
OMS_WEB_PUBLIC_RAW: str = resolve("OMS_WEB_PUBLIC_RAW", "1")

# Local-LLM fallback (OpenAI-compatible); oms ships no keys.
OMS_LLM_BASE_URL: str = resolve("OMS_LLM_BASE_URL", "")
OMS_LLM_API_KEY: str = resolve("OMS_LLM_API_KEY", "")
OMS_LLM_MODEL: str = resolve("OMS_LLM_MODEL", "")

# Bank (Supabase) connection + the three write identities (oms.bank, M2).
# Local Bank ports are 544xx (not the Supabase 543xx defaults) so oms's stack
# coexists with a sibling datasmith Supabase on the same host.
OMS_BANK_URL: str = resolve("OMS_BANK_URL", "http://127.0.0.1:54421")
OMS_BANK_ANON_KEY: str = resolve("OMS_BANK_ANON_KEY", "")
OMS_BANK_TRUSTED_KEY: str = resolve("OMS_BANK_TRUSTED_KEY", "")
