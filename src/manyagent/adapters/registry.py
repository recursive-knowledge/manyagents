"""Adapter discovery (manyagent.adapters.md "Registry resolution order — Settled").

Resolution order: a local install ``~/.manyagent/adapters/<name>/`` → the first-party
builtin → the plugin hub. ``resolve()`` is **non-interactive** and returns the
adapter *class* (stateless code; the caller binds a session). The hub serves
only adapters merged via maintainer-reviewed PR — there is no arbitrary
plugin ecosystem (Plugin trust — Settled). The Overview's ``[y/n]`` download
confirmation is the **CLI** seam (M8), not part of ``resolve()``.

``_hub_fetch`` is the single network seam: offline it returns ``None`` (so
``resolve`` raises a clear error); tests monkeypatch it.

Local-adapter trust boundary
----------------------------
Files under ``MANYAGENT_ADAPTERS_DIR`` are ``exec``'d as Python.  To prevent
privilege escalation via a world-writable or symlink-planted ``__init__.py``,
``_load_local`` rejects the adapter if:

* the adapters dir itself is world-writable (``stat().st_mode & 0o002``), OR
* the ``__init__.py`` or any of its parents up to (but not including) the
  adapters root is a symlink.
* the ``__init__.py`` or its immediate containing dir is world-writable.

The world-writable mode-bit checks are POSIX-only: Windows has no POSIX
permission model (it uses ACLs) and ``os.stat().st_mode`` there reports a fixed
synthetic value with the world-write bit always set, so the check would reject
every adapter.  The world-writable threat model is a multi-user-POSIX concern,
so skipping it on Windows is correct, not a regression.  The symlink rejection
stays cross-platform.

Only plain, owner-only directories and files are trusted.
"""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

from manyagent.adapters.base import Adapter, AdapterError
from manyagent.adapters.builtin.claude import ClaudeAdapter
from manyagent.adapters.builtin.codex import CodexAdapter
from manyagent.adapters.builtin.gemini import GeminiAdapter
from manyagent.adapters.builtin.qwen import QwenAdapter
from manyagent.utils import config

_BUILTINS: dict[str, type[Adapter]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "gemini": GeminiAdapter,
    "qwen": QwenAdapter,
}


def _local_root() -> Path:
    root = Path(config.resolve("MANYAGENT_ADAPTERS_DIR", str(Path.home() / ".manyagent" / "adapters"))).expanduser()
    # World-writable check is POSIX-only (see module docstring): on Windows
    # st_mode always has the world-write bit set, so this would reject all dirs.
    if os.name == "posix" and root.exists() and (root.stat().st_mode & stat.S_IWOTH):
        raise AdapterError(
            f"MANYAGENT_ADAPTERS_DIR {root} is world-writable — refusing to load local adapters "
            "(remove world-write permission: chmod o-w <dir>)"
        )
    return root


def _check_local_adapter_safety(init: Path, root: Path) -> None:
    """Raise ``AdapterError`` if ``init`` or any parent up to ``root`` is a
    symlink, or if ``init`` / its containing dir is world-writable.

    This guards the ``exec_module`` call against two common privilege-escalation
    vectors: symlink plants (an attacker creates a symlink from a trusted path
    to a hostile file) and world-writable dirs (any local user can drop a
    malicious ``__init__.py`` into an adapter directory)."""
    # Walk from init up to (but not including) root, checking for symlinks.
    # Symlink rejection is cross-platform (valid on Windows too).
    candidate = init
    while candidate != root:
        if candidate.is_symlink():
            raise AdapterError(
                f"local adapter path contains a symlink ({candidate}) — "
                "refusing to exec (symlink plants are a code-execution vector)"
            )
        candidate = candidate.parent

    # Reject world-writable init file or its immediate parent directory.
    # POSIX-only (see module docstring): Windows st_mode always sets the
    # world-write bit, so this check is meaningless and skipping it is correct.
    if os.name == "posix":
        for path in (init, init.parent):
            if path.exists() and (path.stat().st_mode & stat.S_IWOTH):
                raise AdapterError(
                    f"local adapter path {path} is world-writable — "
                    "refusing to exec (world-writable paths are a code-execution vector)"
                )


def _load_local(name: str) -> type[Adapter] | None:
    """A local adapter is ``<root>/<name>/__init__.py`` exporting a top-level
    ``ADAPTER: type[Adapter]`` (the documented contract — kept deliberately
    un-clever for review).

    Trust boundary: the adapter dir and init file must be free of symlinks and
    world-write permission.  See module docstring for the full policy."""
    root = _local_root()
    init = root / name / "__init__.py"
    if not init.is_file():
        return None
    _check_local_adapter_safety(init, root)
    spec = importlib.util.spec_from_file_location(f"manyagent._local_adapter_{name}", init)
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
