"""The hosted-curator stub (oms.distill.md "Hybrid curator" — server mode).

A `curator` identity in oms.bank's role model periodically re-distills the
**public corpus** per goal. C4 corollary (Design Principles §6/§11): curating
the public corpus is corpus-curation, not being the user's *task* inference
provider — OMA already hosts the Bank/API, so a hosted curator is consistent,
not a violation; the structure is a curator tax, never a human tax.

This is a **stub**: the hosted endpoint is Open-Q infra, not closed. The
contract is fixed (POST the cache-split prompt, receive the raw JSON bundle
string); the implementation here only knows how to *fail cleanly* so ``auto``
can fall back to ``local``. ``httpx`` is imported lazily — it is already an
oms.bank dependency, but the import stays out of the offline hot path.
"""

from __future__ import annotations


class ServerUnavailable(RuntimeError):
    """The hosted curator is unreachable. ``auto`` mode catches this and falls
    back to the local curator (oms.distill.md: 'usable degraded')."""


class ServerCurator:
    """POSTs the cache-split prompt to a hosted curator. Stub: an empty URL is
    treated as 'no server' and a network failure as ``ServerUnavailable`` so
    the corpus stays usable degraded (local-only)."""

    mode = "server"

    def __init__(self, url: str) -> None:
        self._url = url.strip().rstrip("/")

    async def complete(self, system: str, user: str) -> str:
        if not self._url:
            raise ServerUnavailable("OMS_CURATOR_SERVER_URL is unset; no hosted curator")
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._url}/curate",
                    json={"system": system, "user": user},
                )
            resp.raise_for_status()
            data = resp.json()
        except ServerUnavailable:
            raise
        except Exception as exc:  # connect/timeout/HTTP/JSON → fall back
            raise ServerUnavailable(f"hosted curator unreachable: {exc!r}") from exc
        raw = data.get("raw") if isinstance(data, dict) else None
        if not isinstance(raw, str):
            raise ServerUnavailable("hosted curator returned no 'raw' bundle string")
        return raw
