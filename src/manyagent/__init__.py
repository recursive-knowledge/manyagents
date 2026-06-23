"""ManyAgent — wrap installed coding-agent CLIs; curate cross-session knowledge.

Distribution name: ``manyagent``. Import name: ``manyagent``. Console script: ``ma``.
Identity is fixed here and in ``pyproject.toml`` and is never re-derived as a
string elsewhere (datasmith identity rule; Package Structure & Workflow).
"""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING

import dotenv

__version__ = "0.4.0"


def setup_environment() -> None:
    """Load environment variables from ``./manyagent.env`` **and** ``~/.manyagent/env``.

    Two layers; ``dotenv.load_dotenv`` never overwrites an already-set process
    var, so the FIRST source to set a key wins and the order below is the
    precedence:

    1. ``./manyagent.env`` — the project-scoped overrides (M0).
    2. ``~/.manyagent/env`` — the user-level fallback, written by ``manyagent init``.
       Loaded so an MCP server launched by Claude Code / Codex / Gemini
       *outside* a project directory still finds the Bank credentials.

    Live process env always wins over both files; CLI flags win over everything.
    """
    if os.path.exists("manyagent.env"):
        dotenv.load_dotenv("manyagent.env")
    # expanduser on the override too (every other MANYAGENT_HOME consumer does):
    # a literal-tilde value — dotenv files and MCP-config env blocks don't
    # shell-expand — must resolve to the same file `manyagent init` writes.
    user_home = os.path.expanduser(os.environ.get("MANYAGENT_HOME") or "~/.manyagent")
    user_env = os.path.join(user_home, "env")
    if os.path.exists(user_env):
        dotenv.load_dotenv(user_env)


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
    "testing",
    "web",
}

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # --- core (M3): the flat value-object surface the Overview REPL uses ---
    "Session": ("manyagent.core", "Session"),
    "Goal": ("manyagent.core", "Goal"),
    "Agent": ("manyagent.core", "Agent"),
    "Packet": ("manyagent.core", "Packet"),
    "KnowledgePacket": ("manyagent.core", "KnowledgePacket"),
    "Collection": ("manyagent.core", "Collection"),
    # --- capture (M4): the CanonicalTrace contract adapter authors conform to ---
    "CanonicalTrace": ("manyagent.capture", "CanonicalTrace"),
    "TraceEvent": ("manyagent.capture", "TraceEvent"),
    "ScrubReport": ("manyagent.capture", "ScrubReport"),
    # --- adapters (M5): the extension-point contract (builtins stay nested) ---
    "Adapter": ("manyagent.adapters", "Adapter"),
}

__all__ = [
    "__version__",
    "setup_environment",
    *sorted(_SUBMODULES),
    *sorted(_LAZY_IMPORTS),
]


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        mod = importlib.import_module(f"manyagent.{name}")
        globals()[name] = mod
        return mod

    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val

    raise AttributeError(f"module 'manyagent' has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__


# ---------------------------------------------------------------------------
# Static type-checking imports (never executed at runtime)
# ---------------------------------------------------------------------------
if TYPE_CHECKING:
    from manyagent import adapters as adapters
    from manyagent import bank as bank
    from manyagent import capture as capture
    from manyagent import core as core
    from manyagent import distill as distill
    from manyagent import forum as forum
    from manyagent import testing as testing
    from manyagent import utils as utils
    from manyagent import web as web
