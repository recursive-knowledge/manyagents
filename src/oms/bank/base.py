"""The Bank interface — the async seam every other module reads/writes through.

Async because ``oms.core.Packet.fetch()`` is async (oms.core.md) and ``oms.web``
is FastAPI. The Bank trades plain ``dict`` records (the PostgREST row shape);
``oms.core`` (M3) layers the frozen Pydantic ``Packet`` on top — so the Bank
stays independent of the model layer (correct dependency direction).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Identity selects the access key (oms.bank.md 4-identity model).
Identity = str  # "public" | "trusted" | "admin" | "curator"


@runtime_checkable
class Bank(Protocol):
    """Async knowledge-Bank surface. Implemented by FakeBank and SupabaseBank."""

    # Stable per-Bank identity so oms.core's hydration cache never confuses two
    # Banks (different identities / dev vs. test). SupabaseBank: identity@url;
    # FakeBank: a per-instance uuid (test isolation).
    cache_key: str

    # --- sessions ---
    async def put_session(self, id: str, *, goal: str | None = None, status: str = "active") -> None: ...
    async def get_session(self, id: str) -> dict[str, Any] | None: ...
    async def list_sessions(self) -> list[dict[str, Any]]: ...

    # --- agents ---
    async def next_agent_seq(self, session_id: str) -> int: ...
    async def put_agent(self, id: str, *, session_id: str, adapter: str, seq: int) -> None: ...
    async def get_agent(self, id: str) -> dict[str, Any] | None: ...
    async def list_agents(self, session_id: str) -> list[dict[str, Any]]: ...

    # --- packets (idempotent upsert by record["id"]) ---
    async def put_packet(self, record: dict[str, Any]) -> str: ...
    async def get_packet(self, id: str) -> dict[str, Any] | None: ...
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
    ) -> list[dict[str, Any]]: ...

    # --- traces (raw scrubbed; never public) ---
    async def put_trace(
        self, packet_id: str, body: str, *, scrub_version: str | None = None, complete: bool = True
    ) -> None: ...
    async def get_trace(self, packet_id: str) -> dict[str, Any] | None: ...

    # --- injection ledger + reuse signal ---
    async def record_injection(self, packet_id: str, target_session_id: str) -> None: ...
    async def list_injections(
        self, *, packet_id: str | None = None, target_session_id: str | None = None
    ) -> list[dict[str, Any]]: ...
    async def reuse_score(self, packet_id: str | None = None) -> list[dict[str, Any]]: ...

    # --- quarantine (append-only; visible, excluded from curation/inject) ---
    async def quarantine(self, packet_id: str, reason: str, *, auditor_version: str | None = None) -> None: ...
