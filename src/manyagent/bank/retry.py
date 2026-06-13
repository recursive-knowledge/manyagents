"""Async exponential-backoff retry (ported from datasmith ``with_backoff``).

Wraps Bank network calls so a transient PostgREST/HTTP failure does not abort
a session write (manyagent.bank.md Verification: "retry wrapper").

``ParamSpec`` + ``Coroutine`` return type keep the decorated method's exact
signature, so a ``@with_backoff()``-decorated ``SupabaseBank`` still
structurally satisfies the async :class:`~manyagent.bank.base.Bank` Protocol.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


class NonRetryableError(Exception):
    """Marker base: a failure retrying cannot fix (a missing Bank key, a
    misconfigured identity). ``with_backoff`` re-raises these immediately
    instead of burning the full backoff schedule (~3.5s at the defaults)
    in front of the same error."""


def with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.5,
    *,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[_P, Awaitable[_T]]], Callable[_P, Coroutine[Any, Any, _T]]]:
    """Decorator: retry an async function with doubling backoff."""

    def decorator(func: Callable[_P, Awaitable[_T]]) -> Callable[_P, Coroutine[Any, Any, _T]]:
        @functools.wraps(func)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            delay = base_delay
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if isinstance(exc, NonRetryableError) or attempt == max_retries:
                        raise
                    await asyncio.sleep(delay)
                    delay *= 2
            raise last_exc  # type: ignore[misc]  # unreachable; satisfies mypy

        return wrapper

    return decorator
