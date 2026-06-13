"""The real Bank: self-hosted Supabase over the async PostgREST client.

The four identities (public/trusted/admin/curator) are selected by which key
the client is built with — RLS is DB-enforced, so this layer just picks the
key (manyagent.bank.md "Access identities"). Network calls are retry-wrapped.
"""

from __future__ import annotations

from typing import Any

from manyagent.bank.retry import NonRetryableError, with_backoff
from manyagent.utils import config

_IDENTITY_KEY_VARS: dict[str, str] = {
    "public": "MANYAGENT_BANK_ANON_KEY",
    "trusted": "MANYAGENT_BANK_TRUSTED_KEY",
    "admin": "MANYAGENT_BANK_ADMIN_KEY",
    "curator": "MANYAGENT_BANK_CURATOR_KEY",
}


class BankConfigError(RuntimeError, NonRetryableError):
    """The Bank is misconfigured (no key for the identity): fail fast — the
    retry shim must not back off in front of an error a retry cannot fix."""


def _cf_access_headers() -> dict[str, str]:
    cid = config.resolve("MANYAGENT_BANK_CF_ACCESS_CLIENT_ID", "")
    secret = config.resolve("MANYAGENT_BANK_CF_ACCESS_CLIENT_SECRET", "")
    if cid and secret:
        return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret}
    return {}


class SupabaseBank:
    """Async :class:`~manyagent.bank.base.Bank` over Supabase PostgREST."""

    def __init__(self, identity: str = "trusted") -> None:
        if identity not in _IDENTITY_KEY_VARS:
            raise ValueError(f"unknown Bank identity {identity!r}")
        self.identity = identity
        self._url = config.resolve("MANYAGENT_BANK_URL", config.MANYAGENT_BANK_URL)
        # identity@url so manyagent.core's hydration cache never confuses two Banks
        # (different identity, or dev vs. test URL; see Bank.cache_key).
        self.cache_key = f"{identity}@{self._url}"
        self._key = config.resolve(_IDENTITY_KEY_VARS[identity], "")
        self._cli: Any = None

    async def _client(self) -> Any:
        if self._cli is None:
            from supabase import AsyncClientOptions, acreate_client

            if not self._key:
                raise BankConfigError(
                    f"Bank identity {self.identity!r} has no key ({_IDENTITY_KEY_VARS[self.identity]} unset)"
                )
            headers = _cf_access_headers()
            opts = AsyncClientOptions(headers=headers) if headers else None
            self._cli = await acreate_client(self._url, self._key, options=opts)
        return self._cli

    # --- sessions ---
    @with_backoff()
    async def put_session(self, id: str, *, goal: str | None = None, status: str = "active") -> None:
        cli = await self._client()
        await cli.table("sessions").upsert({"id": id, "goal": goal, "status": status}).execute()

    @with_backoff()
    async def get_session(self, id: str) -> dict[str, Any] | None:
        cli = await self._client()
        resp = await cli.table("sessions").select("*").eq("id", id).execute()
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    @with_backoff()
    async def list_sessions(self) -> list[dict[str, Any]]:
        cli = await self._client()
        resp = await cli.table("sessions").select("*").order("created_at").execute()
        return [dict(r) for r in (resp.data or [])]

    # --- agents ---
    @with_backoff()
    async def next_agent_seq(self, session_id: str) -> int:
        cli = await self._client()
        resp = await cli.rpc("next_agent_seq", {"p_session_id": session_id}).execute()
        return int(resp.data)

    @with_backoff()
    async def put_agent(self, id: str, *, session_id: str, adapter: str, seq: int) -> None:
        cli = await self._client()
        await cli.table("agents").upsert({"id": id, "session_id": session_id, "adapter": adapter, "seq": seq}).execute()

    @with_backoff()
    async def get_agent(self, id: str) -> dict[str, Any] | None:
        cli = await self._client()
        resp = await cli.table("agents").select("*").eq("id", id).execute()
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    @with_backoff()
    async def list_agents(self, session_id: str) -> list[dict[str, Any]]:
        cli = await self._client()
        resp = await cli.table("agents").select("*").eq("session_id", session_id).order("seq").execute()
        return [dict(r) for r in (resp.data or [])]

    # --- packets ---
    @with_backoff()
    async def put_packet(self, record: dict[str, Any]) -> str:
        cli = await self._client()
        await cli.table("packets").upsert(record).execute()
        return str(record["id"])

    @with_backoff()
    async def get_packet(self, id: str) -> dict[str, Any] | None:
        cli = await self._client()
        resp = await cli.table("packets").select("*").eq("id", id).execute()
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    @with_backoff()
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
        cli = await self._client()
        q = cli.table("packets").select("*")
        if session_id is not None:
            q = q.eq("session_id", session_id)
        if type is not None:
            q = q.eq("type", type)
        if goal is not None:
            q = q.eq("goal", goal)
        if not include_quarantined:
            q = q.eq("quarantined", False)
        if since is not None:
            q = q.gte("created_at", since)
        if cursor is not None:
            q = q.gt("created_at", cursor.partition("|")[0])
        q = q.order("created_at").order("id")
        if limit is not None:
            q = q.limit(limit)
        resp = await q.execute()
        return [dict(r) for r in (resp.data or [])]

    # --- traces ---
    @with_backoff()
    async def put_trace(
        self, packet_id: str, body: str, *, scrub_version: str | None = None, complete: bool = True
    ) -> None:
        cli = await self._client()
        await (
            cli.table("traces")
            .upsert({"packet_id": packet_id, "body": body, "scrub_version": scrub_version, "complete": complete})
            .execute()
        )

    @with_backoff()
    async def get_trace(self, packet_id: str) -> dict[str, Any] | None:
        cli = await self._client()
        resp = await cli.table("traces").select("*").eq("packet_id", packet_id).execute()
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    # --- trace renditions ---
    @with_backoff()
    async def put_rendition(
        self, packet_id: str, fmt: str, body: str, *, miner_version: str | None = None, complete: bool = True
    ) -> None:
        cli = await self._client()
        await (
            cli.table("trace_renditions")
            .upsert(
                {
                    "packet_id": packet_id,
                    "format": fmt,
                    "body": body,
                    "miner_version": miner_version,
                    "complete": complete,
                },
                on_conflict="packet_id,format",
            )
            .execute()
        )

    @with_backoff()
    async def get_rendition(self, packet_id: str, fmt: str) -> dict[str, Any] | None:
        cli = await self._client()
        resp = await cli.table("trace_renditions").select("*").eq("packet_id", packet_id).eq("format", fmt).execute()
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    # --- injection ledger ---
    @with_backoff()
    async def record_injection(self, packet_id: str, target_session_id: str) -> None:
        cli = await self._client()
        await (
            cli.table("injections")
            .upsert(
                {"packet_id": packet_id, "target_session_id": target_session_id},
                on_conflict="packet_id,target_session_id",
            )
            .execute()
        )

    @with_backoff()
    async def list_injections(
        self, *, packet_id: str | None = None, target_session_id: str | None = None
    ) -> list[dict[str, Any]]:
        cli = await self._client()
        q = cli.table("injections").select("*")
        if packet_id is not None:
            q = q.eq("packet_id", packet_id)
        if target_session_id is not None:
            q = q.eq("target_session_id", target_session_id)
        resp = await q.execute()
        return [dict(r) for r in (resp.data or [])]

    @with_backoff()
    async def reuse_score(self, packet_id: str | None = None) -> list[dict[str, Any]]:
        cli = await self._client()
        q = cli.table("reuse_score").select("*")
        if packet_id is not None:
            q = q.eq("packet_id", packet_id)
        resp = await q.execute()
        return [dict(r) for r in (resp.data or [])]

    # --- quarantine ---
    @with_backoff()
    async def quarantine(self, packet_id: str, reason: str, *, auditor_version: str | None = None) -> None:
        cli = await self._client()
        await (
            cli.table("packets")
            .update({"quarantined": True, "quarantine_reason": reason, "auditor_version": auditor_version})
            .eq("id", packet_id)
            .execute()
        )
