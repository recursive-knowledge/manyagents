"""Adapter discovery (oma.adapters.md "Registry resolution order — Settled").

Resolution order: a local install ``~/.oma/adapters/<name>/`` → the first-party
builtin → the plugin hub. ``resolve()`` is **non-interactive** and returns the
adapter *class* (stateless code; the caller binds a session). The hub serves
only adapters merged via maintainer-reviewed PR — there is no arbitrary
plugin ecosystem (Plugin trust — Settled). The Overview's ``[y/n]`` download
confirmation is the **CLI** seam (M8), not part of ``resolve()``.

``_hub_fetch`` is the single network seam: offline it returns ``None`` (so
``resolve`` raises a clear error); tests monkeypatch it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from oma.adapters.base import Adapter, AdapterError
from oma.adapters.builtin.claude import ClaudeAdapter
from oma.adapters.builtin.codex import CodexAdapter
from oma.adapters.builtin.gemini import GeminiAdapter
from oma.adapters.builtin.qwen import QwenAdapter
from oma.utils import config

_BUILTINS: dict[str, type[Adapter]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "gemini": GeminiAdapter,
    "qwen": QwenAdapter,
}


def _local_root() -> Path:
    return Path(config.resolve("OMA_ADAPTERS_DIR", str(Path.home() / ".oma" / "adapters"))).expanduser()


def _load_local(name: str) -> type[Adapter] | None:
    """A local adapter is ``<root>/<name>/__init__.py`` exporting a top-level
    ``ADAPTER: type[Adapter]`` (the documented contract — kept deliberately
    un-clever for review)."""
    init = _local_root() / name / "__init__.py"
    if not init.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"oma._local_adapter_{name}", init)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    adapter = getattr(mod, "ADAPTER", None)
    if not (isinstance(adapter, type) and issubclass(adapter, Adapter)):
        raise AdapterError(f"local adapter {name!r} must export ADAPTER: type[Adapter]")
    return adapter


def _hub_fetch(name: str) -> Path | None:
    """Network seam: download a maintainer-reviewed plugin into the local root
    and return its directory. Offline default: ``None`` (tests override)."""
    return None


def resolve(name: str) -> type[Adapter]:
    """Return the adapter class for ``name`` (local → builtin → hub)."""
    local = _load_local(name)
    if local is not None:
        return local
    if name in _BUILTINS:
        return _BUILTINS[name]
    if _hub_fetch(name) is not None:
        fetched = _load_local(name)  # hub installs into the local root
        if fetched is not None:
            return fetched
    raise AdapterError(f"no adapter {name!r}: not a local install, a builtin, or on the hub")


def available() -> list[str]:
    """Builtin adapter names (registry listing / observability)."""
    return sorted(_BUILTINS)
