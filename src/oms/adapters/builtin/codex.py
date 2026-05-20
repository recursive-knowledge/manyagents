"""``codex`` — Codex CLI ``codex exec --json``, native structured →
``source_fidelity = "structured"``.

Codex's JSON stream is item-oriented (``message``/``reasoning``/
``*_call``/``item.completed``). The mapper is tolerant: unknown shapes fall
back to a ``system`` event rather than dropping session content.
"""

from __future__ import annotations

import json

from oms.adapters.builtin import _jsonl, _msg_text, _StructuredBuiltin
from oms.capture import TraceEvent

_CALL_TYPES = {"function_call", "local_shell_call", "tool_call", "custom_tool_call"}


class CodexAdapter(_StructuredBuiltin):
    name = "codex"
    binary = "codex"
    version = "1"

    def _distill_cmd_prefix(self) -> list[str]:
        return ["codex", "exec"]

    def install_skills(
        self,
        *,
        session_id: str | None,
        oma_home: object,
        scope: str = "user",
        dry_run: bool = False,
    ) -> object | None:
        """Install ``$oms-<verb>`` skills + merge ``[mcp_servers.oms]`` into
        ``~/.codex/config.toml`` (tomlkit-preserving). Codex reserves the
        ``/`` namespace; bare verbs surface as ``$oms-<verb>`` instead."""
        from pathlib import Path

        from oms.adapters.skills.codex import install

        return install(
            session_id=session_id,
            oma_home=Path(str(oma_home)),
            scope=scope,
            dry_run=dry_run,
        )

    def _parse(self, raw: str) -> list[TraceEvent]:
        events: list[TraceEvent] = []
        for ts, obj in enumerate(_jsonl(raw)):
            # codex >=0.114 wraps payloads in {"type":"item.completed","item":{...}}
            raw_item = obj.get("item")
            item: dict[str, object] = raw_item if isinstance(raw_item, dict) else obj
            t = str(item.get("type", obj.get("type", "")))
            role = item.get("role", "")
            if t == "_text":
                events.append(TraceEvent(float(ts), "system", str(obj.get("text", ""))))
            elif t in ("message", "agent_message") and role == "user":
                events.append(TraceEvent(float(ts), "user", _msg_text(item)))
            elif t in ("message", "agent_message", "reasoning"):
                txt = _msg_text(item) or str(item.get("text", ""))
                if txt:
                    events.append(TraceEvent(float(ts), "agent", txt))
            elif t in _CALL_TYPES:
                events.append(TraceEvent(float(ts), "tool_call", json.dumps(item)))
            elif t in ("function_call_output", "tool_result", "local_shell_call_output"):
                events.append(TraceEvent(float(ts), "tool_result", str(item.get("output", _msg_text(item)))))
            else:
                events.append(TraceEvent(float(ts), "system", json.dumps(obj)))
        return events
