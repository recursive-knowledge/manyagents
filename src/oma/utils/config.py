"""Config / env loading and the tunable-constant convention.

datasmith's rule, adopted verbatim as ``OMA_``: any module-level knob (timeout,
retry, cap, window, threshold) is overridable from the environment without a
code change, ``OMA_``-prefixed and greppable (Design Principles §8).

``oma.__init__.setup_environment()`` loads ``oma.env`` into ``os.environ`` via
``dotenv.load_dotenv`` (which does not overwrite already-set process vars), so
reading ``os.environ`` here yields the precedence:

    CLI flag  >  process env  >  oma.env  >  built-in default

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
    otherwise ``os.environ[name]`` (process env or oma.env) is cast; otherwise
    the built-in ``default`` is returned unchanged.
    """
    if cli_value is not None:
        return cast(cli_value)
    raw = os.environ.get(name)
    if raw is not None:
        return cast(raw)
    return default


# --- The canonical OMA_ tunables (snapshot at import; resolve() for dynamic) ---
# Implemented here once; consumed by oma.bank/capture/distill/cli per milestone.

OMA_DISTILL_TIMEOUT_S: int = resolve("OMA_DISTILL_TIMEOUT_S", 600, cast=int)
OMA_TRACE_MAX_BYTES: int = resolve("OMA_TRACE_MAX_BYTES", 2 * 1024 * 1024, cast=int)
OMA_CROSSDISTILL_WINDOW_DAYS: int = resolve("OMA_CROSSDISTILL_WINDOW_DAYS", 30, cast=int)
OMA_INJECT_PREVIEW_HEAD_TOKENS: int = resolve("OMA_INJECT_PREVIEW_HEAD_TOKENS", 100, cast=int)
OMA_INJECT_PREVIEW_TAIL_TOKENS: int = resolve("OMA_INJECT_PREVIEW_TAIL_TOKENS", 100, cast=int)
OMA_CURATOR_MODE: str = resolve("OMA_CURATOR_MODE", "auto")  # local | server | auto
OMA_CURATOR_SERVER_URL: str = resolve("OMA_CURATOR_SERVER_URL", "")
OMA_RATING_PROMPT: bool = resolve("OMA_RATING_PROMPT", True, cast=as_bool)
OMA_REUSE_WEIGHT: float = resolve("OMA_REUSE_WEIGHT", 1.0, cast=float)
OMA_NONINTERACTIVE: bool = resolve("OMA_NONINTERACTIVE", False, cast=as_bool)

# oma.web read API (M9): default + max page size for cursor pagination, and
# the `oma.web.server.serve()` bind address.
OMA_WEB_PAGE_LIMIT: int = resolve("OMA_WEB_PAGE_LIMIT", 50, cast=int)
OMA_WEB_MAX_PAGE_LIMIT: int = resolve("OMA_WEB_MAX_PAGE_LIMIT", 200, cast=int)
OMA_WEB_HOST: str = resolve("OMA_WEB_HOST", "127.0.0.1")
OMA_WEB_PORT: int = resolve("OMA_WEB_PORT", 8000, cast=int)

# Local-LLM fallback (OpenAI-compatible); oma ships no keys.
OMA_LLM_BASE_URL: str = resolve("OMA_LLM_BASE_URL", "")
OMA_LLM_API_KEY: str = resolve("OMA_LLM_API_KEY", "")
OMA_LLM_MODEL: str = resolve("OMA_LLM_MODEL", "")

# Bank (Supabase) connection + the three write identities (oma.bank, M2).
# Local Bank ports are 544xx (not the Supabase 543xx defaults) so oma's stack
# coexists with a sibling datasmith Supabase on the same host.
OMA_BANK_URL: str = resolve("OMA_BANK_URL", "http://127.0.0.1:54421")
OMA_BANK_ANON_KEY: str = resolve("OMA_BANK_ANON_KEY", "")
OMA_BANK_TRUSTED_KEY: str = resolve("OMA_BANK_TRUSTED_KEY", "")
