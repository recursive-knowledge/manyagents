"""``gemini`` — Gemini CLI via a **PTY tee** → ``source_fidelity = "pty"``.

The author here only has a terminal tee, not native structured logs, so the
trace is raw bytes the author still makes schema-conformant: one ``system``
event carrying the tee. ``oms.distill`` must degrade gracefully on ``"pty"``
(no tool-call structure) — that contract is exercised in M4/M7.

The tee is stored as ``{"pty_tee": "<raw terminal bytes>"}`` so every builtin
sample shares the ``.json`` fixture convention.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from oms.adapters.base import Adapter, PromptPrefixInjector, RawTrace
from oms.adapters.builtin import _HeadlessModel, _make_trace, _read_source
from oms.capture import TraceEvent


class GeminiAdapter(PromptPrefixInjector, Adapter):
    name = "gemini"
    binary = "gemini"
    version = "1"
    source_fidelity = "pty"

    def __init__(self, *, session_id: str = "", agent_id: str = "", trace_source: str | Path | None = None) -> None:
        super().__init__(session_id=session_id, agent_id=agent_id)
        self.trace_source = trace_source

    def install_skills(
        self,
        *,
        session_id: str | None,
        oma_home: object,
        scope: str = "user",
        dry_run: bool = False,
    ) -> object | None:
        """Stage the gemini-extension bundle under ``$OMS_HOME/extensions/
        gemini-oms/`` and ``gemini extensions link`` it (symlink semantics →
        ``oms`` pip-upgrade auto-propagates the bundle). Idempotent via
        pre-uninstall."""
        from oms.adapters.skills.gemini import install

        return install(
            session_id=session_id,
            oma_home=Path(str(oma_home)),
            scope=scope,
            dry_run=dry_run,
        )

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

    def capture(self) -> RawTrace:
        raw = _read_source(self)
        tee = json.loads(raw).get("pty_tee", "") if raw.lstrip().startswith("{") else raw
        events = [TraceEvent(0.0, "system", str(tee))] if tee else []
        return _make_trace(self, events, raw)

    def distill_model(self) -> object | None:
        if not self.is_available():
            return None
        return _HeadlessModel(self.name, ["gemini", "-p"])
