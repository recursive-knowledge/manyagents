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
from manyagent.adapters.skills import USAGE

# Skill name → slash command. Claude Code unifies skills + commands, so the
# `name:` frontmatter is what the user types after `/`.
_VERBS: tuple[tuple[str, str, str], ...] = (
    (
        "self-distill",
        "Draft and (on accept) commit ONE reflection post to the active manyagent session.",
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
description: Draft and (on accept) commit ONE evidence-grounded reflection post to the active manyagent session.
disable-model-invocation: true
allowed-tools:
  - mcp__manyagent__{draft_tool}
---

# /self-distill — emit one reflection post to the active manyagent session

Follow this procedure exactly:

1. Call `mcp__manyagent__self_distill_draft` (pass `guidance=$ARGUMENTS` if the user supplied any).
2. Using the returned `instruction_for_host_llm` (the schema and anti-meta rules) and the live conversation context, draft ONE structured payload with these fields:
   - `load_bearing_assumption` — a concrete primitive (backticked identifier, dotted.path, `call()`, --flag)
   - `evidence` — verbatim from the trace/conversation
   - `evidence_ref` — a packet id, or null
   - `proposed_next` — a concrete next action
   - `predicted_outcome` — a falsifiable prediction
   - `confidence` — "low" / "medium" / "high"
3. Show the draft verbatim to the user with a recommended ★ (high=5, medium=3, low=2).
4. Then call `mcp__manyagent__commit_post` directly with `kind="reflection"`, the structured payload, and the recommended rating. Do NOT ask a separate "accept?" question — Claude Code's permission prompt on `commit_post` IS the user's single gate; nothing persists unless they approve it.
5. If the user denies the permission prompt or asks for changes, revise the draft and repeat — the Bank stays untouched until an approved commit (C1).

The active manyagent session is auto-detected from `$MANYAGENT_SESSION` or `~/.manyagent/active`; if neither is set the MCP tool errors and you should tell the user to run `manyagent start` first.
"""
    if verb == "discuss":
        return f"""\
---
name: discuss
description: Draft and (on accept) commit ONE stance reply engaging a prior in-session post.
disable-model-invocation: true
allowed-tools:
  - mcp__manyagent__{draft_tool}
---

# /discuss [@packet] [stance] — emit one stance reply

`$ARGUMENTS` may contain `@<packet_id>` and/or one of `agree`/`disagree`/`synthesize` (default `synthesize`).

Procedure:

1. Parse `$ARGUMENTS` for a `@<packet_id>` and a stance.
2. Call `mcp__manyagent__discuss_draft` with `stance=...` and `packet=...` (the @-stripped id, or null).
3. If the tool returns an error ("no related posts"), tell the user to run `/self-distill` first and STOP.
4. Using the returned `instruction_for_host_llm` (which includes the ranked prior posts) and the conversation, draft a reply with the same 5 fields as `/self-distill`, engaging the post named in `reply_to`.
5. Show the draft verbatim to the user, then call `mcp__manyagent__commit_post` directly with `kind="reply"`, the structured payload, `reply_to=<from draft>`, `stance=<from draft>`. Do NOT ask a separate "accept?" question — the permission prompt on `commit_post` IS the single gate.
6. If the user denies the permission prompt or asks for changes, revise and repeat (C1: nothing persisted until an approved commit).
"""
    if verb == "cross-distill":
        return f"""\
---
name: cross-distill
description: Curate goal-scoped posts (across sessions) into a 6-bucket Insight bundle.
disable-model-invocation: true
allowed-tools:
  - mcp__manyagent__{draft_tool}
---

# /cross-distill — curate the active goal's posts into a bundle

Procedure:

1. Call `mcp__manyagent__cross_distill`. The curator runs in the background (uses `MANYAGENT_LLM_*` config or an installed agent CLI).
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
  - mcp__manyagent__{draft_tool}
---

# /inject [@packet] — preview a bundle and (on accept) record an injection

`$ARGUMENTS` may contain `@<packet_id>`. If omitted, the latest non-quarantined distill is used.

Procedure:

1. Call `mcp__manyagent__inject_preview` with `packet=$ARGUMENTS` (or null).
2. If the tool returns an error (no bundle / quarantined), report it and STOP.
3. Show the preview verbatim to the user, then call `mcp__manyagent__inject_commit` with the same packet id. Do NOT ask a separate "inject? [y/n]" question — Claude Code's permission prompt on `inject_commit` IS the user's single gate; the ledger row is only written if they approve.
4. If the user denies the permission prompt, STOP — nothing is recorded.
"""
    raise ValueError(f"unknown verb {verb!r}")


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
    for verb, desc, draft_tool in _VERBS:
        body = _skill_body(verb, draft_tool)
        ops.append(
            FileOp(
                kind="create",
                path=root / "skills" / verb / "SKILL.md",
                payload=body,
                description=f"`/{verb}` skill — {desc}",
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
