"""manyagent.utils — config, session-id codec, LLM provider, logging (M1).

Small, depended on by everything (the ``ds.utils`` analog).
"""

from __future__ import annotations

from manyagent.utils import config, log, provider, sid, slug, ui
from manyagent.utils.log import get_logger
from manyagent.utils.provider import (
    OpenAICompatibleProvider,
    Provider,
    ProviderUnavailable,
    RateLimit,
    rate_limit_signal,
    resolve,
)
from manyagent.utils.slug import normalize_goal, slugify

__all__ = [
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderUnavailable",
    "RateLimit",
    "config",
    "get_logger",
    "log",
    "normalize_goal",
    "provider",
    "rate_limit_signal",
    "resolve",
    "sid",
    "slug",
    "slugify",
    "ui",
]
