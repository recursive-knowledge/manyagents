"""Codex CLI skill + MCP installer (M11).

Targets per scope:

- ``user``: ``~/.codex/skills/manyagent-<verb>/SKILL.md`` (four files, invokable as
  ``$manyagent-self-distill`` etc. — Codex reserves the ``/`` namespace for built-ins,
  so the seamless surface is ``$prefix`` invocation, plus natural-language
  auto-trigger via the skill's ``description`` field) + idempotent MERGE of
  the ``[mcp_servers.manyagent]`` block (with per-tool ``approval_mode = "prompt"``
  on ``commit_post`` and ``inject_commit``) into ``~/.codex/config.toml``.
- ``project``: ``<cwd>/.codex/...`` (note: project-scoped Codex config
  requires the user to mark the project trusted on first entry — see the
  research brief; for v1 we recommend per-user scope).

The TOML merge goes through ``tomlkit`` so the user's comments, key order,
and other ``[mcp_servers.*]`` entries survive both install and uninstall
byte-identically.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

from manyagent._installer import (
    FileOp,
    InstallPlan,
    Manifest,
    Scope,
    apply_plan,
    consent_prompt,
    load_manifest,
)
from manyagent.adapters.skills import USAGE

_VERBS: tuple[tuple[str, str], ...] = (
    (
        "self-distill",
        "draft and (on accept) commit ONE evidence-grounded reflection post to the active manyagent session",
    ),
    ("discuss", "draft and (on accept) commit ONE stance reply engaging a prior in-session post"),
    ("cross-distill", "curate goal-scoped posts (across sessions) into a 6-bucket Insight bundle"),
    ("inject", "preview a curated bundle, confirm, then write an injection-ledger row"),
)


def _skill_body(verb: str, description: str) -> str:
    """SKILL.md for Codex. The user types ``$<verb>`` (Codex reserves ``/``)
    or describes the intent in natural language — the model auto-triggers
    on the skill's ``description`` field."""
    head = (
        f"---\nname: manyagent-{verb}\ndescription: manyagent — {description}. "
        f"Invoke as $manyagent-{verb} or by natural language describing the intent.\n---\n\n"
    )
    if verb == "self-distill":
        return (
            head
            + """\
# $manyagent-self-distill — emit one reflection post to the active manyagent session

Procedure (follow exactly):

1. Call the `manyagent.self_distill_draft` MCP tool (pass guidance from the user's request).
2. Using the returned `instruction_for_host_llm` and the live conversation, draft ONE structured payload with these fields:
   - `load_bearing_assumption` — a concrete primitive (backticked identifier, dotted.path, `call()`, --flag)
   - `evidence` — verbatim from the conversation/trace
   - `evidence_ref` — a packet id, or null
   - `proposed_next` — a concrete next action
   - `predicted_outcome` — a falsifiable prediction
   - `confidence` — "low" | "medium" | "high"
3. Show the draft verbatim with a recommended ★ (high=5, medium=3, low=2).
4. Then call `manyagent.commit_post(kind='reflection', structured={...}, rating=N)` directly with the recommended rating. Do NOT ask a separate "accept?" question — Codex's per-tool approval prompt (`approval_mode='prompt'`) IS the user's single gate; nothing persists unless they approve.
5. If the user denies the approval or asks for changes, revise the draft and repeat (C1: no persistence without consent).
"""
        )
    if verb == "discuss":
        return (
            head
            + """\
# $manyagent-discuss — emit one stance reply to a prior in-session post

The user's prompt may contain `@<packet_id>` and/or one of `agree`/`disagree`/`synthesize` (default `synthesize`).

Procedure:

1. Parse the prompt for a `@<packet_id>` and a stance.
2. Call `manyagent.discuss_draft(stance=..., packet=...)`.
3. If the tool returns an error ("no related posts"), tell the user to run $manyagent-self-distill first and STOP.
4. Using the returned `instruction_for_host_llm` (which includes the ranked prior posts), draft a reply with the same 5 fields as $manyagent-self-distill, engaging the post named in `reply_to`.
5. Show the draft verbatim, then call `manyagent.commit_post(kind='reply', structured={...}, reply_to=..., stance=...)` directly. Do NOT ask a separate "accept?" question — the per-tool approval prompt IS the single gate.
6. If the user denies the approval or asks for changes, revise and repeat (C1).
"""
        )
    if verb == "cross-distill":
        return (
            head
            + """\
# $manyagent-cross-distill — curate goal-scoped posts into a bundle

Procedure:

1. Call `manyagent.cross_distill`. The curator runs in the background.
2. If the tool returns `{"ok": false, "error": "Run /self-distill first!"}`, tell the user to run $manyagent-self-distill first and STOP.
3. Otherwise, summarize the result: `bundle_id`, `scope`, `goal`, and per-bucket counts.

Idempotent — same posts ⇒ same bundle, no re-spend.
"""
        )
    if verb == "inject":
        return (
            head
            + """\
# $manyagent-inject — preview a bundle and (on accept) record an injection

The user's prompt may contain `@<packet_id>`. If omitted, the latest non-quarantined distill is used.

Procedure:

1. Call `manyagent.inject_preview(packet=...)`.
2. If the tool returns an error (no bundle / quarantined), report it and STOP.
3. Show the preview verbatim.
4. Then call `manyagent.inject_commit(packet=<id>)` directly. Do NOT ask a separate "inject? [y/n]" question — Codex's per-call confirmation on `inject_commit` IS the user's single gate; the ledger row is only written if they approve.
5. If the user denies the confirmation, STOP — nothing is recorded.
"""
        )
    raise ValueError(f"unknown verb {verb!r}")


def _mcp_server_value() -> dict[str, Any]:
    """``[mcp_servers.manyagent]`` table value. Codex MCP servers don't get full
    env passthrough — declare ``env_vars`` allowlist explicitly."""
    return {
        "command": sys.executable,
        "args": ["-m", "manyagent._mcp"],
        "env_vars": ["MANYAGENT_SESSION", "MANYAGENT_HOME"],
        "startup_timeout_sec": 10.0,
    }


def _tool_approval_value() -> dict[str, str]:
    """Per-tool override. Codex's ``approval_mode = "prompt"`` is the native
    per-call confirmation UI — our `commit_post` / `inject_commit` gate."""
    return {"approval_mode": "prompt"}


def _root(scope: str) -> Path:
    if scope == "user":
        return Path.home() / ".codex"
    if scope == "project":
        return Path.cwd() / ".codex"
    raise ValueError(f"unknown scope {scope!r}")


def build_plan(*, session_id: str | None, oma_home: Path | None = None, scope: str = "user") -> InstallPlan:
    # ``oma_home`` accepted for API symmetry with the other adapters
    # (M11 follow-up P3) — Codex's target dirs are agent-owned (``~/.codex/``),
    # so the path isn't used here. Gemini's bundle is manyagent-owned and needs it.
    root = _root(scope)
    ops: list[FileOp] = []
    for verb, description in _VERBS:
        ops.append(
            FileOp(
                kind="create",
                path=root / "skills" / f"manyagent-{verb}" / "SKILL.md",
                payload=_skill_body(verb, description),
                description=f"`$manyagent-{verb}` skill — {description}",
            )
        )

    config_path = root / "config.toml"
    # The MCP server entry under [mcp_servers.manyagent] — one MERGE op.
    ops.append(
        FileOp(
            kind="merge",
            path=config_path,
            payload={"__section__": "mcp_servers.manyagent", "__value__": _mcp_server_value()},
            description="Register the manyagent MCP server under `[mcp_servers.manyagent]` (preserves all other servers + comments via tomlkit).",
            merge_keys=("mcp_servers.manyagent",),
        )
    )
    # Per-tool approval-mode overrides — two more MERGE ops on the same file.
    # Splitting keeps uninstall granular (each is a separate manifest entry).
    for tool in ("commit_post", "inject_commit"):
        ops.append(
            FileOp(
                kind="merge",
                path=config_path,
                payload={"__section__": f"mcp_servers.manyagent.tools.{tool}", "__value__": _tool_approval_value()},
                description=f"Force per-call approval prompt on `{tool}` (the human gate).",
                merge_keys=(f"mcp_servers.manyagent.tools.{tool}",),
            )
        )
    return InstallPlan(
        adapter="codex",
        scope=cast("Scope", scope),
        ops=ops,
        session_id=session_id,
        commands=[(f"$manyagent-{verb}", blurb) for verb, blurb in USAGE],
    )


def install(
    *,
    session_id: str | None,
    oma_home: Path,
    scope: str = "user",
    dry_run: bool = False,
    input_fn: object = input,
    output_fn: object = print,
) -> Manifest | None:
    plan = build_plan(session_id=session_id, oma_home=oma_home, scope=scope)
    existing = load_manifest("codex", oma_home) is not None
    if not consent_prompt(
        plan,
        input_fn=input_fn,  # type: ignore[arg-type]
        output_fn=output_fn,  # type: ignore[arg-type]
        manifest_exists=existing,
        oma_home=oma_home,
        dry_run=dry_run,
    ):
        return None
    return apply_plan(plan, oma_home=oma_home, dry_run=dry_run)
