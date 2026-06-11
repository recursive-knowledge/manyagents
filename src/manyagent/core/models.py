"""Frozen Pydantic value objects: Session, Goal, Agent, Packet, and the
KnowledgePacket wire shape.

Explicit async ``.fetch()`` hydration (cache → manyagent.bank). A bare ``Packet(...)``
constructs the value object and does **no I/O**; only ``.fetch()`` touches the
Bank. No per-instance ``__getattr__`` dispatch (Design Principles §4) — derived
values are plain properties. ``goal=None`` is valid everywhere (open-endedness).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from manyagent.bank import Bank, get_bank
from manyagent.core.collection import Collection

_PACKET_TYPES = {"raw", "post", "distill"}
_KINDS = {"reflection", "reply"}
_STANCES = {"agree", "disagree", "synthesize"}
_SCOPES = {"per_goal", "cross_goal"}

# Process-local hydration cache (cache → Bank), keyed by (bank.cache_key, id)
# so two Banks (different identity / dev vs. test) never collide. Tests call
# clear_packet_cache().
_PACKET_CACHE: dict[tuple[str, str], Packet] = {}


def clear_packet_cache() -> None:
    """Drop the hydration cache (test/ops hook)."""
    _PACKET_CACHE.clear()


class Goal(BaseModel):
    """A soft, optional scope label — the swarms ``task`` analog minus the
    oracle. Never gates anything (manyagent.core.md)."""

    model_config = ConfigDict(frozen=True)
    label: str


class Agent(BaseModel):
    """A registered adapter instance; mostly derived bookkeeping. Constructible
    from just ``id`` (a real canonical id, or the ``online``/``curator``
    sentinels) with no I/O."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    id: str
    session_id: str | None = None
    adapter: str | None = None
    seq: int | None = None
    # Registration timestamp from the agents row (DB-assigned). Distinct from
    # ``start_date`` (which collapses registration + first packet). Populated
    # whenever the row dict carries it.
    created_at: datetime | None = None
    # Derived activity span for the manyagent.web ``GET /s/{session}/agents`` route
    # (manyagent.web.md). Defaulted None; populated only by :meth:`from_activity`.
    start_date: datetime | None = None
    end_date: datetime | None = None

    @classmethod
    def from_activity(cls, row: dict[str, Any], *, packets: Iterable[dict[str, Any]] = ()) -> Agent:
        """Build an Agent with a derived activity span. Pure (no I/O): the
        ``manyagent.web`` route fetches the agent + packet rows and hands them here,
        so the route stays a dumb orchestrator and the derivation lives in the
        frozen model (manyagent.web.md; Design Principles §3, §4).

        ``start_date`` is the earliest timestamp known for the agent (its
        registration time, or its first packet if earlier); ``end_date`` is its
        last *activity* — the latest packet it produced — falling back to
        ``start_date`` when it produced nothing (a registration timestamp is a
        start event and can never bound the *end* of activity). Both are None
        only when no timestamp exists anywhere.
        """
        aid = row["id"]

        def _key(v: Any) -> str:
            return v.isoformat() if isinstance(v, datetime) else str(v)

        pkt_times = [p["created_at"] for p in packets if p.get("agent_id") == aid and p.get("created_at") is not None]
        start_candidates: list[Any] = list(pkt_times)
        if row.get("created_at") is not None:
            start_candidates.append(row["created_at"])
        start = min(start_candidates, key=_key) if start_candidates else None
        end = max(pkt_times, key=_key) if pkt_times else start
        return cls(
            id=aid,
            session_id=row.get("session_id"),
            adapter=row.get("adapter"),
            seq=row.get("seq"),
            created_at=row.get("created_at"),
            start_date=start,
            end_date=end,
        )


class Session(BaseModel):
    """A collaboration container several agents join. Not a task: no verifier,
    no solved-state. ``goal`` is the soft scope key (nullable)."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    id: str
    goal: str | None = None
    status: str = "active"
    created_at: datetime | None = None

    async def agents(self, *, bank: Bank | None = None) -> Collection[Agent]:
        b = bank or get_bank()
        rows = await b.list_agents(self.id)
        return Collection(Agent(**r) for r in rows)

    async def packets(self, *, bank: Bank | None = None) -> Collection[Packet]:
        b = bank or get_bank()
        rows = await b.list_packets(session_id=self.id)
        return Collection(Packet(**r) for r in rows)

    async def posts(self, goal: str | None = None, *, bank: Bank | None = None) -> Collection[Packet]:
        """Posts in this session. ``goal=None`` lists all (ungoaled included);
        a goal filters to exactly that goal's posts."""
        b = bank or get_bank()
        rows = await b.list_packets(session_id=self.id, type="post", goal=goal)
        return Collection(Packet(**r) for r in rows)

    async def distills(self, goal: str | None = None, *, bank: Bank | None = None) -> Collection[Packet]:
        b = bank or get_bank()
        rows = await b.list_packets(session_id=self.id, type="distill", goal=goal)
        return Collection(Packet(**r) for r in rows)


class _PacketFields(BaseModel):
    """Shared field set for Packet and the KnowledgePacket wire shape."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str  # "{session_id}/{uuid}"
    type: str  # raw | post | distill
    agent_id: str | None  # canonical id | "online" | "curator" | None
    goal: str | None = None  # soft scope (None = ungoaled)
    created_at: datetime | None = None
    quarantined: bool = False

    # --- post (manyagent.forum) ---
    kind: str | None = None  # reflection | reply
    reply_to: str | None = None
    stance: str | None = None  # agree | disagree | synthesize
    structured: dict[str, Any] | None = None
    rating: int | None = None  # 1..5 | None (unrated valid)

    # --- distill (manyagent.distill curator) ---
    scope: str | None = None  # per_goal | cross_goal
    bundle: dict[str, Any] | None = None
    parents: list[str] = []
    curator: str | None = None  # local | server
    preference: str | None = None  # accept | reject | None (distill only)
    parent_attempt: str | None = None

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in _PACKET_TYPES:
            raise ValueError(f"bad packet type {v!r}; expected one of {sorted(_PACKET_TYPES)}")
        return v

    @field_validator("rating")
    @classmethod
    def _check_rating(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 5):
            raise ValueError(f"rating must be None or 1..5, got {v!r}")
        return v

    @model_validator(mode="after")
    def _check_shape(self) -> _PacketFields:
        if self.kind == "reply" and (self.reply_to is None or self.stance is None):
            raise ValueError("a reply requires reply_to and stance")
        if self.stance is not None and self.stance not in _STANCES:
            raise ValueError(f"bad stance {self.stance!r}; expected one of {sorted(_STANCES)}")
        if self.kind is not None and self.kind not in _KINDS:
            raise ValueError(f"bad kind {self.kind!r}; expected one of {sorted(_KINDS)}")
        if self.type == "distill" and (self.scope is None or self.bundle is None):
            raise ValueError("a distill requires scope and bundle")
        if self.scope is not None and self.scope not in _SCOPES:
            raise ValueError(f"bad scope {self.scope!r}; expected one of {sorted(_SCOPES)}")
        if self.preference is not None and self.type != "distill":
            # C1 (manyagent.core.md:70/98): preference=accept|reject is distill-only,
            # set via /cross-distill — NEVER on a post. A rejected /self-distill
            # post is not persisted (the agent is re-prompted), so a post must
            # never carry preference. Mechanical, past the manyagent.forum parser.
            raise ValueError("preference is distill-only (manyagent.core.md; C1) — a post never carries it")
        return self


class KnowledgePacket(_PacketFields):
    """The canonical public wire shape (manyagent.web). Mirrors Packet's public
    fields; never carries the raw trace body."""


class Packet(_PacketFields):
    """A knowledge packet: ``raw`` | ``post`` | ``distill``."""

    @property
    def session_id(self) -> str:
        return self.id.split("/")[0]

    @property
    def agent(self) -> Agent | None:
        """The owning agent as a no-I/O reference (real id, ``online``,
        ``curator``, or None)."""
        if self.agent_id is None:
            return None
        return Agent(id=self.agent_id, session_id=self.session_id)

    def to_record(self) -> KnowledgePacket:
        """Project to the canonical public wire shape."""
        return KnowledgePacket(**self.model_dump())

    @classmethod
    async def fetch(cls, id: str, *, bank: Bank | None = None, force: bool = False) -> Packet:
        """Hydrate from the Bank (cache → API). Explicit; the Overview REPL
        elides the await. Bare ``Packet(...)`` does no I/O."""
        b = bank or get_bank()
        ck = (b.cache_key, id)
        if not force and ck in _PACKET_CACHE:
            return _PACKET_CACHE[ck]
        rec = await b.get_packet(id)
        if rec is None:
            raise LookupError(f"no packet {id!r} in the Bank")
        pkt = cls(**rec)
        _PACKET_CACHE[ck] = pkt
        return pkt
