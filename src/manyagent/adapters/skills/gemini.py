"""Gemini CLI extension installer (M11).

Targets per scope:

- ``user``: ``~/.gemini/extensions/manyagent/`` — a single extension bundle
  containing ``gemini-extension.json`` (declares the MCP server), four
  ``commands/<verb>.toml`` slash commands, and a ``GEMINI.md`` context file
  the model reads when the extension is active. The user types
  ``/self-distill`` natively inside Gemini CLI.
- ``project``: ``<cwd>/.gemini/extensions/manyagent/`` (same bundle, scoped to
  the repo the wrapper was launched in).

Gemini extensions have **restricted env passthrough** — extension-bundled MCP
servers only inherit env vars declared in the manifest's ``settings`` array.
We declare ``MANYAGENT_SESSION`` there. The MCP server also falls back to
``~/.manyagent/active`` (set by ``manyagent start``) so the chain works either way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

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

# Gemini CLI uses native `/self-distill` slash commands (defined as TOML under
# `commands/`) backed by the `manyagent` MCP server. Tools are referenced as
# `mcp__manyagent__<tool>`, args arrive as `{{args}}`, and the per-tool permission
# UI on the commit tool is the human gate. The per-verb prose lives once in
# manyagent._skills; this dialect only supplies Gemini's tokens.
_DIALECT = Dialect(
    tool_ref=lambda name: f"mcp__manyagent__{name}",
    invocation=lambda slug: f"/{slug}",
    args="{{args}}",
    gate="Gemini's permission UI",
)


_GEMINI_MD = """\
# manyagent — ManyAgent

This extension lets the user run `manyagent`'s knowledge-curation loop from inside
Gemini CLI. The four slash commands (`/self-distill`, `/discuss`,
`/cross-distill`, `/inject`) are defined in `commands/` and call into the
`manyagent` MCP server registered in `gemini-extension.json`.

## The contract

- The active manyagent session is set by `manyagent start` (writes `~/.manyagent/active`);
  `MANYAGENT_SESSION` env wins if set. If neither is present, the MCP tool errors
  and you should tell the user to run `manyagent start` first.
- For `/self-distill` and `/discuss`: always call the corresponding `*_draft`
  MCP tool, show the structured payload to the user verbatim, ask for accept
  + ★, and **only on accept** call `mcp__manyagent__commit_post`. On reject, do
  NOT call commit_post — the Bank stays untouched (the C1 invariant: no
  persistence without explicit consent).
- For `/inject`: always call `mcp__manyagent__inject_preview` first, show the
  preview, ask for accept, and **only on accept** call `mcp__manyagent__inject_commit`.
  Gemini's per-tool permission UI fires on the commit tool; the user has the
  final word.
- For `/cross-distill`: no human gate; just call the tool and report the
  resulting bundle id + per-bucket counts.

These rules are mechanical, not stylistic — the parser refuses
non-evidence-grounded posts and the per-call permission UI is the audit
trail for every persistence.
"""


def _toml_command(skill: Skill) -> str:
    """One TOML slash command for Gemini: a `description` plus a `prompt` whose
    body is the shared dialect-substituted procedure (draft → show → commit,
    the per-tool permission UI being the human gate)."""
    return f'description = "{skill.description}"\nprompt = """\n{skill.body(_DIALECT)}\n"""\n'


def _manifest_payload() -> dict[str, Any]:
    """``gemini-extension.json`` — declares the MCP server and the formal
    ``MANYAGENT_SESSION`` env-passthrough allowlist (gemini extensions have
    restricted env passthrough; only names declared here are forwarded).
    Restored 2026-05-20 (M11 follow-up P1) after switching from
    ``extensions link`` → ``extensions install --skip-settings`` — ``link``
    has no skip-settings flag, so the M11.3 first pass dropped the
    allowlist to avoid the interactive setup prompt; ``install`` exposes
    ``--skip-settings`` so we can keep both."""
    return {
        "name": "manyagent",
        "version": "0.1.0",
        "description": "ManyAgent — in-agent knowledge curation. Type /self-distill, /discuss, /cross-distill, /inject inside Gemini CLI.",
        "mcpServers": {
            "manyagent": {
                "command": sys.executable,
                "args": ["-m", "manyagent._mcp"],
            }
        },
        "settings": [
            {
                "name": "session",
                "description": "Active manyagent session id (set by `manyagent start`; the MCP server also falls back to ~/.manyagent/active).",
                "envVar": "MANYAGENT_SESSION",
                "sensitive": False,
            }
        ],
    }


def _bundle_root(oma_home: Path, scope: str) -> Path:
    """Source of truth for the extension bundle is **manyagent-owned** (under
    ``$MANYAGENT_HOME``), not the gemini extensions dir directly. ``gemini extensions
    link`` then symlinks it into ``~/.gemini/extensions/manyagent/`` — the
    documented register path, and updates to the source propagate without
    re-install. Project scope uses ``<cwd>/.manyagent/extensions/gemini-manyagent/`` so the
    bundle travels with the repo if the user wants per-project skills."""
    if scope == "user":
        return oma_home / "extensions" / "gemini-manyagent"
    if scope == "project":
        return Path.cwd() / ".manyagent" / "extensions" / "gemini-manyagent"
    raise ValueError(f"unknown scope {scope!r}")


def _gemini_cli_actions(bundle_path: Path) -> list[CLIAction]:
    """Register the bundle via ``gemini extensions install --skip-settings
    --consent`` (M11 follow-up P1: ``install`` exposes ``--skip-settings``
    which ``link`` doesn't, so we can declare the formal ``MANYAGENT_SESSION``
    env-passthrough allowlist in the manifest as defense-in-depth without
    the install hanging on the interactive settings prompt). Tradeoff:
    ``install`` copies the bundle (not a symlink), so it isn't idempotent
    against an existing install — we pre-clear with ``uninstall`` first."""
    uninstall = ("gemini", "extensions", "uninstall", "manyagent")
    return [
        CLIAction(
            install_argv=uninstall,
            uninstall_argv=("true",),  # pre-clear's inverse is a no-op
            description="pre-clear any existing manyagent gemini extension (install isn't idempotent)",
            failure_ok=True,  # "not installed" IS the fresh install
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
            description="register the manyagent extension with Gemini CLI",
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
            description="Extension manifest — declares the `manyagent` MCP server and `MANYAGENT_SESSION` env passthrough.",
        ),
        FileOp(
            kind="create",
            path=root / "GEMINI.md",
            payload=_GEMINI_MD,
            description="Context file the model reads when the extension is active.",
        ),
    ]
    # Pre-trust the manyagent-owned bundle folder so `gemini extensions install`
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
    for skill in REGISTRY:
        ops.append(
            FileOp(
                kind="create",
                path=root / "commands" / f"{skill.slug}.toml",
                payload=_toml_command(skill),
                description=f"`/{skill.slug}` slash command — {skill.description}",
            )
        )
    return InstallPlan(
        adapter="gemini",
        scope=cast("Scope", scope),
        ops=ops,
        cli_actions=_gemini_cli_actions(root),
        session_id=session_id,
        commands=[(f"/{verb}", blurb) for verb, blurb in USAGE],
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
        oma_home=oma_home,
        dry_run=dry_run,
    ):
        return None
    return apply_plan(plan, oma_home=oma_home, dry_run=dry_run)
