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

# Bank (Supabase) connection + the write identities (manyagent.bank, M2).
# The default is the HOSTED Bank (Cloudflare tunnel) so a bare
# `uv tool install manyagent` works with zero configuration — a localhost default
# is dead-on-arrival on every machine that isn't running `make bank-up`
# (local dev points back at http://127.0.0.1:54421 via manyagent.env; the local
# stack's 544xx ports coexist with a sibling datasmith Supabase).
MANYAGENT_BANK_URL_DEFAULT = "https://db-swarms.formulacode.org"


def _demo_jwt(role: str) -> str:
    """Mint the Supabase DEMO-stack JWT for ``role`` — the ``admin/admin`` of
    Supabase: the signing secret below is the PUBLIC demo default from
    Supabase's docs, so the minted tokens are public knowledge, not secrets
    (anon = read-only role; authenticated = the RLS-enforced trusted writer
    of migration 00004 — never service_role). They are the offline fallback
    for the hosted pre-alpha Bank, which has not rotated its secret; the
    CURRENT connection is published at /.well-known/manyagent.json
    (manyagent.web) and fetched/cached by `ma init`. Rotating the hosted
    secret invalidates these mechanically — the intended failure mode (the
    CLI error hint routes to `ma init`, which fetches the new keys).

    Derived, never hardcoded: **no key-shaped literal may live in this
    repo** — a literal would train scanners to cry wolf and train future
    edits to paste a REAL key where the demo one sat.
    """
    import base64
    import hashlib
    import hmac
    import json

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    secret = b"super-secret-jwt-token-with-at-least-32-characters-long"  # Supabase's published demo default
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    # Claim shape/order mirrors the demo keys `npx supabase status` prints.
    payload = b64url(
        json.dumps({"iss": "supabase-demo", "role": role, "exp": 1983812996}, separators=(",", ":")).encode()
    )
    sig = b64url(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


MANYAGENT_BANK_URL: str = resolve("MANYAGENT_BANK_URL", MANYAGENT_BANK_URL_DEFAULT)
MANYAGENT_BANK_ANON_KEY: str = resolve("MANYAGENT_BANK_ANON_KEY", _demo_jwt("anon"))
MANYAGENT_BANK_TRUSTED_KEY: str = resolve("MANYAGENT_BANK_TRUSTED_KEY", _demo_jwt("authenticated"))

# What the deployment PUBLISHES at /.well-known/manyagent.json (manyagent.web).
# Deliberately defaulted to the derived demo JWTs, never to the resolved
# MANYAGENT_BANK_* above — the web host's own env may hold a privileged key
# (service_role locally) that must never reach the published document. After
# rotating the hosted stack's JWT secret, set these on the web deployment;
# clients pick the new values up at their next `ma init`.
MANYAGENT_WEB_PUBLISHED_BANK_URL: str = resolve("MANYAGENT_WEB_PUBLISHED_BANK_URL", MANYAGENT_BANK_URL_DEFAULT)
MANYAGENT_WEB_PUBLISHED_ANON_KEY: str = resolve("MANYAGENT_WEB_PUBLISHED_ANON_KEY", _demo_jwt("anon"))
MANYAGENT_WEB_PUBLISHED_TRUSTED_KEY: str = resolve("MANYAGENT_WEB_PUBLISHED_TRUSTED_KEY", _demo_jwt("authenticated"))
