"""Oh My Swarm — wrap installed coding-agent CLIs; curate cross-session knowledge.

Distribution name: ``oh-my-swarm``. Import name: ``oms``. Console script: ``oms``.
Identity is fixed here and in ``pyproject.toml`` and is never re-derived as a
string elsewhere (datasmith identity rule; Package Structure & Workflow).
"""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING

import dotenv

__version__ = "0.1.0"


def setup_environment() -> None:
    """Load environment variables from ``~/.oms/env`` **and** ``./oms.env``.

    Two layers, lowest precedence first so the cwd file wins on overlap:

    1. ``~/.oms/env`` — the user-level fallback (M11). Loaded so an MCP server
       launched by Claude Code / Codex / Gemini *outside* a project directory
       still finds the Bank credentials installed once by ``oms start``.
    2. ``./oms.env`` — the project-scoped overrides (M0).

    ``dotenv.load_dotenv`` does not overwrite already-set process vars, so the
    real env always wins over file values. CLI flags still win over both.
    """
    user_home = os.environ.get("OMS_HOME") or os.path.expanduser("~/.oms")
    user_env = os.path.join(user_home, "env")
    if os.path.exists(user_env):
        dotenv.load_dotenv(user_env)
    if os.path.exists("oms.env"):
        dotenv.load_dotenv("oms.env")


setup_environment()

# ---------------------------------------------------------------------------
# PEP 562 lazy loading
#
# Package-level lazy import of known, static submodules: explicit (everything
# enumerated below), type-checker-visible, and the pattern datasmith kept. This
# is NOT the per-instance __getattr__ dispatch forbidden by Design Principles §4
# (reconciled in Package Structure & Workflow). Public symbols are added to
# _LAZY_IMPORTS by the milestone that introduces them.
# ---------------------------------------------------------------------------

_SUBMODULES: set[str] = {
    "utils",
    "core",
    "bank",
    "capture",
    "adapters",
    "forum",
    "distill",
    "web",
}

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # --- core (M3): the flat value-object surface the Overview REPL uses ---
    "Session": ("oms.core", "Session"),
    "Goal": ("oms.core", "Goal"),
    "Agent": ("oms.core", "Agent"),
    "Packet": ("oms.core", "Packet"),
    "KnowledgePacket": ("oms.core", "KnowledgePacket"),
    "Collection": ("oms.core", "Collection"),
    # --- capture (M4): the CanonicalTrace contract adapter authors conform to ---
    "CanonicalTrace": ("oms.capture", "CanonicalTrace"),
    "TraceEvent": ("oms.capture", "TraceEvent"),
    "ScrubReport": ("oms.capture", "ScrubReport"),
    # --- adapters (M5): the extension-point contract (builtins stay nested) ---
    "Adapter": ("oms.adapters", "Adapter"),
}

__all__ = [
    "__version__",
    "setup_environment",
    *sorted(_SUBMODULES),
    *sorted(_LAZY_IMPORTS),
]


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        mod = importlib.import_module(f"oms.{name}")
        globals()[name] = mod
        return mod

    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val

    raise AttributeError(f"module 'oms' has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__


# ---------------------------------------------------------------------------
# Static type-checking imports (never executed at runtime)
# ---------------------------------------------------------------------------
if TYPE_CHECKING:
    from oms import adapters as adapters
    from oms import bank as bank
    from oms import capture as capture
    from oms import core as core
    from oms import distill as distill
    from oms import forum as forum
    from oms import utils as utils
    from oms import web as web
