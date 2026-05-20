"""oma.web — read-only FastAPI surface + static viewer (M9).

The public read window over the Knowledge Bank: the role ``ds.publish`` plays
in datasmith. Read-only is **DB-enforced** (the ``public`` grant), not merely
app-enforced; the anon API can never return a raw trace body (oma.web.md).
"""

from __future__ import annotations

from oma.web.api import create_app
from oma.web.server import build_app, serve

__all__ = ["build_app", "create_app", "serve"]
