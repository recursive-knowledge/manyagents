"""Shared test fixtures + marker gating.

Default suite is offline and ~full coverage. The ``integration`` (local Bank)
and ``online`` (installed CLIs / live LLM) suites are opt-in via env, so
``make test`` stays green without external services. The in-memory fake-Bank
fixture and respx HTTP mock are added by M2 (oms.bank); the simulated-
conversation harness (``sim`` / ``trial_bank``) wraps ``oms.testing``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest

from oms.bank import FakeBank
from oms.testing import Simulation, seed_trial_story


@pytest.fixture
def fake_bank() -> FakeBank:
    """A fresh in-memory Bank (offline). The gated integration suite uses the
    real Supabase-backed Bank instead."""
    return FakeBank()


@pytest.fixture
def sim(tmp_path: Path) -> Iterator[Simulation]:
    """A conversation simulator over a fresh FakeBank: scripted model/agent/IO
    doubles wired into the REAL lifecycle verbs and knowledge-loop handlers
    (see ``oms.testing``). OMS_HOME is sandboxed to tmp_path."""
    with Simulation(home=tmp_path / ".oms") as s:
        yield s


@pytest.fixture
async def trial_bank() -> FakeBank:
    """A FakeBank pre-seeded with the trial story (a real captured session:
    raw trace + ★2 reflection + cross-goal distill bundle) for read-side
    tests that start from existing knowledge."""
    bank = FakeBank()
    await seed_trial_story(bank)
    return bank


@pytest.fixture(autouse=True)
def adapter_gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """The agent-mint gate (``oms._handlers._validate_adapter``) requires the
    adapter's real CLI on PATH — never true offline/CI, where most tests mint
    ``claude`` agents freely. Default it to a no-op; gate tests request this
    fixture by name to get the REAL function back."""
    import oms._handlers as h

    real = h._validate_adapter
    monkeypatch.setattr(h, "_validate_adapter", lambda name: None)
    yield real


@pytest.fixture(autouse=True)
def _plain_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI output under test is plain text at a fixed width regardless of how
    pytest is invoked (`pytest -s` on a TTY would otherwise let ANSI escapes —
    and the real terminal width, via rich honoring COLUMNS/ioctl — leak into
    exact-text assertions). Individual tests override OMS_COLOR to exercise
    styling."""
    monkeypatch.setenv("OMS_COLOR", "never")
    monkeypatch.setenv("COLUMNS", "80")


_GATES = {
    "integration": "OMS_RUN_INTEGRATION",
    "online": "OMS_RUN_ONLINE",
}


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: Iterable[pytest.Item],
) -> None:
    """Skip opt-in markers unless their env gate is set to a truthy value."""
    for item in items:
        for marker, env_var in _GATES.items():
            if marker in item.keywords and os.environ.get(env_var, "0") in ("0", ""):
                item.add_marker(
                    pytest.mark.skip(reason=f"{marker} suite is opt-in: set {env_var}=1"),
                )
