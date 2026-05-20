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
OMS_TRACE_MAX_BYTES: int = resolve("OMS_TRACE_MAX_BYTES", 2 * 1024 * 1024, cast=int)
OMS_CROSSDISTILL_WINDOW_DAYS: int = resolve("OMS_CROSSDISTILL_WINDOW_DAYS", 30, cast=int)
OMS_INJECT_PREVIEW_HEAD_TOKENS: int = resolve("OMS_INJECT_PREVIEW_HEAD_TOKENS", 100, cast=int)
OMS_INJECT_PREVIEW_TAIL_TOKENS: int = resolve("OMS_INJECT_PREVIEW_TAIL_TOKENS", 100, cast=int)
OMS_CURATOR_MODE: str = resolve("OMS_CURATOR_MODE", "auto")  # local | server | auto
OMS_CURATOR_SERVER_URL: str = resolve("OMS_CURATOR_SERVER_URL", "")
OMS_RATING_PROMPT: bool = resolve("OMS_RATING_PROMPT", True, cast=as_bool)
OMS_REUSE_WEIGHT: float = resolve("OMS_REUSE_WEIGHT", 1.0, cast=float)
OMS_NONINTERACTIVE: bool = resolve("OMS_NONINTERACTIVE", False, cast=as_bool)

# oms.web read API (M9): default + max page size for cursor pagination, and
# the `oms.web.server.serve()` bind address.
OMS_WEB_PAGE_LIMIT: int = resolve("OMS_WEB_PAGE_LIMIT", 50, cast=int)
OMS_WEB_MAX_PAGE_LIMIT: int = resolve("OMS_WEB_MAX_PAGE_LIMIT", 200, cast=int)
OMS_WEB_HOST: str = resolve("OMS_WEB_HOST", "127.0.0.1")
OMS_WEB_PORT: int = resolve("OMS_WEB_PORT", 8000, cast=int)

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
