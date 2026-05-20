"""Gemini CLI extension installer (M11).

Targets per scope:

- ``user``: ``~/.gemini/extensions/oms/`` — a single extension bundle
  containing ``gemini-extension.json`` (declares the MCP server), four
  ``commands/<verb>.toml`` slash commands, and a ``GEMINI.md`` context file
  the model reads when the extension is active. The user types
  ``/self-distill`` natively inside Gemini CLI.
- ``project``: ``<cwd>/.gemini/extensions/oms/`` (same bundle, scoped to
  the repo the wrapper was launched in).

Gemini extensions have **restricted env passthrough** — extension-bundled MCP
servers only inherit env vars declared in the manifest's ``settings`` array.
We declare ``OMS_SESSION`` there. The MCP server also falls back to
``~/.oms/active`` (set by ``oms start``) so the chain works either way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

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

_VERBS: tuple[tuple[str, str], ...] = (
    ("self-distill", "Draft and (on accept) commit ONE evidence-grounded reflection post to the active oms session."),
    ("discuss", "Draft and (on accept) commit ONE stance reply engaging a prior in-session post."),
    ("cross-distill", "Curate goal-scoped posts (across sessions) into a 6-bucket Insight bundle."),
    ("inject", "Preview a curated bundle, confirm, then write an injection-ledger row."),
)


_GEMINI_MD = """\
# oms — Oh My Swarm

This extension lets the user run `oms`'s knowledge-curation loop from inside
Gemini CLI. The four slash commands (`/self-distill`, `/discuss`,
`/cross-distill`, `/inject`) are defined in `commands/` and call into the
`oms` MCP server registered in `gemini-extension.json`.

## The contract

- The active oms session is set by `oms start` (writes `~/.oms/active`);
  `OMS_SESSION` env wins if set. If neither is present, the MCP tool errors
  and you should tell the user to run `oms start` first.
- For `/self-distill` and `/discuss`: always call the corresponding `*_draft`
  MCP tool, show the structured payload to the user verbatim, ask for accept
  + ★, and **only on accept** call `mcp__oms__commit_post`. On reject, do
  NOT call commit_post — the Bank stays untouched (the C1 invariant: no
  persistence without explicit consent).
- For `/inject`: always call `mcp__oms__inject_preview` first, show the
  preview, ask for accept, and **only on accept** call `mcp__oms__inject_commit`.
  Gemini's per-tool permission UI fires on the commit tool; the user has the
  final word.
- For `/cross-distill`: no human gate; just call the tool and report the
  resulting bundle id + per-bucket counts.

These rules are mechanical, not stylistic — the parser refuses
non-evidence-grounded posts and the per-call permission UI is the audit
trail for every persistence.
"""


def _toml_command(verb: str) -> str:
    """One TOML slash command. The body is a prompt that instructs the host
    model to follow the draft → show → ask → commit procedure."""
    if verb == "self-distill":
        return """\
description = "Draft and (on accept) commit ONE reflection post to the active oms session."
prompt = \"\"\"
Follow this procedure exactly for /self-distill:

1. Call `mcp__oms__self_distill_draft` (pass `guidance={{args}}` if there is text after the slash).
2. Using the returned `instruction_for_host_llm` and the live conversation, draft ONE structured payload with these fields:
   - `load_bearing_assumption` — a concrete primitive (backticked identifier, dotted.path, `call()`, --flag)
   - `evidence` — verbatim from the conversation/trace
   - `evidence_ref` — a packet id, or null
   - `proposed_next` — a concrete next action
   - `predicted_outcome` — a falsifiable prediction
   - `confidence` — "low" | "medium" | "high"
3. Show the draft verbatim to the user with a recommended ★ (high=5, medium=3, low=2).
4. Ask: "Accept this post + ★? [y/n]"
5. **Only on accept**, call `mcp__oms__commit_post(kind='reflection', structured={...}, rating=N)`. Gemini's permission UI will fire — the user must approve before the post is persisted.
6. On reject, do NOT call commit_post (C1).
\"\"\"
"""
    if verb == "discuss":
        return """\
description = "Draft and (on accept) commit ONE stance reply engaging a prior in-session post."
prompt = \"\"\"
`{{args}}` may contain `@<packet_id>` and/or one of `agree`/`disagree`/`synthesize` (default `synthesize`).

Procedure:

1. Parse `{{args}}` for a `@<packet_id>` and a stance.
2. Call `mcp__oms__discuss_draft(stance=..., packet=...)`.
3. If the tool returns an error ("no related posts"), tell the user to run `/self-distill` first and STOP.
4. Using the returned `instruction_for_host_llm` (which includes the ranked prior posts), draft a reply with the same 5 fields as `/self-distill`, engaging the post named in `reply_to`.
5. Show the draft verbatim. Ask: "Accept this reply? [y/n]"
6. **Only on accept**, call `mcp__oms__commit_post(kind='reply', structured={...}, reply_to=..., stance=...)`. The permission UI will fire.
7. On reject, do NOT call commit_post (C1).
\"\"\"
"""
    if verb == "cross-distill":
        return """\
description = "Curate goal-scoped posts (across sessions) into a 6-bucket Insight bundle."
prompt = \"\"\"
Procedure for /cross-distill:

1. Call `mcp__oms__cross_distill`. The curator runs in the background.
2. If the tool returns `{"ok": false, "error": "Run /self-distill first!"}`, tell the user to run `/self-distill` first and STOP.
3. Otherwise, summarize the bundle: `bundle_id`, `scope`, `goal`, and per-bucket counts. Tell the user they can `/inject @<bundle_id>` to seed a session with it.

The curator is mechanical and idempotent — re-running over the same posts returns the same bundle, no re-spend.
\"\"\"
"""
    if verb == "inject":
        return """\
description = "Preview a curated bundle, ask the user to confirm, then write an injection-ledger row."
prompt = \"\"\"
`{{args}}` may contain `@<packet_id>`. If omitted, the latest non-quarantined distill is used.

Procedure:

1. Call `mcp__oms__inject_preview(packet={{args}} or null)`.
2. If the tool returns an error (no bundle / quarantined), report it and STOP.
3. Show the preview verbatim to the user.
4. Ask: "Inject this bundle into your session? [y/n]"
5. **Only on accept**, call `mcp__oms__inject_commit(packet=<id>)`. Gemini's permission UI will fire — the user must approve again before the ledger row is written.
6. On reject, do NOT call inject_commit.
\"\"\"
"""
    raise ValueError(f"unknown verb {verb!r}")


def _manifest_payload() -> dict[str, Any]:
    """``gemini-extension.json`` — declares the MCP server and the formal
    ``OMS_SESSION`` env-passthrough allowlist (gemini extensions have
    restricted env passthrough; only names declared here are forwarded).
    Restored 2026-05-20 (M11 follow-up P1) after switching from
    ``extensions link`` → ``extensions install --skip-settings`` — ``link``
    has no skip-settings flag, so the M11.3 first pass dropped the
    allowlist to avoid the interactive setup prompt; ``install`` exposes
    ``--skip-settings`` so we can keep both."""
    return {
        "name": "oms",
        "version": "0.1.0",
        "description": "Oh My Swarm — in-agent knowledge curation. Type /self-distill, /discuss, /cross-distill, /inject inside Gemini CLI.",
        "mcpServers": {
            "oms": {
                "command": sys.executable,
                "args": ["-m", "oms._mcp"],
            }
        },
        "settings": [
            {
                "name": "session",
                "description": "Active oms session id (set by `oms start`; the MCP server also falls back to ~/.oms/active).",
                "envVar": "OMS_SESSION",
                "sensitive": False,
            }
        ],
    }


def _bundle_root(oma_home: Path, scope: str) -> Path:
    """Source of truth for the extension bundle is **oms-owned** (under
    ``$OMS_HOME``), not the gemini extensions dir directly. ``gemini extensions
    link`` then symlinks it into ``~/.gemini/extensions/oms/`` — the
    documented register path, and updates to the source propagate without
    re-install. Project scope uses ``<cwd>/.oms/extensions/gemini-oms/`` so the
    bundle travels with the repo if the user wants per-project skills."""
    if scope == "user":
        return oma_home / "extensions" / "gemini-oms"
    if scope == "project":
        return Path.cwd() / ".oms" / "extensions" / "gemini-oms"
    raise ValueError(f"unknown scope {scope!r}")


def _gemini_cli_actions(bundle_path: Path) -> list[CLIAction]:
    """Register the bundle via ``gemini extensions install --skip-settings
    --consent`` (M11 follow-up P1: ``install`` exposes ``--skip-settings``
    which ``link`` doesn't, so we can declare the formal ``OMS_SESSION``
    env-passthrough allowlist in the manifest as defense-in-depth without
    the install hanging on the interactive settings prompt). Tradeoff:
    ``install`` copies the bundle (not a symlink), so it isn't idempotent
    against an existing install — we pre-clear with ``uninstall`` first."""
    uninstall = ("gemini", "extensions", "uninstall", "oms")
    return [
        CLIAction(
            install_argv=uninstall,
            uninstall_argv=("true",),  # pre-clear's inverse is a no-op
            description="pre-clear any existing oms gemini extension (install isn't idempotent)",
        ),
        CLIAction(
            # Gemini fires THREE interactive prompts on extension install:
            # (1) the security-warning consent → ``--consent`` skips it;
            # (2) the settings configuration prompt → ``--skip-settings``
            #     skips it (declared envVars stay in the manifest as the
            #     formal env-passthrough allowlist, with no value pre-set);
            # (3) the workspace-trust check — unlike ``link``, ``install``
            #     ABORTS on untrusted folder before any interactive prompt,
            #     so stdin can't answer it. We pre-trust the bundle path
            #     via a FileOp MERGE on ``~/.gemini/trustedFolders.json``
            #     (declared in the plan so the consent prompt shows the
            #     user we'll touch that file). ``"1\n"`` covers any
            #     residual interactive prompt as belt-and-suspenders.
            install_argv=(
                "gemini",
                "extensions",
                "install",
                str(bundle_path),
                "--consent",
                "--skip-settings",
            ),
            uninstall_argv=uninstall,
            description="register the oms extension with Gemini CLI",
            stdin_input="1\n",
        ),
    ]


def build_plan(*, session_id: str | None, oma_home: Path, scope: str = "user") -> InstallPlan:
    root = _bundle_root(oma_home, scope)
    ops: list[FileOp] = [
        FileOp(
            kind="create",
            path=root / "gemini-extension.json",
            payload=json.dumps(_manifest_payload(), indent=2) + "\n",
            description="Extension manifest — declares the `oms` MCP server and `OMS_SESSION` env passthrough.",
        ),
        FileOp(
            kind="create",
            path=root / "GEMINI.md",
            payload=_GEMINI_MD,
            description="Context file the model reads when the extension is active.",
        ),
    ]
    # Pre-trust the oms-owned bundle folder so `gemini extensions install`
    # doesn't abort with "Folder is not trusted". The shape of
    # ``~/.gemini/trustedFolders.json`` is flat: ``{"/abs/path": "TRUST_FOLDER"}``.
    # This is announced in the install plan + tracked in the manifest under
    # ``merge_keys=("flat:<bundle_path>",)`` so uninstall pops exactly our
    # entry without touching the user's other trusted folders.
    trust_path = Path.home() / ".gemini" / "trustedFolders.json"
    ops.append(
        FileOp(
            kind="merge",
            path=trust_path,
            payload={"__flat_key__": str(root), "__value__": "TRUST_FOLDER"},
            description="Pre-trust the bundle folder so `gemini extensions install` doesn't abort (preserves your other trusted folders).",
            merge_keys=(f"flat:{root}",),
        )
    )
    for verb, _desc in _VERBS:
        ops.append(
            FileOp(
                kind="create",
                path=root / "commands" / f"{verb}.toml",
                payload=_toml_command(verb),
                description=f"`/{verb}` slash command — host-LLM procedure (draft → show → ask → commit).",
            )
        )
    return InstallPlan(
        adapter="gemini",
        scope=cast("Scope", scope),
        ops=ops,
        cli_actions=_gemini_cli_actions(root),
        session_id=session_id,
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
    existing = load_manifest("gemini", oma_home) is not None
    if not consent_prompt(
        plan,
        input_fn=input_fn,  # type: ignore[arg-type]
        output_fn=output_fn,  # type: ignore[arg-type]
        manifest_exists=existing,
    ):
        return None
    return apply_plan(plan, oma_home=oma_home, dry_run=dry_run)
