"""Shared test fixtures + marker gating.

Default suite is offline and ~full coverage. The ``integration`` (local Bank)
and ``online`` (installed CLIs / live LLM) suites are opt-in via env, so
``make test`` stays green without external services. The in-memory fake-Bank
fixture and respx HTTP mock are added by M2 (oma.bank).
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import pytest

from oma.bank import FakeBank


@pytest.fixture
def fake_bank() -> FakeBank:
    """A fresh in-memory Bank (offline). The gated integration suite uses the
    real Supabase-backed Bank instead."""
    return FakeBank()


_GATES = {
    "integration": "OMA_RUN_INTEGRATION",
    "online": "OMA_RUN_ONLINE",
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
