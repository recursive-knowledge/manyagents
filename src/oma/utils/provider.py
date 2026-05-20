"""Local-LLM provider abstraction + rate-limit detection.

Lets ``oma.distill`` use the *user's* model without ``oma`` hosting inference;
``oma`` ships no keys. Resolution order (Settled): adapter ``distill_model()``
hook → configured ``OMA_LLM_*`` OpenAI-compatible fallback → hard error
(never a silent skip; asserted in ``oma.distill``).

Rate-limit detection: the general per-provider signal map is Open
(``oma.utils.md``). The two datasmith-proven schemas (Codex free-text reset,
Claude structured ``rate_limit_event``) are ported here verbatim because
``oma.utils.md`` Verification requires parsing canned Codex/Claude payloads;
unknown input returns ``None``.
"""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

import httpx

from oma.utils import config

# --------------------------------------------------------------------------- #
# Rate-limit detection (ported from datasmith agents/rate_limit.py)
# --------------------------------------------------------------------------- #

_CLAUDE_OK_STATUSES = frozenset({"allowed", "allowed_warning"})

_CODEX_RESET_RE = re.compile(
    r"try again at\s+"
    r"(?P<month>[A-Za-z]{3,9})\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?,\s+"
    r"(?P<year>\d{4})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*"
    r"(?P<ampm>AM|PM)",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass(frozen=True)
class RateLimit:
    """A detected provider budget/limit exhaustion.

    ``reset_at`` is tz-aware UTC when known, else ``None`` ("rate-limited but
    no parseable reset" — the caller picks a default pause).
    """

    provider: str
    reset_at: datetime.datetime | None
    reason: str = ""

    def retry_after_s(self, now: datetime.datetime | None = None) -> float | None:
        """Seconds until the budget clears, or ``None`` if the reset is unknown."""
        if self.reset_at is None:
            return None
        ref = now or datetime.datetime.now(tz=datetime.UTC)
        return max(0.0, (self.reset_at - ref).total_seconds())


def _parse_codex_reset(match: re.Match[str]) -> datetime.datetime:
    month = _MONTHS[match.group("month")[:3].lower()]
    day = int(match.group("day"))
    year = int(match.group("year"))
    hour = int(match.group("hour")) % 12
    if match.group("ampm").upper() == "PM":
        hour += 12
    minute = int(match.group("minute"))
    return datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.UTC)


def _detect_codex(raw: str) -> RateLimit | None:  # noqa: C901
    if "usage limit" not in raw.lower():
        return None
    for line in reversed(raw.splitlines()[-20:]):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ""
        if evt.get("type") == "error":
            msg = evt.get("message") or ""
        elif evt.get("type") == "turn.failed":
            msg = (evt.get("error") or {}).get("message") or ""
        if not msg or "usage limit" not in msg.lower():
            continue
        m = _CODEX_RESET_RE.search(msg)
        if not m:
            return RateLimit("codex", None, "usage limit; no parseable reset")
        try:
            return RateLimit("codex", _parse_codex_reset(m), "usage limit")
        except (ValueError, KeyError):
            return RateLimit("codex", None, "usage limit; bad reset string")
    m = _CODEX_RESET_RE.search(raw)
    if m:
        try:
            return RateLimit("codex", _parse_codex_reset(m), "usage limit")
        except (ValueError, KeyError):
            return RateLimit("codex", None, "usage limit; bad reset string")
    return RateLimit("codex", None, "usage limit; no structured event")


def _detect_claude(raw: str) -> RateLimit | None:  # noqa: C901
    if "rate_limit_event" not in raw and "rate_limit" not in raw.lower():
        return None
    last_reset: int | None = None
    last_overage_reset: int | None = None
    blocked = False
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") != "rate_limit_event":
            continue
        info = evt.get("rate_limit_info") or {}
        if isinstance(info.get("resetsAt"), int | float):
            last_reset = int(info["resetsAt"])
        if isinstance(info.get("overageResetsAt"), int | float):
            last_overage_reset = int(info["overageResetsAt"])
        status = info.get("status")
        overage = info.get("overageStatus")
        if status and status not in _CLAUDE_OK_STATUSES:
            blocked = True
        if overage and overage not in _CLAUDE_OK_STATUSES:
            blocked = True
    if not blocked:
        return None
    epoch = last_reset or last_overage_reset
    if epoch is None:
        return RateLimit("claude", None, "rate limited; no reset epoch")
    return RateLimit("claude", datetime.datetime.fromtimestamp(epoch, tz=datetime.UTC), "rate limited")


def rate_limit_signal(raw_error: str, *, provider: str | None = None) -> RateLimit | None:
    """Map a raw CLI/provider error stream to a :class:`RateLimit` or ``None``.

    ``provider`` routes to the known schema; when omitted, the Codex and Claude
    detectors are tried in turn (so canned payloads parse without a hint).
    The general per-provider map is Open (``oma.utils.md``).
    """
    if not raw_error:
        return None
    if provider is not None:
        p = provider.lower()
        if "codex" in p:
            return _detect_codex(raw_error)
        if "claude" in p:
            return _detect_claude(raw_error)
        return None
    return _detect_codex(raw_error) or _detect_claude(raw_error)


# --------------------------------------------------------------------------- #
# Provider abstraction + resolution
# --------------------------------------------------------------------------- #


@runtime_checkable
class Provider(Protocol):
    """The model seam ``oma.distill`` curates through."""

    name: str

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str: ...

    def rate_limit_signal(self, raw_error: str) -> RateLimit | None: ...


class ProviderUnavailable(RuntimeError):
    """No usable model: neither an adapter ``distill_model()`` hook nor a
    configured ``OMA_LLM_*`` fallback (``oma`` ships no keys)."""


@dataclass
class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible chat client (the ``OMA_LLM_*`` fallback)."""

    base_url: str
    model: str
    api_key: str = ""
    name: str = "oma-llm"
    timeout_s: float = 60.0

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        resp = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])

    def rate_limit_signal(self, raw_error: str) -> RateLimit | None:
        return rate_limit_signal(raw_error, provider=self.name)


@dataclass
class _CallableProvider:
    """Wraps an adapter ``distill_model()`` callable as a :class:`Provider`."""

    _complete: Callable[..., str]
    name: str = "adapter-model"

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        return str(self._complete(prompt, max_tokens=max_tokens))

    def rate_limit_signal(self, raw_error: str) -> RateLimit | None:
        return rate_limit_signal(raw_error)


def resolve(adapter: Any | None = None) -> Provider:
    """Resolve the model ``oma.distill`` curates through.

    Order (Settled): adapter ``distill_model()`` hook → ``OMA_LLM_*``
    OpenAI-compatible fallback → :class:`ProviderUnavailable`. ``oma.adapters``
    (M5) supplies the adapter; until then callers pass ``adapter=None``.
    """
    hook = getattr(adapter, "distill_model", None) if adapter is not None else None
    if callable(hook):
        produced = hook()
        if produced is None:
            pass
        elif isinstance(produced, Provider):
            return produced
        elif callable(getattr(produced, "complete", None)):
            return cast(Provider, produced)  # duck-typed Provider
        elif callable(produced):
            return _CallableProvider(produced, name=getattr(adapter, "name", "adapter-model"))

    base_url = config.resolve("OMA_LLM_BASE_URL", "")
    model = config.resolve("OMA_LLM_MODEL", "")
    if base_url and model:
        return OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=config.resolve("OMA_LLM_API_KEY", ""),
        )

    raise ProviderUnavailable(
        "No distill model: an adapter must implement distill_model() or "
        "OMA_LLM_BASE_URL + OMA_LLM_MODEL must be configured (oma ships no keys)."
    )
