"""M8/M10 tests for manyagent.preflight — env / Bank-reachability /
migration-inventory + the M10 best-effort live ``schema_migrations`` diff
(manyagent.cli.md "preflight.py"). httpx is mocked with respx (the M2 pattern); the
live diff's privileged path is a gated ``integration`` test, while its
default (no admin key ⇒ skipped, no httpx) path is asserted offline.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from manyagent import preflight


def test_env_missing_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANYAGENT_BANK_URL", raising=False)
    assert preflight._check_env() is not None
    assert preflight.run_preflight() == 1


def test_env_missing_all_keys_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_BANK_URL", "http://127.0.0.1:54421")
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
    # The repo ships supabase/migrations/00001..00007 (manyagent.bank, M2).
    assert preflight._check_migrations() is None


def test_migrations_skipped_off_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Installed wheel (`uv tool install manyagent`): no repo checkout, no
    ``supabase/`` tree — the inventory check skips instead of failing (it used
    to hard-fail with 'migrations dir missing: <venv>/…', making the error
    hint's `python -m manyagent.preflight` advice a dead end off-repo)."""
    monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)  # no pyproject.toml here
    monkeypatch.setattr(preflight, "_MIGRATIONS_DIR", tmp_path / "supabase" / "migrations")
    assert preflight._check_migrations() is None
    # A source checkout (pyproject.toml present) with no migrations still fails.
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    reason = preflight._check_migrations()
    assert reason is not None and "migrations dir missing" in reason


@respx.mock
def test_run_preflight_green_off_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MANYAGENT_BANK_URL", "http://127.0.0.1:54421")
    monkeypatch.setenv("MANYAGENT_BANK_ANON_KEY", "anon-key")
    monkeypatch.delenv("MANYAGENT_BANK_ADMIN_KEY", raising=False)
    monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(preflight, "_MIGRATIONS_DIR", tmp_path / "supabase" / "migrations")
    respx.get("http://127.0.0.1:54421/rest/v1/").mock(return_value=httpx.Response(200))
    assert preflight.run_preflight() == 0


@respx.mock
def test_run_preflight_green_when_env_and_bank_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_BANK_URL", "http://127.0.0.1:54421")
    monkeypatch.setenv("MANYAGENT_BANK_ANON_KEY", "anon-key")
    monkeypatch.delenv("MANYAGENT_BANK_ADMIN_KEY", raising=False)  # no privileged key → live diff skips
    respx.get("http://127.0.0.1:54421/rest/v1/").mock(return_value=httpx.Response(200))
    assert preflight.run_preflight() == 0  # offline path unchanged by the M10 live-diff


def test_live_schema_diff_skips_without_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path: no admin key ⇒ a 'skipped' string and **no** httpx
    call at all (so the preflight exit code is provably unchanged)."""
    monkeypatch.delenv("MANYAGENT_BANK_ADMIN_KEY", raising=False)
    out = preflight._live_schema_diff("http://127.0.0.1:54421")
    assert out.startswith("skipped (no MANYAGENT_BANK_ADMIN_KEY")


def test_live_schema_diff_skips_off_repo_even_with_admin_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Off-repo there is no on-disk inventory to diff against — with an admin
    key set, every applied migration would otherwise read as spurious DRIFT
    on a healthy Bank. Skipped before any httpx call (none is mocked here)."""
    monkeypatch.setenv("MANYAGENT_BANK_ADMIN_KEY", "admin-key")
    monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(preflight, "_MIGRATIONS_DIR", tmp_path / "supabase" / "migrations")
    out = preflight._live_schema_diff("http://127.0.0.1:54421")
    assert out.startswith("skipped (installed package")


@pytest.mark.integration
def test_live_schema_diff_against_local_bank() -> None:
    """Privileged path (opt-in): against a real local Supabase
    (`make bank-up && make bank-migrate`, MANYAGENT_BANK_ADMIN_KEY set) the live
    diff returns a status string and never flips the preflight exit code."""
    import os

    out = preflight._live_schema_diff(os.environ["MANYAGENT_BANK_URL"])
    assert isinstance(out, str) and out
    assert preflight.run_preflight() == 0
