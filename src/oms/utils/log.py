"""Structured logging — owns the ``[DEBUG]``/``[INFO]`` line prefixes the
Overview transcripts and tests assert on.
"""

from __future__ import annotations

import logging
import sys

_ROOT = "oms"
_FORMAT = "[%(levelname)s] %(message)s"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``oms`` namespace.

    Lines render as ``[INFO] message`` / ``[DEBUG] message`` — the exact prefix
    the transcripts rely on (``%(levelname)s`` is ``INFO``/``DEBUG``).
    """
    full = f"{_ROOT}.{name}" if name else _ROOT
    logger = logging.getLogger(full)
    root = logging.getLogger(_ROOT)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return logger
