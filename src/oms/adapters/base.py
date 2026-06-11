"""The ``Adapter`` ABC — the whole contract (oms.adapters.md "The four-method
contract — Settled").

An *adapter* is the small OO integration that sources a session's trace and
injects context for one terminal agent. It produces **raw** material only;
normalization/bounding/secret-scrub is ``oms.capture``'s job — keeping adapters
dumb so the public corpus is not only as safe as the worst third-party plugin
(Design Principles §2).

``RawTrace`` is **not a separate shape**: it is the pre-scrub/pre-bound
lifecycle stage of ``oms.capture.CanonicalTrace``. The adapter author emits
``CanonicalTrace``-shaped material (``oms.capture.md`` "Validate conformance");
``oms.capture`` validates → scrubs → bounds → persists. The ABC keeps the
frozen ``capture() -> RawTrace`` wording via this alias (see the
2026-05-19 Decision-log entry on ``oms.adapters.md``).

The subprocess-lifecycle seam (process-group kill, ``terminate_all_agents``)
is ported from datasmith's surviving design and lives here so M8's two-stage
SIGINT handler can reach session-detached agent processes.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oms.capture import CanonicalTrace
from oms.utils.log import get_logger

logger = get_logger("adapters")

# Lifecycle-stage alias (NOT a distinct dataclass): an adapter returns a
# CanonicalTrace that is still raw — scrub_report empty, bytes_out 0.
RawTrace = CanonicalTrace


class AdapterError(RuntimeError):
    """Base for adapter-layer failures."""


class NotInstalled(AdapterError):
    """The wrapped CLI binary is not on PATH (e.g. the ``qwen`` stub)."""


# --------------------------------------------------------------------------- #
# Subprocess lifecycle (mirrors datasmith agents/installed/base.py). Agent
# processes run in their own session (start_new_session=True) so a terminal
# CTRL+C does not reach them; M8's SIGINT handler calls terminate_all_agents().
# --------------------------------------------------------------------------- #

_active_procs: set[subprocess.Popen[str]] = set()
_active_procs_lock = threading.Lock()


def _register_proc(proc: subprocess.Popen[str]) -> None:
    with _active_procs_lock:
        _active_procs.add(proc)


def _unregister_proc(proc: subprocess.Popen[str]) -> None:
    with _active_procs_lock:
        _active_procs.discard(proc)


# Windows' ``signal`` module has no SIGKILL; fall back to SIGTERM there (the
# Popen.terminate() branch in _kill_process_group still does the right thing).
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _kill_process_group(proc: subprocess.Popen[str], sig: int = signal.SIGTERM) -> None:
    """Terminate the process and (on POSIX) its whole process group, since
    agent subprocesses are launched with ``start_new_session=True`` to keep
    the terminal's SIGINT off them. On Windows there's no process-group
    concept here — ``Popen.terminate()`` (sig != SIGKILL) or
    ``Popen.kill()`` (sig == SIGKILL) is the equivalent."""
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform != "win32":  # mypy: narrows so os.{killpg,getpgid} resolve
            os.killpg(os.getpgid(proc.pid), sig)
        elif sig == _SIGKILL:  # Windows: SIGKILL collapses to SIGTERM (above)
            proc.kill()
        else:
            proc.terminate()


def terminate_all_agents(*, force: bool = False) -> None:
    """Kill every tracked agent subprocess (the M8 SIGINT seam). ``force``
    sends SIGKILL instead of SIGTERM."""
    sig = _SIGKILL if force else signal.SIGTERM
    for proc in list(_active_procs):  # snapshot — concurrent mutation safe
        _kill_process_group(proc, sig)


def run_agent_subprocess(
    cmd: list[str],
    *,
    timeout: int = 3600,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    agent_name: str = "agent",
) -> tuple[int, str, str, float]:
    """Run a CLI command with process-group cleanup on interrupt/timeout.

    Returns ``(returncode, stdout, stderr, duration_s)``; on timeout the group
    is killed and partial output returned with ``returncode=-1``. Re-raises
    ``KeyboardInterrupt`` after cleanup. Used by the builtins' headless
    ``distill_model()`` shell-out.
    """
    start = time.time()
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
        _register_proc(proc)
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr, time.time() - start
    except subprocess.TimeoutExpired as exc:
        logger.warning("%s timed out after %ds — capturing partial output", agent_name, timeout)
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        if proc is not None:
            _kill_process_group(proc, _SIGKILL)
            proc.wait()
        return -1, out, err, time.time() - start
    except KeyboardInterrupt:
        if proc is not None:
            _kill_process_group(proc, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
        raise
    finally:
        if proc is not None:
            _unregister_proc(proc)
            if proc.poll() is None:
                _kill_process_group(proc, _SIGKILL)
                proc.wait()


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


class Adapter(ABC):
    """One CLI's integration. ``name``/``binary``/``version`` are *class*
    attributes (registry discriminates by ``name``); the four behavioural
    methods are abstract; ``distill_model()`` is an optional hook.

    A runtime instance is bound to one session (``session_id``/``agent_id``)
    so the no-arg ``capture()`` can stamp the trace — the *class* stays
    stateless pluggable code (Adapter vs. Agent — Settled)."""

    name: str = ""
    binary: str = ""
    version: str = ""
    source_fidelity: str = "structured"  # builtin default; gemini/qwen override

    def __init__(self, *, session_id: str = "", agent_id: str = "") -> None:
        self.session_id = session_id
        self.agent_id = agent_id

    @classmethod
    def is_available(cls) -> bool:
        """True iff the wrapped binary is on PATH."""
        return bool(cls.binary) and shutil.which(cls.binary) is not None

    @abstractmethod
    def invoke(self, args: list[str]) -> subprocess.Popen[str]:
        """Start the agent as a session-attached subprocess (registered for
        ``terminate_all_agents``). Returns the live process."""

    @abstractmethod
    def capture(self) -> RawTrace:
        """Return RAW session material as a ``CanonicalTrace`` (native log or
        PTY bytes mapped to events; ``source_fidelity`` declared). Scrub,
        size-bounding, and persistence are NOT done here — ``oms.capture``
        owns that, centrally and uniformly."""

    @abstractmethod
    def inject(self, context: str) -> None:
        """Make ``context`` available to the agent's next turn."""

    @abstractmethod
    def retrieve(self) -> str | None:
        """Return (and clear) any pending injected context."""

    def distill_model(self) -> object | None:
        """Optional: expose this agent's own model to ``oms.distill`` (the
        ``oms.utils.provider`` seam). Default: no model → caller falls back to
        the OpenAI-compatible provider."""
        return None

    def install_skills(
        self,
        *,
        session_id: str | None,
        oma_home: object,
        scope: str = "user",
        dry_run: bool = False,
    ) -> object | None:
        """Optional (M11): install the in-agent skills + MCP server entry so the
        user can type ``/self-distill`` (etc.) inside this agent's UI. Default:
        no-op (returns None). Per-adapter overrides delegate to
        ``oms.adapters.skills.<name>.install`` and return a
        :class:`oms._installer.Manifest` (or None if the user declined consent).
        Every filesystem write flows through ``oms._installer`` for transparency
        + ``oms uninstall <adapter>`` reversal."""
        return None

    def mine(self, ctx: MineContext) -> dict[str, Any] | None:
        """Optional (M13): read the harness's LOCAL files — its own transcript
        of the wrapped session (Claude Code:
        ``~/.claude/projects/<munged-cwd>/<session-id>.jsonl``) — and return
        the normalized conversation artifact persisted as the ``harness``
        rendition (Trace Renditions & Mining §4a shape: ``miner_version`` /
        ``binding`` / ``completeness`` / ``run_started`` / ``segments``).
        Default: no miner → None, nothing stored. The CLI wraps the call in a
        never-fail guard, but implementations must still parse defensively —
        the on-disk formats are undocumented and drift. Per-adapter overrides
        delegate to ``oms.adapters.miners.<name>.mine`` (the skills pattern)."""
        return None


@dataclass(frozen=True)
class MineContext:
    """Everything :meth:`Adapter.mine` needs to bind a finished wrapped run
    to the harness's local artifacts (M13).

    ``bindings`` are the ``oms._hook`` lifecycle records for this run
    (``harness_session_id`` + ``transcript_path`` per SessionStart/End —
    one PTY run spans several harness sessions across ``/clear``); the
    ``window`` (wall-clock start/end) powers the mtime-scan fallback when
    no hooks were installed."""

    cwd: Path
    window: tuple[float, float]
    bindings: list[dict[str, Any]] = field(default_factory=list)


class PromptPrefixInjector:
    """The lowest-common-default ``inject``/``retrieve`` (oms.adapters.md
    "Mid-session injection — Open"): stash a context block, prepend it to the
    next prompt, clear on retrieve. Builtins mix this in for v1."""

    _pending_context: str | None = None

    def inject(self, context: str) -> None:
        self._pending_context = context

    def retrieve(self) -> str | None:
        ctx, self._pending_context = self._pending_context, None
        return ctx

    def _consume_prefix(self, prompt: str) -> str:
        """Builtin ``invoke()`` prepends any pending context to the prompt."""
        ctx = self.retrieve()
        return f"{ctx}\n\n{prompt}" if ctx else prompt
