"""In-memory Bank for offline tests + the conftest ``fake_bank`` fixture.

Models round-trip, idempotent upsert, atomic contiguous ``next_agent_seq``, the
injection ledger, and ``reuse_score``. It does NOT enforce RLS — that is
DB-enforced and covered by the gated integration suite (manyagent.bank.md
Verification splits unit vs. integration/security).

``_session_outcome`` / ``reuse_score`` mirror the SQL ``reuse_score`` view in
``supabase/migrations/00007_injection_ledger.sql`` exactly; keep them in sync.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

_last_now = ""


def _now() -> str:
    """A strictly-increasing ISO timestamp. Wall-clock, but bumped by 1µs when
    the clock hasn't advanced since the last call — Windows' ~15ms clock
    resolution otherwise gives several inserts the *same* ``created_at``, and
    tests that rely on insertion order being reflected in timestamps (e.g. the
    cross-distill nudge's strict newer-than-bundle count) flake there."""
    global _last_now
    t = datetime.datetime.now(tz=datetime.UTC).isoformat()
    if t <= _last_now:
        t = (datetime.datetime.fromisoformat(_last_now) + datetime.timedelta(microseconds=1)).isoformat()
    _last_now = t
    return t


class FakeBank:
    """A dict-backed async :class:`~manyagent.bank.base.Bank`."""

    def __init__(self) -> None:
        # Per-instance identity so manyagent.core's hydration cache never confuses
        # two FakeBanks (test isolation; see Bank.cache_key).
        self.cache_key = f"fake-{uuid.uuid4()}"
        self._sessions: dict[str, dict[str, Any]] = {}
        self._agents: dict[str, dict[str, Any]] = {}
        self._packets: dict[str, dict[str, Any]] = {}
        self._traces: dict[str, dict[str, Any]] = {}
        self._renditions: dict[tuple[str, str], dict[str, Any]] = {}
        self._injections: dict[tuple[str, str], dict[str, Any]] = {}
        self._seq: dict[str, int] = {}
        self._seq_lock = asyncio.Lock()

    # --- sessions ---
    async def put_session(self, id: str, *, goal: str | None = None, status: str = "active") -> None:
        existing = self._sessions.get(id, {})
        self._sessions[id] = {
            "id": id,
            "goal": goal if goal is not None else existing.get("goal"),
            "status": status,
            "created_at": existing.get("created_at", _now()),
        }

    async def get_session(self, id: str) -> dict[str, Any] | None:
        s = self._sessions.get(id)
        return dict(s) if s else None

    async def list_sessions(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self._sessions.values()]

    # --- agents ---
    async def next_agent_seq(self, session_id: str) -> int:
        async with self._seq_lock:
            nxt = self._seq.get(session_id, 0) + 1
            self._seq[session_id] = nxt
            return nxt

    async def put_agent(
        self, id: str, *, session_id: str, adapter: str, seq: int, principal_id: str | None = None
    ) -> None:
        self._agents[id] = {
            "id": id,
            "session_id": session_id,
            "adapter": adapter,
            "seq": seq,
            "principal_id": principal_id,
            "created_at": self._agents.get(id, {}).get("created_at", _now()),
        }

    async def get_agent(self, id: str) -> dict[str, Any] | None:
        a = self._agents.get(id)
        return dict(a) if a else None

    async def list_agents(self, session_id: str) -> list[dict[str, Any]]:
        return sorted(
            (dict(a) for a in self._agents.values() if a["session_id"] == session_id),
            key=lambda a: a["seq"],
        )

    async def list_agents_by_principal(self, principal_id: str) -> list[dict[str, Any]]:
        return sorted(
            (dict(a) for a in self._agents.values() if a.get("principal_id") == principal_id),
            key=lambda a: (a["session_id"], a["seq"]),
        )

    # --- packets ---
    async def put_packet(self, record: dict[str, Any]) -> str:
        pid = record["id"]
        prev = self._packets.get(pid, {})
        row = dict(record)
        row.setdefault("created_at", prev.get("created_at", _now()))
        row.setdefault("quarantined", prev.get("quarantined", False))
        row.setdefault("parents", prev.get("parents", []))
        self._packets[pid] = {**prev, **row}
        return str(pid)

    async def get_packet(self, id: str) -> dict[str, Any] | None:
        p = self._packets.get(id)
        return dict(p) if p else None

    async def list_packets(
        self,
        *,
        session_id: str | None = None,
        type: str | None = None,
        goal: str | None = None,
        since: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        include_quarantined: bool = True,
    ) -> list[dict[str, Any]]:
        rows = [dict(p) for p in self._packets.values()]
        if session_id is not None:
            rows = [r for r in rows if r.get("session_id") == session_id]
        if type is not None:
            rows = [r for r in rows if r.get("type") == type]
        if goal is not None:
            rows = [r for r in rows if r.get("goal") == goal]
        if not include_quarantined:
            rows = [r for r in rows if not r.get("quarantined", False)]
        if since is not None:
            rows = [r for r in rows if str(r.get("created_at", "")) >= since]
        rows.sort(key=lambda r: (str(r.get("created_at", "")), r["id"]))
        if cursor is not None:
            rows = [r for r in rows if (str(r.get("created_at", "")), r["id"]) > _split_cursor(cursor)]
        if limit is not None:
            rows = rows[:limit]
        return rows

    # --- traces ---
    async def put_trace(
        self, packet_id: str, body: str, *, scrub_version: str | None = None, complete: bool = True
    ) -> None:
        prev = self._traces.get(packet_id, {})
        self._traces[packet_id] = {
            "packet_id": packet_id,
            "body": body,
            "scrub_version": scrub_version,
            "complete": complete,
            "created_at": prev.get("created_at", _now()),
        }

    async def get_trace(self, packet_id: str) -> dict[str, Any] | None:
        t = self._traces.get(packet_id)
        return dict(t) if t else None

    # --- trace renditions ---
    async def put_rendition(
        self, packet_id: str, fmt: str, body: str, *, miner_version: str | None = None, complete: bool = True
    ) -> None:
        prev = self._renditions.get((packet_id, fmt), {})
        self._renditions[(packet_id, fmt)] = {
            "packet_id": packet_id,
            "format": fmt,
            "body": body,
            "miner_version": miner_version,
            "complete": complete,
            "created_at": prev.get("created_at", _now()),
        }

    async def get_rendition(self, packet_id: str, fmt: str) -> dict[str, Any] | None:
        r = self._renditions.get((packet_id, fmt))
        return dict(r) if r else None

    # --- injection ledger ---
    async def record_injection(self, packet_id: str, target_session_id: str) -> None:
        key = (packet_id, target_session_id)
        if key not in self._injections:  # idempotent on the composite PK
            self._injections[key] = {
                "packet_id": packet_id,
                "target_session_id": target_session_id,
                "injected_at": _now(),
            }

    async def list_injections(
        self, *, packet_id: str | None = None, target_session_id: str | None = None
    ) -> list[dict[str, Any]]:
        out = []
        for (pid, sid), row in self._injections.items():
            if packet_id is not None and pid != packet_id:
                continue
            if target_session_id is not None and sid != target_session_id:
                continue
            out.append(dict(row))
        return out

    def _session_outcome(self, session_id: str) -> float:
        """Mirrors target_outcome in the 00007 reuse_score view."""
        ratings = [
            p["rating"]
            for p in self._packets.values()
            if p.get("session_id") == session_id and p.get("type") == "post" and p.get("rating") is not None
        ]
        best_rating = max(ratings) if ratings else 0
        accept_bonus = (
            4
            if any(
                p.get("session_id") == session_id and p.get("type") == "distill" and p.get("preference") == "accept"
                for p in self._packets.values()
            )
            else 0
        )
        return float(max(best_rating, accept_bonus))

    async def reuse_score(self, packet_id: str | None = None) -> list[dict[str, Any]]:
        ids = [packet_id] if packet_id is not None else list(self._packets)
        out = []
        for pid in ids:
            if pid not in self._packets:
                continue
            sessions = sorted({sid for (p, sid) in self._injections if p == pid})
            out.append({
                "packet_id": pid,
                "inject_count": len(sessions),
                "reuse_score": sum(self._session_outcome(sid) for sid in sessions),
            })
        return out

    # --- quarantine ---
    async def quarantine(self, packet_id: str, reason: str, *, auditor_version: str | None = None) -> None:
        if packet_id in self._packets:
            self._packets[packet_id]["quarantined"] = True
            self._packets[packet_id]["quarantine_reason"] = reason
            self._packets[packet_id]["auditor_version"] = auditor_version


def _split_cursor(cursor: str) -> tuple[str, str]:
    created_at, _, pid = cursor.partition("|")
    return (created_at, pid)


def make_cursor(row: dict[str, Any]) -> str:
    """Opaque pagination cursor: ``created_at|id`` (manyagent.web M9 reuses this)."""
    return f"{row.get('created_at', '')}|{row['id']}"
