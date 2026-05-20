"""oms.adapters — the agent adapter/plugin system (M5; oms.adapters.md).

An *adapter* sources a session's trace and injects context for one terminal
agent, producing **raw** material only — normalization/bounding/scrub is
``oms.capture``'s job (Design Principles §2). The contract is the ``Adapter``
ABC; discovery is ``registry.resolve()`` (local → builtin → hub, Settled
order). The builtins live under ``oms.adapters.builtin.*`` and are *not*
flattened onto the top-level surface (only ``Adapter`` is — the key noun).
"""

from __future__ import annotations

from oms.adapters.base import (
    Adapter,
    AdapterError,
    NotInstalled,
    PromptPrefixInjector,
    RawTrace,
    terminate_all_agents,
)
from oms.adapters.registry import available, resolve

__all__ = [
    "Adapter",
    "AdapterError",
    "NotInstalled",
    "PromptPrefixInjector",
    "RawTrace",
    "available",
    "resolve",
    "terminate_all_agents",
]
