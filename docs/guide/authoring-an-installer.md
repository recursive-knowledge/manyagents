# Authoring an in-agent installer

A guide for adding a fifth adapter (or porting an existing one to a new host
agent's CLI). Read after [Quickstart](quickstart.md). Reference: `manyagent._installer`,
`manyagent.adapters.skills.{claude,codex,gemini}`, `tests/test_adapter_install.py`.

## The seam

`manyagent <name>` calls `adapter.install_skills(*, session_id, oma_home, scope,
dry_run)` before spawning the PTY. The default ABC implementation
(`manyagent.adapters.base.Adapter.install_skills`) returns `None` (no-op), so
adapters that don't expose an in-agent surface (e.g. the `qwen` stub) are
silently skipped — `_do_run_agent` continues regardless.

To add a real installer, override the method on your `Adapter` subclass:

```python
# src/manyagent/adapters/builtin/<name>.py
class MyAdapter(_StructuredBuiltin):
    name = "myname"
    binary = "myname"

    def install_skills(self, *, session_id, oma_home, scope="user", dry_run=False):
        from manyagent.adapters.skills.myname import install
        from pathlib import Path
        return install(
            session_id=session_id,
            oma_home=Path(str(oma_home)),
            scope=scope,
            dry_run=dry_run,
        )
```

Then write `src/manyagent/adapters/skills/myname.py` (the actual installer
module). Convention: it exports `build_plan(*, session_id, oma_home,
scope) -> InstallPlan` (pure, no I/O — used by `--dry-run` and the consent
preview) and `install(...) -> Manifest | None` (the active path: builds the
plan, runs the consent gate, applies atomically, saves the manifest).

## The contract

The plan is heterogeneous: zero or more `FileOp` (CREATE a new file, or
MERGE our keys into an existing one) plus zero or more `CLIAction` (run an
external command to register/unregister with the host agent). Both are
logged into a per-adapter manifest at `$MANYAGENT_HOME/installed/<name>.json`;
`manyagent status` lists them; `manyagent uninstall <name>` reverses both layers (CLI
unregisters first, then files).

```python
from manyagent._installer import CLIAction, FileOp, InstallPlan, apply_plan, consent_prompt, load_manifest

def build_plan(*, session_id, oma_home, scope="user"):
    return InstallPlan(
        adapter="myname",
        scope=scope,
        ops=[
            FileOp(
                kind="create",
                path=Path.home() / ".myagent" / "skills" / "self-distill" / "SKILL.md",
                payload=_skill_body("self-distill"),
                description="`/self-distill` skill — host-LLM procedure (draft → show → ask → commit).",
            ),
            # … 3 more skills …
        ],
        cli_actions=[
            CLIAction(
                install_argv=("myagent", "mcp", "add", "manyagent", "--", sys.executable, "-m", "manyagent._mcp"),
                uninstall_argv=("myagent", "mcp", "remove", "manyagent"),
                description="register the manyagent MCP server with MyAgent",
            ),
        ],
        session_id=session_id,
    )
```

## When to use FileOp vs CLIAction

**Use a CLIAction** when the host agent exposes an official register CLI
(`claude mcp add`, `gemini extensions link`). The CLI usually writes to a
file you don't control (`~/.claude.json`, `~/.gemini/extensions/manyagent`
symlink) — going through the CLI keeps the file format their problem, not
yours.

**Use a FileOp** when:
- you're creating a file the host agent loads on its own (a skill /
  command file in the well-known `~/.<agent>/skills/` location);
- the agent's CLI doesn't expose a knob you need (Codex's `mcp add` has no
  `env_vars` or per-tool `approval_mode` flag, so we merge those into
  `~/.codex/config.toml` ourselves via `tomlkit`);
- you want sha256-tracked rollback (the manifest records sha256-at-write
  for every CREATE so `manyagent uninstall` skips user-edited files).

## The three working examples

| Adapter | Skills files | MCP/register surface | Slash invocation |
|---|---|---|---|
| `claude` | `~/.claude/skills/<verb>/SKILL.md` (CREATE × 4) | `claude mcp add --scope user manyagent -- <python> -m manyagent._mcp` (CLIAction, with `remove` pre-clear for idempotency) | `/<verb>` |
| `codex` | `~/.codex/skills/manyagent-<verb>/SKILL.md` (CREATE × 4) | `tomlkit`-preserving MERGE of `~/.codex/config.toml`: `[mcp_servers.manyagent]` + `[mcp_servers.manyagent.tools.commit_post]` + `[mcp_servers.manyagent.tools.inject_commit]` — Codex's `mcp add` doesn't expose `env_vars` or per-tool approval-modes, so direct TOML merge gives full control | `$manyagent-<verb>` (Codex reserves `/` for built-ins) |
| `gemini` | bundle at `$MANYAGENT_HOME/extensions/gemini-manyagent/` (CREATE × 6 — `gemini-extension.json` + `GEMINI.md` + 4 `commands/<verb>.toml`) | `gemini extensions link <bundle> --consent` with `"1\n"` piped to stdin (CLIAction) — the workspace-trust prompt has no skip flag that coexists with `--consent` | `/<verb>` |

Cross-cutting facts:

- The MCP server executable is **always** `(sys.executable, "-m",
  "manyagent._mcp")` — `sys.executable` at install time is whatever python `manyagent`
  itself is running under, so the host agent's spawned MCP child uses the
  same venv `manyagent` is installed into.
- The host agent inherits `MANYAGENT_SESSION` (exported by `_do_run_agent`
  before the PTY spawn) when it spawns the MCP child. The MCP server
  falls back to `~/.manyagent/active` if the env isn't set, so opening the
  agent later in any directory still works.

## Idempotency + atomicity (the invariants tests assert)

1. **Twice == once, byte-identical.** Run `install(...)` twice; the second
   run must produce a filesystem indistinguishable from the first (same
   bytes, same manifest, same sha256s). The `apply_plan` machinery handles
   this automatically as long as your `_skill_body` is deterministic.
2. **Third-party content survives round-trip.** If the user already has
   *other* MCP servers in `~/.codex/config.toml` (or `~/.claude.json`), an
   `install → uninstall` cycle must leave those byte-identical. The merge
   primitives (`merge_json_keys`, `merge_toml_section`) preserve siblings;
   `unmerge_*` pop only our keys.
3. **User-edited CREATE files are kept.** If the user manually edits a
   `SKILL.md` after install, sha256 won't match what we recorded at write
   time — `uninstall` prints `KEPT` and leaves the file alone.
4. **Consent prints every absolute path.** Before any write, the user sees
   the full plan and types `[y/n/d]`. `MANYAGENT_INSTALL_SKILLS=auto` overrides
   to silent yes; `=deny` to silent no; default is "prompt once, then
   silent re-installs."
5. **Atomic writes.** Every CREATE / MERGE goes through `_atomic_write`
   (tempfile + `os.replace`) — no partial-state crash window.

## The real prompt landmines

The M11.3 cycle burned three iterations on these; capture them so the next
author doesn't re-burn:

- **Some agent CLIs are interactive even with the documented "non-
  interactive" flag.** Gemini's `extensions link --consent` skips ONE prompt
  (security warning) but workspace trust is a SEPARATE prompt with no
  skip-flag that yargs accepts in the same invocation. The escape:
  `CLIAction(..., stdin_input="1\n")` — `_run_cli` pipes that to the
  child's stdin. Verify against the *specific* CLI version you target.
- **The skill directory name IS the slash command.** Claude Code:
  `~/.claude/skills/<dirname>/SKILL.md` → `/<dirname>`. The YAML `name:`
  field is only for display in `/skills` and logs. We learned this when
  the M11.2 first pass shipped `manyagent-self-distill/` and the user saw
  `/manyagent-self-distill` instead of `/self-distill`.
- **Pick the host agent's documented register CLI, not the file it
  writes to.** The M11.2 first pass wrote `~/.claude/settings.json`
  `mcpServers.manyagent` (per stale research) — `claude mcp list` reported it
  as not connected because Claude Code actually reads MCP servers from
  `~/.claude.json`. Going through `claude mcp add` made the file location
  Claude's problem, not ours.
- **Uninstall order matters.** Run the agent's unregister CLI FIRST, then
  remove your files. Reversing leaves the agent with a dangling pointer
  (Gemini's `extensions uninstall manyagent` refuses on a broken symlink).
  `_installer.uninstall` does the right order automatically; your
  `CLIAction.uninstall_argv` is what it invokes.
- **Smoke against the user's real `~/.<agent>/` before declaring done.**
  Local FakeHome smoke can prove the file writes happen and the manifest
  records them; only running against the real agent (and inspecting
  `<agent> mcp list` / `extensions list`) can prove the registration
  ACTUALLY connects. Both M11.2 and M11.3 only found their bugs at this
  step. Build it into your checklist.

## The skill body

Every skill is a markdown file with YAML frontmatter:

```markdown
---
name: <verb>
description: <one-line, used for /skills menu + auto-trigger>
disable-model-invocation: true        # user invokes via slash, not the model autonomously
allowed-tools:
  - mcp__manyagent__<draft_tool>             # auto-approve the read-only draft
  # commit_post / inject_commit are deliberately OMITTED — let the per-tool
  # permission prompt fire on commit (that IS the human accept gate)
---

# /<verb> — <one-line purpose>

Numbered procedure (follow exactly — soft contract, easy to drift):

1. Call `mcp__manyagent__<draft_tool>` (pass guidance from $ARGUMENTS).
2. Draft the structured payload from the conversation context.
3. Show it verbatim to the user with a recommended ★.
4. Ask "Accept this post + ★? [y/n]".
5. **Only on accept**, call `mcp__manyagent__commit_post(...)`. The agent's
   permission prompt fires — the user must approve before persistence.
6. On reject, do NOT call commit_post (C1: no persistence without consent).
```

The agent skill is a **soft contract** — you instruct the model in
prose. It usually follows it, but tighten the procedure if a real-terminal
smoke shows drift. (V2 of M11 verified Claude Code follows it faithfully
end-to-end: drafts, checks anti-meta, refuses to fabricate when there's no
substantive content to ground against.)

## Tests to copy

`tests/test_adapter_install.py` has the template parametrized by adapter.
For a fifth adapter, copy the Claude block and adapt:

```python
@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "auto")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path

@pytest.fixture
def captured_cli(monkeypatch):
    invocations = []
    monkeypatch.setattr(manyagent._installer.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        manyagent._installer, "_run_cli",
        lambda argv, *, description, stdin_input=None: invocations.append(list(argv)),
    )
    return invocations

def test_myname_plan_creates_<expected_files>_and_cli_actions(fake_home, captured_cli):
    plan = build_plan(session_id="S1", oma_home=fake_home / ".manyagent", scope="user")
    # assert FileOp counts + paths
    # assert CLIAction install/uninstall argv shapes

def test_myname_install_idempotent_twice_equals_once(fake_home, captured_cli):
    install(...); once = (...).read_bytes()
    install(...); assert (...).read_bytes() == once
```

The `captured_cli` fixture stubs `shutil.which` (so the installer thinks
the binary is on PATH) and `_run_cli` (so tests don't actually shell out).
This is critical — without it, tests would modify the developer's real
agent state.

## Doc-sync

When you land a fifth adapter:

1. Add a dated entry to **both** `manyagent.adapters.md` copies (source-of-truth
   `docs/design/components/manyagent.adapters.md` AND the repo copy
   `docs/design/components/manyagent.adapters.md`), byte-identical.
2. Update the README transparency table to add a row for the new adapter
   (every absolute path, CREATE-vs-MERGE, reversal).
3. Update this guide if you found a new landmine worth documenting.
