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
from manyagent._skills import REGISTRY, Dialect, Skill
from manyagent.adapters.skills import USAGE

# Codex reserves the `/` namespace, so the seamless surface is `$manyagent-<verb>`
# (plus natural-language auto-trigger via the `description` field). Tools are
# referenced as `manyagent.<tool>`; the per-call `approval_mode='prompt'` on the
# commit tool is the human gate. The per-verb prose lives once in manyagent._skills.
_DIALECT = Dialect(
    tool_ref=lambda name: f"manyagent.{name}",
    invocation=lambda slug: f"$manyagent-{slug}",
    args="the user's request",
    gate="Codex's per-tool approval prompt",
)


def _skill_body(skill: Skill) -> str:
    """SKILL.md for Codex: frontmatter (the natural-language `description`
    trigger + the `$manyagent-<verb>` invocation hint) + the shared
    dialect-substituted procedure body."""
    return (
        f"---\nname: manyagent-{skill.slug}\n"
        f"description: manyagent — {skill.description} "
        f"Invoke as $manyagent-{skill.slug} or by natural language describing the intent.\n---\n\n"
        f"# {_DIALECT.invocation(skill.slug)}{skill.arg_hint} — {skill.title}\n\n"
        f"{skill.body(_DIALECT)}\n"
    )


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
    for skill in REGISTRY:
        ops.append(
            FileOp(
                kind="create",
                path=root / "skills" / f"manyagent-{skill.slug}" / "SKILL.md",
                payload=_skill_body(skill),
                description=f"`$manyagent-{skill.slug}` skill — {skill.description}",
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
