"""oma.utils — config, session-id codec, LLM provider, logging (M1).

Small, depended on by everything (the ``ds.utils`` analog).
"""

from __future__ import annotations

from oma.utils import config, log, provider, sid
from oma.utils.log import get_logger
from oma.utils.provider import (
    OpenAICompatibleProvider,
    Provider,
    ProviderUnavailable,
    RateLimit,
    rate_limit_signal,
    resolve,
)

__all__ = [
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderUnavailable",
    "RateLimit",
    "config",
    "get_logger",
    "log",
    "provider",
    "rate_limit_signal",
    "resolve",
    "sid",
]
