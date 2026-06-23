"""M11 per-adapter installer tests.

Validates that ``manyagent.adapters.skills.<name>.install`` (and the
``Adapter.install_skills`` method that wraps it) writes exactly the files
the agent expects, idempotently, and registers/unregisters the MCP server
via the agent's *official* CLI (``claude mcp add`` / ``claude mcp remove``)
— the user-scope MCP file is `~/.claude.json`, not the YAML/JSON we were
writing in M11.2's first pass, so we delegate to the CLI rather than
file-poke.

The ``claude`` binary is stubbed out (we capture invocations) so the suite
never depends on it being installed and never modifies the test machine's
real Claude Code state.

Codex + Gemini installers land in M11.3.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from manyagent._installer import load_manifest, uninstall
from manyagent.adapters.builtin.claude import ClaudeAdapter
from manyagent.adapters.skills.claude import build_plan, install


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` to a tmp dir so the user's real ~/.claude is
    NEVER touched. Sets ``MANYAGENT_INSTALL_SKILLS=auto`` (silent consent) and
    monkeypatches the ``claude`` binary lookup so the installer thinks it's
    present and we can record what would have been invoked."""
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "auto")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))  # type: ignore[arg-type]
    return tmp_path


@pytest.fixture
def captured_cli(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture every external CLI invocation the installer would run. Pretend
    every binary is on PATH so the install-flow runs end-to-end without
    actually shelling out."""
    invocations: list[list[str]] = []

    def _which(name: str) -> str:
        return f"/usr/bin/{name}"

    def _run(argv: list[str], **_kwargs: Any) -> None:
        invocations.append(list(argv))

    import manyagent._installer as inst_mod

    monkeypatch.setattr(inst_mod.shutil, "which", _which)
    monkeypatch.setattr(
        inst_mod,
        "_run_cli",
        lambda argv, *, description, stdin_input=None, failure_ok=False: _run(argv),
    )
    return invocations


# --------------------------------------------------------------------------- #
# plan shape: 4 SKILL.md files (bare-verb dirs!) + 2 CLIActions (remove+add)
# --------------------------------------------------------------------------- #


def test_plan_creates_four_bare_verb_skill_dirs(fake_home: Path) -> None:
    """The directory name *is* the slash command in Claude Code (NOT the YAML
    ``name:`` field). Bare-verb dirs → ``/self-distill`` not
    ``/manyagent-self-distill`` (M11.2 hotfix)."""
    plan = build_plan(session_id="S1", scope="user")
    assert plan.adapter == "claude" and plan.scope == "user"
    creates = [op for op in plan.ops if op.kind == "create"]
    assert len(creates) == 4
    assert {Path(op.path).name for op in creates} == {"SKILL.md"}
    skill_dirs = {Path(op.path).parent.name for op in creates}
    assert skill_dirs == {"self-distill", "discuss", "cross-distill", "inject"}


def test_plan_declares_advisory_commands(fake_home: Path) -> None:
    """The consent prompt leads with what the user GETS — the plan carries
    the four slash invocations + usage blurbs (`InstallPlan.commands`); the
    file ops stay behind the [d]etails keypress."""
    plan = build_plan(session_id="S1", scope="user")
    assert [inv for inv, _ in plan.commands] == ["/self-distill", "/discuss", "/cross-distill", "/inject"]
    assert all(blurb for _, blurb in plan.commands)


def test_plan_merges_lifecycle_hooks_into_settings_json(fake_home: Path) -> None:
    """M12 groundwork: exactly two shared-array merges into
    ``~/.claude/settings.json`` — SessionStart + SessionEnd hooks running
    ``python -m manyagent._hook``. List-item merges (``list:`` keys), NOT key
    upserts: the user's own hooks under the same events must survive.
    settings.json is the documented home for hooks; MCP registration stays
    on the ``claude mcp`` CLI (``~/.claude.json`` — the M11.2 lesson)."""
    plan = build_plan(session_id="S1", scope="user")
    merges = [op for op in plan.ops if op.kind == "merge"]
    assert len(merges) == 2
    assert {str(op.path) for op in merges} == {str(fake_home / ".claude" / "settings.json")}
    events = set()
    for op in merges:
        assert isinstance(op.payload, dict)
        assert op.payload["__top_key__"] == "hooks"
        events.add(op.payload["__our_key__"])
        item = op.payload["__list_item__"]
        command = item["hooks"][0]["command"]
        assert "-m manyagent._hook" in command and sys.executable.rsplit("/", 1)[-1] in command
        assert op.merge_keys == (f"list:hooks.{op.payload['__our_key__']}",)
    assert events == {"SessionStart", "SessionEnd"}


def test_plan_includes_claude_mcp_add_cli_actions(fake_home: Path) -> None:
    """MCP registration goes through ``claude mcp add --scope user`` (the
    documented, restart-safe path; the user-scope file is ``~/.claude.json``
    with a non-trivial shape that we don't manage directly)."""
    plan = build_plan(session_id="S1", scope="user")
    assert len(plan.cli_actions) == 2  # idempotent pre-clear + add
    pre_clear, add = plan.cli_actions
    assert pre_clear.install_argv[:5] == ("claude", "mcp", "remove", "--scope", "user")
    assert add.install_argv[:5] == ("claude", "mcp", "add", "--scope", "user")
    assert add.install_argv[5] == "manyagent" and add.install_argv[6] == "--"
    assert sys.executable in add.install_argv and "-m" in add.install_argv
    assert "manyagent._mcp" in add.install_argv
    # The inverse is `claude mcp remove` so uninstall can reverse it.
    assert add.uninstall_argv[:5] == ("claude", "mcp", "remove", "--scope", "user")
    assert add.uninstall_argv[5] == "manyagent"


def test_plan_project_scope_writes_to_cwd_with_project_scope(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    plan = build_plan(session_id="S1", scope="project")
    # Creates point at the project's .claude/skills/<verb>/SKILL.md; the hook
    # merges at the project's .claude/settings.json.
    for op in plan.ops:
        if op.kind == "create":
            assert str(op.path).startswith(str(project / ".claude" / "skills"))
        else:
            assert op.path == project / ".claude" / "settings.json"
    # CLI scope flips to project too.
    add = next(a for a in plan.cli_actions if a.install_argv[2] == "add")
    assert add.install_argv[3:5] == ("--scope", "project")


# --------------------------------------------------------------------------- #
# install: writes the files, runs the CLI registration, records the manifest
# --------------------------------------------------------------------------- #


def test_install_writes_skills_and_hooks_but_never_mcp_into_settings_json(
    fake_home: Path, captured_cli: list[list[str]]
) -> None:
    """settings.json gains ONLY our two lifecycle hook entries (M12
    groundwork) — never an ``mcpServers`` key: the user-scope MCP file is
    ``~/.claude.json``, managed by Claude Code itself via its CLI (the
    M11.2-first-pass bug was writing MCP config into settings.json)."""
    oma_home = fake_home / ".manyagent"
    m = install(session_id="S1", oma_home=oma_home, scope="user")
    assert m is not None
    skills_root = fake_home / ".claude" / "skills"
    for verb in ("self-distill", "discuss", "cross-distill", "inject"):
        skill = skills_root / verb / "SKILL.md"
        assert skill.is_file(), f"missing {skill}"
        body = skill.read_text()
        assert f"name: {verb}" in body
        assert "mcp__manyagent__" in body
    import json as _json

    settings = _json.loads((fake_home / ".claude" / "settings.json").read_text())
    assert set(settings) == {"hooks"}  # hooks only — no mcpServers, ever
    assert set(settings["hooks"]) == {"SessionStart", "SessionEnd"}
    for event in ("SessionStart", "SessionEnd"):
        (entry,) = settings["hooks"][event]
        assert "-m manyagent._hook" in entry["hooks"][0]["command"]


def test_install_purges_stale_manyagent_hook_from_previous_venv(fake_home: Path, captured_cli: list[list[str]]) -> None:
    """A reinstall from a different interpreter (moved/recreated venv) must
    replace the old manyagent hook entry, not accumulate a second one that fires —
    and possibly errors — on every one of the user's sessions."""
    import json as _json

    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    stale = {"hooks": [{"type": "command", "command": "/dead/venv/bin/python -m manyagent._hook"}]}
    user = {"hooks": [{"type": "command", "command": "echo mine"}]}
    settings_path.write_text(_json.dumps({"hooks": {"SessionStart": [user, stale], "SessionEnd": [stale]}}, indent=2))
    install(session_id="S1", oma_home=fake_home / ".manyagent", scope="user")
    data = _json.loads(settings_path.read_text())
    for event in ("SessionStart", "SessionEnd"):
        cmds = [h["hooks"][0]["command"] for h in data["hooks"][event]]
        assert not any("/dead/venv" in c for c in cmds), event
        assert sum("-m manyagent._hook" in c for c in cmds) == 1, event
    assert data["hooks"]["SessionStart"][0] == user  # user's hook untouched


def test_install_hooks_preserve_user_hooks_through_round_trip(fake_home: Path, captured_cli: list[list[str]]) -> None:
    """A user hook already under SessionStart survives install AND
    uninstall; uninstall leaves settings.json byte-identical."""
    import json as _json

    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = (
        _json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}},
            indent=2,
        )
        + "\n"
    )
    settings_path.write_text(original)

    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user")
    merged = _json.loads(settings_path.read_text())
    commands = [h["hooks"][0]["command"] for h in merged["hooks"]["SessionStart"]]
    assert commands[0] == "echo mine"  # user's hook untouched, first
    assert any("-m manyagent._hook" in c for c in commands[1:])

    rc = uninstall("claude", oma_home, output_fn=lambda _s: None)
    assert rc == 0
    assert settings_path.read_text() == original  # byte-identical round trip


def test_install_invokes_claude_mcp_add_with_right_argv(fake_home: Path, captured_cli: list[list[str]]) -> None:
    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user")
    # Two CLI invocations: pre-clear remove, then add.
    assert len(captured_cli) == 2
    pre_clear, add = captured_cli
    assert pre_clear[:5] == ["claude", "mcp", "remove", "--scope", "user"]
    assert add[:7] == ["claude", "mcp", "add", "--scope", "user", "manyagent", "--"]
    assert sys.executable in add and "manyagent._mcp" in add


def test_install_idempotent_twice_equals_once(fake_home: Path, captured_cli: list[list[str]]) -> None:
    """The advisor's invariant: re-running install is byte-identical for the
    skill files. The CLI actions re-run too (claude mcp remove + add is
    idempotent by design)."""
    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user")
    skill = fake_home / ".claude" / "skills" / "self-distill" / "SKILL.md"
    settings = fake_home / ".claude" / "settings.json"
    once = skill.read_bytes()
    settings_once = settings.read_bytes()

    install(session_id="S1", oma_home=oma_home, scope="user")
    assert skill.read_bytes() == once  # files byte-identical
    assert settings.read_bytes() == settings_once  # no duplicate hook entries
    assert len(captured_cli) == 4  # two CLI actions per install * 2 runs


def test_install_declined_writes_nothing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, captured_cli: list[list[str]]
) -> None:
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "deny")
    oma_home = fake_home / ".manyagent"
    m = install(session_id="S1", oma_home=oma_home, scope="user", output_fn=lambda _s: None)
    assert m is None
    assert not (fake_home / ".claude" / "skills").exists()
    assert not (fake_home / ".claude" / "settings.json").exists()  # no hook merge either
    assert captured_cli == []  # no CLI invocation either
    assert load_manifest("claude", oma_home) is None


def test_install_dry_run_writes_nothing_runs_no_cli(fake_home: Path, captured_cli: list[list[str]]) -> None:
    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user", dry_run=True)
    assert not (fake_home / ".claude" / "skills").exists()
    assert captured_cli == []


# --------------------------------------------------------------------------- #
# uninstall: removes files, runs the inverse CLI, pops only our hook entries
# --------------------------------------------------------------------------- #


def test_uninstall_removes_skills_and_runs_claude_mcp_remove(fake_home: Path, captured_cli: list[list[str]]) -> None:
    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user")
    skills_root = fake_home / ".claude" / "skills"

    captured_cli.clear()  # forget the install-time invocations
    out_lines: list[str] = []
    rc = uninstall("claude", oma_home, output_fn=out_lines.append)
    assert rc == 0

    # Files removed.
    for verb in ("self-distill", "discuss", "cross-distill", "inject"):
        assert not (skills_root / verb / "SKILL.md").exists()
    # settings.json held only our hook entries, so it's gone too.
    assert not (fake_home / ".claude" / "settings.json").exists()

    # The inverse CLI ran. The pre-clear remove's inverse is `true` (no-op),
    # so only the real `add`'s inverse — `claude mcp remove` — should appear.
    real_invocations = [argv for argv in captured_cli if argv[0] != "true"]
    assert len(real_invocations) >= 1
    assert any(
        argv[:5] == ["claude", "mcp", "remove", "--scope", "user"] and argv[5] == "manyagent"
        for argv in real_invocations
    ), f"expected claude mcp remove in {real_invocations}"


def test_uninstall_when_claude_binary_absent_records_skip(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If ``claude`` isn't on PATH at uninstall time, we record a SKIPPED
    line rather than crashing — the user can run ``claude mcp remove manyagent``
    manually."""
    import manyagent._installer as inst_mod

    # Install with the binary present.
    def _which_present(_name: str) -> str:
        return "/usr/bin/claude"

    monkeypatch.setattr(inst_mod.shutil, "which", _which_present)
    monkeypatch.setattr(inst_mod, "_run_cli", lambda *_a, **_k: None)

    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user")

    # Now pretend claude is gone and try to uninstall.
    monkeypatch.setattr(inst_mod.shutil, "which", lambda _name: None)
    out: list[str] = []
    rc = uninstall("claude", oma_home, output_fn=out.append)
    assert rc == 0  # files still removed cleanly
    assert any("SKIPPED" in line and "claude" in line for line in out)


# --------------------------------------------------------------------------- #
# Adapter.install_skills wires through the per-adapter installer
# --------------------------------------------------------------------------- #


def test_claude_adapter_install_skills_delegates(fake_home: Path, captured_cli: list[list[str]]) -> None:
    adapter = ClaudeAdapter(session_id="S1", agent_id="S1/agent-001-claude")
    oma_home = fake_home / ".manyagent"
    m = adapter.install_skills(session_id="S1", oma_home=oma_home, scope="user")
    assert m is not None  # consent auto-yes via fake_home fixture
    assert (fake_home / ".claude" / "skills" / "self-distill" / "SKILL.md").is_file()
    assert captured_cli  # claude mcp add was invoked


def test_default_adapter_install_skills_is_no_op() -> None:
    from manyagent.adapters.base import Adapter

    class _MinimalAdapter(Adapter):
        name = "minimal"
        binary = ""

        def invoke(self, args: list[str]) -> Any:
            raise NotImplementedError

        def capture(self) -> Any:
            raise NotImplementedError

        def inject(self, context: str) -> None: ...
        def retrieve(self) -> str | None:
            return None

    import tempfile

    assert (
        _MinimalAdapter().install_skills(
            session_id="S1",
            oma_home=Path(tempfile.gettempdir()),
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Gemini installer — extension bundle staged in $MANYAGENT_HOME, registered via CLI
# --------------------------------------------------------------------------- #


def test_gemini_plan_stages_bundle_under_manyagent_home_with_link_cli(
    fake_home: Path, captured_cli: list[list[str]]
) -> None:
    """Lesson from M11.2: don't file-poke ``~/.gemini/extensions/manyagent/``
    directly. Source-of-truth bundle lives under ``$MANYAGENT_HOME/extensions/
    gemini-manyagent/``; ``gemini extensions install --skip-settings --consent`` registers it (M11 P1: switched from `link` so the formal env-passthrough allowlist survives — `link` had no settings-skip flag) (was symlink under `link`, now a copy under `install`; pip
    upgrade auto-propagates the bundle, no re-install)."""
    from manyagent.adapters.skills.gemini import build_plan

    oma_home = fake_home / ".manyagent"
    plan = build_plan(session_id="S1", oma_home=oma_home, scope="user")
    bundle_root = oma_home / "extensions" / "gemini-manyagent"
    # 6 CREATE ops write to the manyagent-owned staging dir; 1 MERGE op pre-trusts
    # that dir in ~/.gemini/trustedFolders.json (M11 P1: required so
    # `gemini extensions install` doesn't abort on the trust check).
    creates = [op for op in plan.ops if op.kind == "create"]
    merges = [op for op in plan.ops if op.kind == "merge"]
    for op in creates:
        assert str(op.path).startswith(str(bundle_root)), op.path
    paths = {Path(op.path).relative_to(bundle_root).as_posix() for op in creates}
    assert paths == {
        "gemini-extension.json",
        "GEMINI.md",
        "commands/self-distill.toml",
        "commands/discuss.toml",
        "commands/cross-distill.toml",
        "commands/inject.toml",
    }
    [trust] = merges
    assert trust.path.name == "trustedFolders.json"
    assert trust.merge_keys == (f"flat:{bundle_root}",)
    assert trust.payload == {"__flat_key__": str(bundle_root), "__value__": "TRUST_FOLDER"}
    # CLI actions: pre-clear uninstall + `gemini extensions install` with
    # both --consent and --skip-settings (M11 follow-up P1: switched from
    # `link` to `install` so the formal settings allowlist survives, but
    # `install` isn't idempotent like `link` was — the pre-clear is back).
    pre_clear, register = plan.cli_actions
    assert pre_clear.install_argv == ("gemini", "extensions", "uninstall", "manyagent")
    assert register.install_argv[:3] == ("gemini", "extensions", "install")
    assert register.install_argv[3] == str(bundle_root)
    assert "--consent" in register.install_argv
    assert "--skip-settings" in register.install_argv
    assert register.uninstall_argv == ("gemini", "extensions", "uninstall", "manyagent")
    assert register.stdin_input == "1\n"  # the workspace-trust prompt answer


def test_gemini_install_writes_bundle_and_invokes_extensions_install(
    fake_home: Path, captured_cli: list[list[str]]
) -> None:
    from manyagent.adapters.skills.gemini import install

    oma_home = fake_home / ".manyagent"
    m = install(session_id="S1", oma_home=oma_home, scope="user")
    assert m is not None

    bundle_root = oma_home / "extensions" / "gemini-manyagent"
    assert (bundle_root / "gemini-extension.json").is_file()
    assert (bundle_root / "GEMINI.md").is_file()
    for verb in ("self-distill", "discuss", "cross-distill", "inject"):
        assert (bundle_root / "commands" / f"{verb}.toml").is_file()
    # NEVER write ~/.gemini/extensions/manyagent/ directly.
    assert not (fake_home / ".gemini" / "extensions" / "manyagent").exists()
    # The link CLI was invoked.
    assert any(argv[:3] == ["gemini", "extensions", "install"] and argv[3] == str(bundle_root) for argv in captured_cli)


def test_gemini_adapter_install_skills_delegates(fake_home: Path, captured_cli: list[list[str]]) -> None:
    from manyagent.adapters.builtin.gemini import GeminiAdapter

    adapter = GeminiAdapter(session_id="S1", agent_id="S1/agent-001-gemini")
    oma_home = fake_home / ".manyagent"
    m = adapter.install_skills(session_id="S1", oma_home=oma_home, scope="user")
    assert m is not None
    assert (oma_home / "extensions" / "gemini-manyagent" / "gemini-extension.json").is_file()


def test_gemini_uninstall_runs_extensions_uninstall(fake_home: Path, captured_cli: list[list[str]]) -> None:
    from manyagent.adapters.skills.gemini import install

    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user")
    captured_cli.clear()

    out: list[str] = []
    rc = uninstall("gemini", oma_home, output_fn=out.append)
    assert rc == 0
    assert any(argv == ["gemini", "extensions", "uninstall", "manyagent"] for argv in captured_cli)


# --------------------------------------------------------------------------- #
# Codex installer — ``codex`` has no env_vars / per-tool approval CLI, so the
# tomlkit merge of ~/.codex/config.toml is the documented path (advisor).
# --------------------------------------------------------------------------- #


def test_codex_plan_creates_bare_verb_skills_and_toml_merges(fake_home: Path) -> None:
    """Codex reserves the ``/`` namespace — skills are invoked as ``$<verb>``."""
    from manyagent.adapters.skills.codex import build_plan

    plan = build_plan(session_id="S1", scope="user")
    creates = [op for op in plan.ops if op.kind == "create"]
    merges = [op for op in plan.ops if op.kind == "merge"]
    assert {Path(op.path).parent.name for op in creates} == {
        "self-distill",
        "discuss",
        "cross-distill",
        "inject",
    }
    # Three TOML merges: the main server entry + two per-tool approval modes.
    assert len(merges) == 3
    assert all(Path(op.path).name == "config.toml" for op in merges)
    sections = {op.merge_keys[0] for op in merges}
    assert sections == {
        "mcp_servers.manyagent",
        "mcp_servers.manyagent.tools.commit_post",
        "mcp_servers.manyagent.tools.inject_commit",
    }


def test_codex_install_writes_skills_and_merges_toml(fake_home: Path) -> None:
    from manyagent.adapters.skills.codex import install

    oma_home = fake_home / ".manyagent"
    m = install(session_id="S1", oma_home=oma_home, scope="user")
    assert m is not None
    config_path = fake_home / ".codex" / "config.toml"
    assert config_path.is_file()
    text = config_path.read_text()
    assert "[mcp_servers.manyagent]" in text
    assert "[mcp_servers.manyagent.tools.commit_post]" in text
    assert 'approval_mode = "prompt"' in text
    assert "MANYAGENT_SESSION" in text  # env_vars allowlist
    for verb in ("self-distill", "discuss", "cross-distill", "inject"):
        assert (fake_home / ".codex" / "skills" / verb / "SKILL.md").is_file()


def test_codex_install_preserves_third_party_mcp_server_round_trip(fake_home: Path) -> None:
    """The user already has another MCP server + comments in
    ``~/.codex/config.toml``. Install adds ``[mcp_servers.manyagent]``, uninstall
    removes it, and the other content is byte-identical to before — including
    inline comments (tomlkit-preserving)."""
    from manyagent.adapters.skills.codex import install

    config_path = fake_home / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    before = (
        "# the user's existing codex config\n"
        "[mcp_servers.docs]\n"
        'command = "docs-server"\n'
        "# a comment on args\n"
        'args = ["--port", "4000"]\n'
    )
    config_path.write_text(before)

    oma_home = fake_home / ".manyagent"
    install(session_id="S1", oma_home=oma_home, scope="user")

    mid = config_path.read_text()
    assert "[mcp_servers.docs]" in mid  # third-party server survives
    assert "[mcp_servers.manyagent]" in mid  # our entry landed
    assert "# the user's existing codex config" in mid  # top comment survives
    assert "# a comment on args" in mid  # mid-comment survives

    rc = uninstall("codex", oma_home, output_fn=lambda _s: None)
    assert rc == 0
    after = config_path.read_text() if config_path.is_file() else None
    # After uninstall: manyagent sections gone, the user's content + comments intact.
    assert after is not None
    assert "[mcp_servers.manyagent]" not in after
    assert "[mcp_servers.docs]" in after
    assert "# the user's existing codex config" in after
    assert "# a comment on args" in after
    assert 'command = "docs-server"' in after


def test_codex_adapter_install_skills_delegates(fake_home: Path) -> None:
    from manyagent.adapters.builtin.codex import CodexAdapter

    adapter = CodexAdapter(session_id="S1", agent_id="S1/agent-001-codex")
    oma_home = fake_home / ".manyagent"
    m = adapter.install_skills(session_id="S1", oma_home=oma_home, scope="user")
    assert m is not None
    assert (fake_home / ".codex" / "config.toml").is_file()
