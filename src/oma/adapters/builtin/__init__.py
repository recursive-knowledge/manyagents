"""First-party adapters: ``claude``, ``codex``, ``gemini`` (real) + ``qwen``
(stub). Reference impls and the smallest examples for contributors
(oma.adapters.md "Modules").

Shared helpers live here so each builtin stays small. ``capture()`` reads the
session's native material from ``self.trace_source`` — set by ``invoke()`` in
live use, passed directly in tests (offline conformance never shells out a
real CLI).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from oma.adapters.base import Adapter, AdapterError, PromptPrefixInjector, RawTrace, run_agent_subprocess
from oma.capture import CanonicalTrace, TraceEvent
from oma.utils import config, provider


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
    ``bytes_in`` is the raw native size; scrub/bound/persist are oma.capture's."""
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
    """An adapter's own model exposed to ``oma.distill`` via a headless
    shell-out (the ``oma.utils.provider`` seam; duck-types as ``Provider``)."""

    name: str
    cmd_prefix: list[str]  # e.g. ["claude", "-p"] — prompt appended

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        rc, out, err, _ = run_agent_subprocess(
            [*self.cmd_prefix, prompt],
            timeout=config.OMA_DISTILL_TIMEOUT_S,
            agent_name=self.name,
        )
        if rc != 0:
            raise AdapterError(f"{self.name} headless distill failed (rc={rc}): {err[:500]}")
        return out.strip()

    def rate_limit_signal(self, raw_error: str) -> provider.RateLimit | None:
        return provider.rate_limit_signal(raw_error, provider=self.name)


class _StructuredBuiltin(PromptPrefixInjector, Adapter):
    """Common machinery for the native-log (``structured``) builtins."""

    source_fidelity = "structured"

    def __init__(self, *, session_id: str = "", agent_id: str = "", trace_source: str | Path | None = None) -> None:
        super().__init__(session_id=session_id, agent_id=agent_id)
        self.trace_source = trace_source

    def invoke(self, args: list[str]) -> subprocess.Popen[str]:
        from oma.adapters.base import _register_proc

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
        return _HeadlessModel(self.name, self._distill_cmd_prefix())

    def _distill_cmd_prefix(self) -> list[str]:  # overridden per agent
        raise NotImplementedError

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
