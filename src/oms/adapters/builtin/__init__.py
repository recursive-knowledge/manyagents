"""First-party adapters: ``claude``, ``codex``, ``gemini`` (real) + ``qwen``
(stub). Reference impls and the smallest examples for contributors
(oms.adapters.md "Modules").

Shared helpers live here so each builtin stays small. ``capture()`` reads the
session's native material from ``self.trace_source`` — set by ``invoke()`` in
live use, passed directly in tests (offline conformance never shells out a
real CLI).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oms.adapters.base import Adapter, AdapterError, PromptPrefixInjector, RawTrace, run_agent_subprocess
from oms.capture import CanonicalTrace, TraceEvent
from oms.utils import config, provider


def _read_source(adapter: Adapter) -> str:
    """Read the native session material an adapter's ``capture()`` parses."""
    src = getattr(adapter, "trace_source", None)
    if src is None:
        raise AdapterError(f"{adapter.name}: no trace_source (call invoke() first, or pass one)")
    p = Path(src)
    if not p.is_file():
        raise AdapterError(f"{adapter.name}: trace_source {p} not found")
    return p.read_text(encoding="utf-8", errors="replace")


def _make_trace(adapter: Adapter, events: list[TraceEvent], raw: str) -> CanonicalTrace:
    """Assemble the pre-scrub/pre-bound ``RawTrace`` (== ``CanonicalTrace``).
    ``bytes_in`` is the raw native size; scrub/bound/persist are oms.capture's."""
    return CanonicalTrace(
        session_id=adapter.session_id,
        agent_id=adapter.agent_id,
        adapter=adapter.name,
        events=events,
        source_fidelity=adapter.source_fidelity,
        bytes_in=len(raw.encode("utf-8")),
    )


def _msg_text(message: object) -> str:
    """Flatten a Claude/Codex-style message payload to text."""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text")
            ]
            return "\n".join(p for p in parts if p)
    return ""


@dataclass
class _HeadlessModel:
    """An adapter's own model exposed to ``oms.distill`` via a headless
    shell-out (the ``oms.utils.provider`` seam; duck-types as ``Provider``)."""

    name: str
    cmd_prefix: list[str]  # e.g. ["claude", "-p"] — prompt appended
    extract: Callable[[str], str] | None = None  # CLI-envelope unwrap, per adapter

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        # The distiller is NOT the wrapped session: drop OMS_SESSION so the
        # user-scope oms._hook inside the spawned CLI neither binds the
        # distiller's own harness session into the session's bindings file nor
        # receives the session's inject stash (the 2026-06-10 wrong-session
        # contamination). Run from an EMPTY temp cwd for the same reason
        # (2026-06-11): in the repo cwd the spawned CLI loads its own project
        # context (CLAUDE.md, git status) and blends it into the post as if it
        # were session evidence — the trace in the prompt must be the
        # distiller's only knowledge of the session.
        env = {k: v for k, v in os.environ.items() if k != "OMS_SESSION"}
        with tempfile.TemporaryDirectory(prefix="oms-distill-") as hermetic_cwd:
            rc, out, err, _ = run_agent_subprocess(
                [*self.cmd_prefix, prompt],
                timeout=config.OMS_DISTILL_TIMEOUT_S,
                agent_name=self.name,
                cwd=hermetic_cwd,
                env=env,
            )
        if rc != 0:
            raise AdapterError(f"{self.name} headless distill failed (rc={rc}): {err[:500]}")
        out = out.strip()
        return self.extract(out) if self.extract is not None else out

    def rate_limit_signal(self, raw_error: str) -> provider.RateLimit | None:
        return provider.rate_limit_signal(raw_error, provider=self.name)


class _StructuredBuiltin(PromptPrefixInjector, Adapter):
    """Common machinery for the native-log (``structured``) builtins."""

    source_fidelity = "structured"

    def __init__(self, *, session_id: str = "", agent_id: str = "", trace_source: str | Path | None = None) -> None:
        super().__init__(session_id=session_id, agent_id=agent_id)
        self.trace_source = trace_source

    def invoke(self, args: list[str]) -> subprocess.Popen[str]:
        from oms.adapters.base import _register_proc

        proc = subprocess.Popen(
            [self.binary, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        _register_proc(proc)
        return proc

    def distill_model(self) -> object | None:
        if not self.is_available():
            return None
        return _HeadlessModel(self.name, self._distill_cmd_prefix(), extract=self._distill_extract)

    def _distill_cmd_prefix(self) -> list[str]:  # overridden per agent
        raise NotImplementedError

    def _distill_extract(self, raw: str) -> str:
        """Unwrap the CLI's headless output envelope (identity by default;
        claude overrides — its ``--output-format json`` wraps the answer)."""
        return raw

    def _parse(self, raw: str) -> list[TraceEvent]:  # overridden per agent
        raise NotImplementedError

    def capture(self) -> RawTrace:
        raw = _read_source(self)
        return _make_trace(self, self._parse(raw), raw)


def _jsonl(raw: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            out.append({"type": "_text", "text": line})
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
