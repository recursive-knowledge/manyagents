"""``claude`` — Claude Code, native structured logs → ``source_fidelity =
"structured"`` (oms.adapters.md "Capture fidelity per agent — Settled").

Maps Claude Code ``--output-format stream-json`` events to ``TraceEvent``s.
The author owns this mapping (the contract); ``oms.capture`` validates it.
"""

from __future__ import annotations

import json

from oms.adapters.builtin import _jsonl, _msg_text, _StructuredBuiltin
from oms.capture import TraceEvent


class ClaudeAdapter(_StructuredBuiltin):
    name = "claude"
    binary = "claude"
    version = "1"

    def _distill_cmd_prefix(self) -> list[str]:
        return ["claude", "-p"]

    def install_skills(
        self,
        *,
        session_id: str | None,
        oma_home: object,
        scope: str = "user",
        dry_run: bool = False,
    ) -> object | None:
        """Drop oms's skill files + register the MCP server entry so the user
        can type ``/self-distill`` (etc.) inside Claude Code. Idempotent;
        every write is logged in ``$OMS_HOME/installed/claude.json`` and
        ``oms uninstall claude`` reverses it cleanly (oms.adapters.skills.claude)."""
        from pathlib import Path

        from oms.adapters.skills.claude import install

        return install(
            session_id=session_id,
            oma_home=Path(str(oma_home)),
            scope=scope,
            dry_run=dry_run,
        )

    def _parse(self, raw: str) -> list[TraceEvent]:  # noqa: C901 — one branch per stream-json event type; splitting would just shuffle the same complexity
        events: list[TraceEvent] = []
        for ts, obj in enumerate(_jsonl(raw)):
            t = obj.get("type", "")
            if t == "_text":
                events.append(TraceEvent(float(ts), "system", str(obj.get("text", ""))))
            elif t == "user":
                events.append(TraceEvent(float(ts), "user", _msg_text(obj.get("message", ""))))
            elif t == "assistant":
                txt = _msg_text(obj.get("message", ""))
                if txt:
                    events.append(TraceEvent(float(ts), "agent", txt))
            elif t == "tool_use":
                events.append(
                    TraceEvent(float(ts), "tool_call", f"{obj.get('name', '')} {json.dumps(obj.get('input', {}))}")
                )
            elif t == "tool_result":
                events.append(TraceEvent(float(ts), "tool_result", _msg_text(obj.get("content", ""))))
            elif t == "result":
                r = obj.get("result", "")
                if r:
                    events.append(TraceEvent(float(ts), "agent", str(r)))
            elif t == "system":
                events.append(TraceEvent(float(ts), "system", json.dumps(obj)))
        return events
