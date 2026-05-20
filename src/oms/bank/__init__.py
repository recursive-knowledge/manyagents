"""oms.bank — the Knowledge Bank: Supabase client, 4-identity access, the
injection reuse ledger. Every module reads/writes through it (M2).

The schema source of truth is ``supabase/migrations/00001..00007`` (append-only;
prose is a rendering — Design Principles §3).
"""

from __future__ import annotations

from oms.bank.base import Bank, Identity
from oms.bank.fake import FakeBank, make_cursor
from oms.bank.retry import with_backoff

# Per-identity singletons: a stable instance (hence stable ``cache_key``) so
# oms.core's hydration cache stays coherent across get_bank() calls.
_BANKS: dict[str, Bank] = {}


def get_bank(identity: Identity = "trusted") -> Bank:
    """Return the real Supabase-backed Bank for ``identity`` (lazy client).

    Memoized per identity so the returned instance — and its ``cache_key`` —
    is stable. Tests use :class:`FakeBank` directly (offline); the gated
    integration suite exercises this against a local Supabase.
    """
    cached = _BANKS.get(identity)
    if cached is not None:
        return cached
    from oms.bank.supabase_bank import SupabaseBank

    bank = SupabaseBank(identity)
    _BANKS[identity] = bank
    return bank


__all__ = [
    "Bank",
    "FakeBank",
    "Identity",
    "get_bank",
    "make_cursor",
    "with_backoff",
]
