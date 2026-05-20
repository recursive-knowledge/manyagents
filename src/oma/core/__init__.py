"""oma.core — the model layer: frozen Pydantic value objects + Collection (M3).

Nouns are four: Session, Goal, Agent, Packet. Packet taxonomy: raw | post |
distill. Explicit async ``.fetch()`` hydration; no per-instance ``__getattr__``
(Design Principles §4).
"""

from __future__ import annotations

from oma.core.collection import Collection
from oma.core.models import (
    Agent,
    Goal,
    KnowledgePacket,
    Packet,
    Session,
    clear_packet_cache,
)

__all__ = [
    "Agent",
    "Collection",
    "Goal",
    "KnowledgePacket",
    "Packet",
    "Session",
    "clear_packet_cache",
]
