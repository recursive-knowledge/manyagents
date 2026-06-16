"""Claude Code skill + MCP + hooks installer (M11; hooks: M12 groundwork).

Targets per scope:

- ``user``: ``~/.claude/skills/<verb>/SKILL.md`` (4 files, bare-verb dirs) +
  the manyagent MCP server registered via ``claude mcp add --scope user`` (the
  user-scope MCP file is ``~/.claude.json``, an internal of Claude Code we
  never write directly — the M11.2 lesson) + MERGE two lifecycle hook
  entries into the shared ``hooks`` arrays of ``~/.claude/settings.json``
  (the documented home for hooks). The user types ``/self-distill``
  natively inside Claude Code.
- ``project``: same shapes under ``<cwd>/.claude/`` with
  ``claude mcp add --scope project``.

The MCP server and hook commands use ``sys.executable`` (the python `manyagent`
itself is running under) so the agent's spawned processes match the
install, even inside a uv-managed venv. ``MANYAGENT_SESSION`` is **not** baked
into any config (it changes per session) — the wrapper exports it in the
parent env at PTY-spawn time; the MCP server and hooks inherit it.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import cast

from manyagent._installer import (
    CLIAction,
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

# Claude Code unifies skills + commands; the user types `/self-distill` natively
# and the host LLM's `commit_post` / `inject_commit` permission prompt is the
# single human gate. Tools are referenced as `mcp__manyagent__<tool>`, args arrive
# as `$ARGUMENTS`. The per-verb procedure prose lives once in manyagent._skills.
_DIALECT = Dialect(
    tool_ref=lambda name: f"mcp__manyagent__{name}",
    invocation=lambda slug: f"/{slug}",
    args="$ARGUMENTS",
    gate="Claude Code's permission prompt",
)


def _skill_body(skill: Skill) -> str:
    """Render one verb's ``SKILL.md`` — Claude frontmatter (the `name:` is the
    `/command`; `allowed-tools` lists only the un-gated draft tool so the
    commit tool's permission prompt stays the human accept moment) + the
    shared dialect-substituted procedure body."""
    return (
        "---\n"
        f"name: {skill.slug}\n"
        f"description: {skill.description}\n"
        "disable-model-invocation: true\n"
        "allowed-tools:\n"
        f"  - {_DIALECT.tool_ref(skill.allowed_tool)}\n"
        "---\n\n"
        f"# {_DIALECT.invocation(skill.slug)}{skill.arg_hint} — {skill.title}\n\n"
        f"{skill.body(_DIALECT)}\n"
    )


def _target_root(scope: str) -> Path:
    if scope == "user":
        return Path.home() / ".claude"
    if scope == "project":
        return Path.cwd() / ".claude"
    raise ValueError(f"unknown scope {scope!r}")


def _mcp_cli_actions(scope: str) -> list[CLIAction]:
    """Register the manyagent MCP server via the official ``claude mcp`` CLI — the
    file location is an internal detail of Claude Code (``~/.claude.json``,
    not ``~/.claude/settings.json``), so going through the CLI is the
    documented, restart-safe path. Idempotent: ``remove`` (ignore failure)
    then ``add``."""
    cli_scope = "project" if scope == "project" else "user"
    add = ("claude", "mcp", "add", "--scope", cli_scope, "manyagent", "--", sys.executable, "-m", "manyagent._mcp")
    remove = ("claude", "mcp", "remove", "--scope", cli_scope, "manyagent")
    return [
        # Belt-and-suspenders: an idempotent re-install should overwrite the
        # entry. `claude mcp remove` exits nonzero if it's not there; we
        # swallow that and run `add` anyway.
        CLIAction(
            install_argv=remove,
            uninstall_argv=("true",),
            description=f"pre-clear any existing manyagent MCP server (--scope {cli_scope})",
            failure_ok=True,  # exit 1 ("no server named manyagent") IS the fresh install
        ),
        CLIAction(
            install_argv=add,
            uninstall_argv=remove,
            description=f"register the manyagent MCP server with Claude Code (--scope {cli_scope})",
        ),
    ]


# Lifecycle hooks (M12 groundwork). Claude Code invokes the command with a
# JSON payload on stdin ({session_id, transcript_path, cwd, hook_event_name,
# …}) at session start and end. ``manyagent._hook`` appends that payload to
# ``$MANYAGENT_HOME/bindings/<session>.jsonl`` when MANYAGENT_SESSION is set (i.e. only
# for manyagent-wrapped runs; it exits silently otherwise) — the binding that lets
# ``Adapter.mine()`` (M13) find the harness's transcript files, including the
# extra session ids a mid-run ``/clear`` rolls over to. SessionStart and
# SessionEnd are deliberately the only events: per-tool events would put a
# subprocess spawn on the host's hot path for no binding gain.
_HOOK_EVENTS: tuple[str, ...] = ("SessionStart", "SessionEnd")


# The staleness marker for the hook entries: any settings.json hook item
# containing this substring is recognizably ours, so reinstalls purge stale
# variants (a moved/recreated venv changes the baked interpreter path) and
# uninstall can clean an entry even after the user/host tool edited it.
_HOOK_MARKER = "-m manyagent._hook"


def _hook_ops(scope: str) -> list[FileOp]:
    """Two shared-array merges into ``settings.json``. Hook arrays are user
    territory (their own hooks may live under the same event), so these go
    through the list-item merge: install appends exactly one entry per
    event (purging stale manyagent variants via ``_HOOK_MARKER``), uninstall
    removes exactly our entries, neighbors survive.

    The command is wrapped so a dead interpreter path (deleted/moved venv)
    degrades to a silent no-op instead of a visible "hook error" notice at
    the start and end of every one of the user's Claude Code sessions —
    ``manyagent._hook``'s never-fail guarantee can't apply when the python that
    hosts it is gone."""
    settings = _target_root(scope) / "settings.json"
    command = f"{shlex.quote(sys.executable)} {_HOOK_MARKER} 2>/dev/null || true"
    item = {"hooks": [{"type": "command", "command": command}]}
    return [
        FileOp(
            kind="merge",
            path=settings,
            payload={
                "__top_key__": "hooks",
                "__our_key__": event,
                "__list_item__": item,
                "__list_purge__": _HOOK_MARKER,
            },
            description=(
                f"{event} hook — records the harness session id + transcript path to "
                f"$MANYAGENT_HOME/bindings/ for manyagent-wrapped runs (exits silently when MANYAGENT_SESSION is unset)"
            ),
            merge_keys=(f"list:hooks.{event}",),
        )
        for event in _HOOK_EVENTS
    ]


def build_plan(*, session_id: str | None, oma_home: Path | None = None, scope: str = "user") -> InstallPlan:
    """Construct the plan without touching disk (used by consent + dry-run).

    ``oma_home`` is accepted for API symmetry with the other adapters
    (``manyagent.adapters.skills.{codex,gemini}.build_plan``) but not used here —
    Claude's target dirs are agent-owned (``~/.claude/skills/``), not manyagent-owned.
    Gemini stages its bundle under ``$MANYAGENT_HOME/extensions/gemini-manyagent/`` so it
    needs the path; we keep the signature uniform anyway (M11 follow-up P3)."""
    root = _target_root(scope)
    ops: list[FileOp] = []
    for skill in REGISTRY:
        ops.append(
            FileOp(
                kind="create",
                path=root / "skills" / skill.slug / "SKILL.md",
                payload=_skill_body(skill),
                description=f"`/{skill.slug}` skill — {skill.description}",
            )
        )
    ops.extend(_hook_ops(scope))
    return InstallPlan(
        adapter="claude",
        scope=cast("Scope", scope),
        ops=ops,
        cli_actions=_mcp_cli_actions(scope),
        session_id=session_id,
        commands=[(f"/{verb}", blurb) for verb, blurb in USAGE],
    )


def install(
    *,
    session_id: str | None,
    oma_home: Path,
    scope: str = "user",
    dry_run: bool = False,
    input_fn: object = input,  # for tests; matches consent_prompt's input
    output_fn: object = print,
) -> Manifest | None:
    """Install Claude Code skills + MCP server entry. Returns the manifest
    written, or ``None`` if the user declined."""
    plan = build_plan(session_id=session_id, oma_home=oma_home, scope=scope)
    existing = load_manifest("claude", oma_home) is not None
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
