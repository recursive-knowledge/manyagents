"""Claude Code skill + MCP installer (M11).

Targets per scope:

- ``user``: ``~/.claude/skills/oms-<verb>/SKILL.md`` (4 files) + MERGE the
  ``mcpServers.oms`` entry into ``~/.claude/settings.json``. The user types
  ``/self-distill`` natively inside Claude Code.
- ``project``: ``<cwd>/.claude/skills/oms-<verb>/SKILL.md`` + MERGE
  ``<cwd>/.mcp.json``. Scoped to the repo the wrapper was launched in.

The MCP server command uses ``sys.executable`` (the python `oms` itself is
running under) so the agent's spawned server matches the install, even
inside a uv-managed venv. ``OMS_SESSION`` is **not** baked into the config
(it changes per session) — the wrapper exports it in the parent env at
PTY-spawn time, and the MCP server inherits it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from oms._installer import (
    CLIAction,
    FileOp,
    InstallPlan,
    Manifest,
    Scope,
    apply_plan,
    consent_prompt,
    load_manifest,
)

# Skill name → slash command. Claude Code unifies skills + commands, so the
# `name:` frontmatter is what the user types after `/`.
_VERBS: tuple[tuple[str, str, str], ...] = (
    (
        "self-distill",
        "Draft and (on accept) commit ONE reflection post to the active oms session.",
        "self_distill_draft",
    ),
    ("discuss", "Draft and (on accept) commit ONE stance reply engaging a prior in-session post.", "discuss_draft"),
    ("cross-distill", "Curate goal-scoped posts (across sessions) into a 6-bucket Insight bundle.", "cross_distill"),
    ("inject", "Preview a curated bundle, confirm, then write an injection-ledger row.", "inject_preview"),
)


def _skill_body(verb: str, draft_tool: str) -> str:
    """The numbered procedure the host LLM follows. The MCP permission gate
    on ``commit_post`` / ``inject_commit`` is the human accept moment —
    nothing persists without that explicit approval."""
    if verb == "self-distill":
        return f"""\
---
name: self-distill
description: Draft and (on accept) commit ONE evidence-grounded reflection post to the active oms session.
disable-model-invocation: true
allowed-tools:
  - mcp__oms__{draft_tool}
---

# /self-distill — emit one reflection post to the active oms session

Follow this procedure exactly:

1. Call `mcp__oms__self_distill_draft` (pass `guidance=$ARGUMENTS` if the user supplied any).
2. Using the returned `instruction_for_host_llm` (the schema and anti-meta rules) and the live conversation context, draft ONE structured payload with these fields:
   - `load_bearing_assumption` — a concrete primitive (backticked identifier, dotted.path, `call()`, --flag)
   - `evidence` — verbatim from the trace/conversation
   - `evidence_ref` — a packet id, or null
   - `proposed_next` — a concrete next action
   - `predicted_outcome` — a falsifiable prediction
   - `confidence` — "low" / "medium" / "high"
3. Show the draft verbatim to the user with a recommended ★ (high=5, medium=3, low=2).
4. Ask: "Accept this post + ★? [y/n, or a rating 1-5]"
5. **Only on accept**, call `mcp__oms__commit_post` with `kind="reflection"`, the structured payload, and the chosen rating. Claude Code's permission prompt will fire — the user must explicitly approve before the post is persisted.
6. On reject, **do NOT call commit_post** — the Bank stays untouched (C1).

The active oms session is auto-detected from `$OMS_SESSION` or `~/.oms/active`; if neither is set the MCP tool errors and you should tell the user to run `oms start` first.
"""
    if verb == "discuss":
        return f"""\
---
name: discuss
description: Draft and (on accept) commit ONE stance reply engaging a prior in-session post.
disable-model-invocation: true
allowed-tools:
  - mcp__oms__{draft_tool}
---

# /discuss [@packet] [stance] — emit one stance reply

`$ARGUMENTS` may contain `@<packet_id>` and/or one of `agree`/`disagree`/`synthesize` (default `synthesize`).

Procedure:

1. Parse `$ARGUMENTS` for a `@<packet_id>` and a stance.
2. Call `mcp__oms__discuss_draft` with `stance=...` and `packet=...` (the @-stripped id, or null).
3. If the tool returns an error ("no related posts"), tell the user to run `/self-distill` first and STOP.
4. Using the returned `instruction_for_host_llm` (which includes the ranked prior posts) and the conversation, draft a reply with the same 5 fields as `/self-distill`, engaging the post named in `reply_to`.
5. Show the draft verbatim to the user. Ask: "Accept this reply? [y/n]"
6. **Only on accept**, call `mcp__oms__commit_post` with `kind="reply"`, the structured payload, `reply_to=<from draft>`, `stance=<from draft>`. The permission prompt will fire.
7. On reject, **do NOT call commit_post** (C1).
"""
    if verb == "cross-distill":
        return f"""\
---
name: cross-distill
description: Curate goal-scoped posts (across sessions) into a 6-bucket Insight bundle.
disable-model-invocation: true
allowed-tools:
  - mcp__oms__{draft_tool}
---

# /cross-distill — curate the active goal's posts into a bundle

Procedure:

1. Call `mcp__oms__cross_distill`. The curator runs in the background (uses `OMS_LLM_*` config or an installed agent CLI).
2. If the tool returns `{{"ok": false, "error": "Run /self-distill first!"}}`, tell the user to run `/self-distill` first and STOP.
3. Otherwise, summarize: the `bundle_id`, `scope`, `goal`, and the per-bucket counts. Tell the user they can `/inject @<bundle_id>` to seed a session with this bundle.

The curator is mechanical and idempotent — re-running over the same posts returns the same bundle, no re-spend.
"""
    if verb == "inject":
        return f"""\
---
name: inject
description: Preview a curated bundle, ask the user to confirm, then write an injection-ledger row.
disable-model-invocation: true
allowed-tools:
  - mcp__oms__{draft_tool}
---

# /inject [@packet] — preview a bundle and (on accept) record an injection

`$ARGUMENTS` may contain `@<packet_id>`. If omitted, the latest non-quarantined distill is used.

Procedure:

1. Call `mcp__oms__inject_preview` with `packet=$ARGUMENTS` (or null).
2. If the tool returns an error (no bundle / quarantined), report it and STOP.
3. Show the preview verbatim to the user.
4. Ask: "Inject this bundle into your session? [y/n]"
5. **Only on accept**, call `mcp__oms__inject_commit` with the same packet id. Claude Code's permission prompt will fire — the user must approve again before the ledger row is written.
6. On reject, **do NOT call inject_commit**.
"""
    raise ValueError(f"unknown verb {verb!r}")


def _target_root(scope: str) -> Path:
    if scope == "user":
        return Path.home() / ".claude"
    if scope == "project":
        return Path.cwd() / ".claude"
    raise ValueError(f"unknown scope {scope!r}")


def _mcp_cli_actions(scope: str) -> list[CLIAction]:
    """Register the oms MCP server via the official ``claude mcp`` CLI — the
    file location is an internal detail of Claude Code (``~/.claude.json``,
    not ``~/.claude/settings.json``), so going through the CLI is the
    documented, restart-safe path. Idempotent: ``remove`` (ignore failure)
    then ``add``."""
    cli_scope = "project" if scope == "project" else "user"
    add = ("claude", "mcp", "add", "--scope", cli_scope, "oms", "--", sys.executable, "-m", "oms._mcp")
    remove = ("claude", "mcp", "remove", "--scope", cli_scope, "oms")
    return [
        # Belt-and-suspenders: an idempotent re-install should overwrite the
        # entry. `claude mcp remove` exits nonzero if it's not there; we
        # swallow that and run `add` anyway.
        CLIAction(
            install_argv=remove,
            uninstall_argv=("true",),
            description=f"pre-clear any existing oms MCP server (--scope {cli_scope})",
        ),
        CLIAction(
            install_argv=add,
            uninstall_argv=remove,
            description=f"register the oms MCP server with Claude Code (--scope {cli_scope})",
        ),
    ]


def build_plan(*, session_id: str | None, oma_home: Path | None = None, scope: str = "user") -> InstallPlan:
    """Construct the plan without touching disk (used by consent + dry-run).

    ``oma_home`` is accepted for API symmetry with the other adapters
    (``oms.adapters.skills.{codex,gemini}.build_plan``) but not used here —
    Claude's target dirs are agent-owned (``~/.claude/skills/``), not oms-owned.
    Gemini stages its bundle under ``$OMS_HOME/extensions/gemini-oms/`` so it
    needs the path; we keep the signature uniform anyway (M11 follow-up P3)."""
    root = _target_root(scope)
    ops: list[FileOp] = []
    for verb, _desc, draft_tool in _VERBS:
        body = _skill_body(verb, draft_tool)
        ops.append(
            FileOp(
                kind="create",
                path=root / "skills" / verb / "SKILL.md",
                payload=body,
                description=(
                    f"`/{verb}` skill — host-LLM procedure (draft → show → ask → commit). "
                    f"The directory name `{verb}` becomes the slash command."
                ),
            )
        )
    return InstallPlan(
        adapter="claude",
        scope=cast("Scope", scope),
        ops=ops,
        cli_actions=_mcp_cli_actions(scope),
        session_id=session_id,
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
    ):
        return None
    return apply_plan(plan, oma_home=oma_home, dry_run=dry_run)
