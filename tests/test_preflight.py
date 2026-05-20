"""M8/M10 tests for oma.preflight — env / Bank-reachability /
migration-inventory + the M10 best-effort live ``schema_migrations`` diff
(oma.cli.md "preflight.py"). httpx is mocked with respx (the M2 pattern); the
live diff's privileged path is a gated ``integration`` test, while its
default (no admin key ⇒ skipped, no httpx) path is asserted offline.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from oma import preflight


def test_env_missing_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMA_BANK_URL", raising=False)
    assert preflight._check_env() is not None
    assert preflight.run_preflight() == 1


def test_env_missing_all_keys_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMA_BANK_URL", "http://127.0.0.1:54421")
    for k in preflight._BANK_KEYS:
        monkeypatch.delenv(k, raising=False)
    reason = preflight._check_env()
    assert reason is not None and "Bank key" in reason


@respx.mock
def test_bank_reachable_4xx_is_up() -> None:
    respx.get("http://127.0.0.1:54421/rest/v1/").mock(return_value=httpx.Response(401))
    assert preflight._check_bank_reachable("http://127.0.0.1:54421") is None


@respx.mock
def test_bank_5xx_is_down() -> None:
    respx.get("http://127.0.0.1:54421/rest/v1/").mock(return_value=httpx.Response(503))
    reason = preflight._check_bank_reachable("http://127.0.0.1:54421")
    assert reason is not None and "503" in reason


@respx.mock
def test_bank_connect_error_is_down() -> None:
    respx.get("http://down.example/rest/v1/").mock(side_effect=httpx.ConnectError("refused"))
    assert preflight._check_bank_reachable("http://down.example") is not None


def test_migrations_present() -> None:
    # The repo ships supabase/migrations/00001..00007 (oma.bank, M2).
    assert preflight._check_migrations() is None


@respx.mock
def test_run_preflight_green_when_env_and_bank_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMA_BANK_URL", "http://127.0.0.1:54421")
    monkeypatch.setenv("OMA_BANK_ANON_KEY", "anon-key")
    monkeypatch.delenv("OMA_BANK_ADMIN_KEY", raising=False)  # no privileged key → live diff skips
    respx.get("http://127.0.0.1:54421/rest/v1/").mock(return_value=httpx.Response(200))
    assert preflight.run_preflight() == 0  # offline path unchanged by the M10 live-diff


def test_live_schema_diff_skips_without_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path: no admin key ⇒ a 'skipped' string and **no** httpx
    call at all (so the preflight exit code is provably unchanged)."""
    monkeypatch.delenv("OMA_BANK_ADMIN_KEY", raising=False)
    out = preflight._live_schema_diff("http://127.0.0.1:54421")
    assert out.startswith("skipped (no OMA_BANK_ADMIN_KEY")


@pytest.mark.integration
def test_live_schema_diff_against_local_bank() -> None:
    """Privileged path (opt-in): against a real local Supabase
    (`make bank-up && make bank-migrate`, OMA_BANK_ADMIN_KEY set) the live
    diff returns a status string and never flips the preflight exit code."""
    import os

    out = preflight._live_schema_diff(os.environ["OMA_BANK_URL"])
    assert isinstance(out, str) and out
    assert preflight.run_preflight() == 0
