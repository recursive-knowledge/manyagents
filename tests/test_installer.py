"""M11 tests for ``manyagent._installer`` — the transparency contract.

Headlines:

* **Idempotent install** — applying the same plan twice writes byte-identical
  files (the advisor's "twice == once" invariant).
* **Third-party content survives** — when we merge our key into a config the
  user already populated (e.g. another MCP server in `.mcp.json`), the other
  keys are preserved through install AND uninstall round-trip.
* **Uninstall reverses cleanly** — created files are removed iff still
  matching what we wrote; merged files have only our keys popped.
* **User-edited files are NOT deleted** — if a created file was modified after
  install (sha256 mismatch), uninstall leaves it in place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manyagent._installer import (
    CLIAction,
    FileOp,
    InstallPlan,
    apply_plan,
    consent_prompt,
    list_installed,
    load_manifest,
    merge_json_keys,
    merge_json_list_item,
    merge_toml_section,
    uninstall,
    unmerge_json_list_items,
)

# --------------------------------------------------------------------------- #
# merge_json_keys
# --------------------------------------------------------------------------- #


def test_merge_json_keys_creates_when_absent(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    text, prev = merge_json_keys(p, "mcpServers", "manyagent", {"command": "python"})
    assert prev is None
    assert json.loads(text) == {"mcpServers": {"manyagent": {"command": "python"}}}


def test_merge_json_keys_preserves_third_party(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps(
            {
                "mcpServers": {"other": {"command": "node"}},
                "permissions": {"allow": ["Bash"]},
            },
            indent=2,
        )
    )
    text, _prev = merge_json_keys(p, "mcpServers", "manyagent", {"command": "python"})
    data = json.loads(text)
    assert data["mcpServers"]["other"] == {"command": "node"}  # third-party survives
    assert data["mcpServers"]["manyagent"] == {"command": "python"}
    assert data["permissions"] == {"allow": ["Bash"]}  # unrelated top-level keys survive


def test_merge_json_keys_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    payload = {"command": "python", "args": ["-m", "manyagent._mcp"]}
    once, _ = merge_json_keys(p, "mcpServers", "manyagent", payload)
    p.write_text(once)
    twice, _ = merge_json_keys(p, "mcpServers", "manyagent", payload)
    assert once == twice  # twice == once, byte-identical


# --------------------------------------------------------------------------- #
# merge_toml_section
# --------------------------------------------------------------------------- #


def test_merge_toml_section_preserves_comments_and_other_servers(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        "# user's codex config\n"
        "[mcp_servers.docs]\n"
        'command = "docs-server"\n'
        "# a comment on the args line\n"
        'args = ["--port", "4000"]\n'
    )
    text, _prev = merge_toml_section(
        p,
        "mcp_servers.manyagent",
        {"command": "python", "args": ["-m", "manyagent._mcp"], "env_vars": ["MANYAGENT_SESSION"]},
    )
    assert "# user's codex config" in text  # top comment survives
    assert "# a comment on the args line" in text  # mid-document comment survives
    assert "docs-server" in text  # third-party server survives
    assert "manyagent._mcp" in text  # our entry landed


def test_merge_toml_section_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    value = {"command": "python", "args": ["-m", "manyagent._mcp"]}
    once, _ = merge_toml_section(p, "mcp_servers.manyagent", value)
    p.write_text(once)
    twice, _ = merge_toml_section(p, "mcp_servers.manyagent", value)
    assert once == twice


# --------------------------------------------------------------------------- #
# merge_json_list_item / unmerge_json_list_items — shared arrays (hooks)
# --------------------------------------------------------------------------- #

_OUR_HOOK = {"hooks": [{"type": "command", "command": "python -m manyagent._hook"}]}
_USER_HOOK = {"matcher": "startup", "hooks": [{"type": "command", "command": "say hi"}]}


def test_merge_json_list_item_creates_when_absent(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    text, prev = merge_json_list_item(p, "hooks", "SessionStart", _OUR_HOOK)
    assert prev is None
    assert json.loads(text) == {"hooks": {"SessionStart": [_OUR_HOOK]}}


def test_merge_json_list_item_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    once, _ = merge_json_list_item(p, "hooks", "SessionStart", _OUR_HOOK)
    p.write_text(once)
    twice, _ = merge_json_list_item(p, "hooks", "SessionStart", _OUR_HOOK)
    assert once == twice  # twice == once, byte-identical (no duplicate entry)


def test_merge_json_list_item_preserves_user_items(tmp_path: Path) -> None:
    """The decisive difference vs ``merge_json_keys``: a hooks array the user
    already populated is APPENDED to, never clobbered."""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"SessionStart": [_USER_HOOK]}, "model": "opus"}, indent=2))
    text, _ = merge_json_list_item(p, "hooks", "SessionStart", _OUR_HOOK)
    data = json.loads(text)
    assert data["hooks"]["SessionStart"] == [_USER_HOOK, _OUR_HOOK]  # user's hook survives, ours appended
    assert data["model"] == "opus"


def test_unmerge_json_list_items_removes_only_ours(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"SessionStart": [_USER_HOOK, _OUR_HOOK]}}, indent=2))
    text = unmerge_json_list_items(p, "hooks", "SessionStart", [_OUR_HOOK])
    assert text is not None
    assert json.loads(text) == {"hooks": {"SessionStart": [_USER_HOOK]}}


def test_unmerge_json_list_items_prunes_empty_and_signals_delete(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"SessionStart": [_OUR_HOOK]}}, indent=2))
    # Removing our only entry empties the array, the `hooks` object, then the
    # whole document — None tells the caller to delete the file.
    assert unmerge_json_list_items(p, "hooks", "SessionStart", [_OUR_HOOK]) is None


def test_merge_json_list_item_purges_stale_marked_variants(tmp_path: Path) -> None:
    """A reinstall whose item differs (e.g. a new venv path baked into the
    command) replaces the old manyagent entry instead of accumulating — but never
    touches user items, which carry no marker."""
    p = tmp_path / "settings.json"
    stale = {"hooks": [{"type": "command", "command": "/old/venv/python -m manyagent._hook"}]}
    p.write_text(json.dumps({"hooks": {"SessionStart": [_USER_HOOK, stale]}}, indent=2))
    text, _ = merge_json_list_item(p, "hooks", "SessionStart", _OUR_HOOK, purge_contains="-m manyagent._hook")
    assert json.loads(text)["hooks"]["SessionStart"] == [_USER_HOOK, _OUR_HOOK]


def test_unmerge_json_list_items_purges_edited_marked_entries(tmp_path: Path) -> None:
    """Uninstall removes an manyagent entry even after the user/host tool edited it
    (structural equality broken) as long as the staleness marker survives."""
    edited = {"matcher": "*", "hooks": [{"type": "command", "command": "python -m manyagent._hook"}]}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"SessionStart": [_USER_HOOK, edited]}}, indent=2))
    text = unmerge_json_list_items(p, "hooks", "SessionStart", [_OUR_HOOK], purge_contains="-m manyagent._hook")
    assert text is not None
    assert json.loads(text) == {"hooks": {"SessionStart": [_USER_HOOK]}}


def test_apply_plan_failure_saves_partial_manifest_for_reversal(tmp_path: Path) -> None:
    """A mid-apply failure (a user-shaped settings.json the merge can't
    parse) must not strand already-written creates with no manifest: a
    partial manifest is saved so `manyagent uninstall` can reverse them."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": []}, indent=2))  # hooks is an ARRAY → TypeError
    plan = InstallPlan(
        adapter="demo-partial",
        scope="user",
        session_id="S1",
        ops=[
            FileOp(kind="create", path=tmp_path / "skills" / "x" / "SKILL.md", payload="# x", description="x"),
            FileOp(
                kind="merge",
                path=settings,
                payload={"__top_key__": "hooks", "__our_key__": "SessionStart", "__list_item__": _OUR_HOOK},
                description="hook",
                merge_keys=("list:hooks.SessionStart",),
            ),
        ],
    )
    oma_home = tmp_path / ".manyagent"
    with pytest.raises(TypeError):
        apply_plan(plan, oma_home=oma_home)
    created = tmp_path / "skills" / "x" / "SKILL.md"
    assert created.is_file()  # the create landed before the failure...
    assert load_manifest("demo-partial", oma_home) is not None  # ...but it is on the books
    assert uninstall("demo-partial", oma_home, output_fn=lambda _s: None) == 0
    assert not created.exists()  # and reversible


def test_list_merge_round_trip_through_apply_and_uninstall(tmp_path: Path) -> None:
    """install → uninstall over a settings.json the user already owns must be
    byte-identical: our hook entry comes and goes, theirs never moves."""
    settings = tmp_path / "settings.json"
    original = json.dumps({"hooks": {"SessionStart": [_USER_HOOK]}, "model": "opus"}, indent=2) + "\n"
    settings.write_text(original)
    plan = InstallPlan(
        adapter="demo-hooks",
        scope="user",
        session_id="S1",
        ops=[
            FileOp(
                kind="merge",
                path=settings,
                payload={"__top_key__": "hooks", "__our_key__": "SessionStart", "__list_item__": _OUR_HOOK},
                description="SessionStart hook",
                merge_keys=("list:hooks.SessionStart",),
            ),
        ],
    )
    oma_home = tmp_path / ".manyagent"
    apply_plan(plan, oma_home=oma_home)
    data = json.loads(settings.read_text())
    assert data["hooks"]["SessionStart"] == [_USER_HOOK, _OUR_HOOK]
    manifest = load_manifest("demo-hooks", oma_home)
    assert manifest is not None
    assert manifest.entries[0].merge_keys == ["list:hooks.SessionStart"]
    assert manifest.entries[0].merge_items == [_OUR_HOOK]

    rc = uninstall("demo-hooks", oma_home, output_fn=lambda _s: None)
    assert rc == 0
    assert settings.read_text() == original  # byte-identical round trip


# --------------------------------------------------------------------------- #
# apply_plan + manifest + uninstall round-trip
# --------------------------------------------------------------------------- #


def _two_file_plan(tmp_path: Path) -> InstallPlan:
    return InstallPlan(
        adapter="demo",
        scope="user",
        session_id="S1",
        ops=[
            FileOp(
                kind="create",
                path=tmp_path / "skills" / "demo" / "SKILL.md",
                payload="# demo skill",
                description="demo skill body",
            ),
            FileOp(
                kind="merge",
                path=tmp_path / "settings.json",
                payload={
                    "__top_key__": "mcpServers",
                    "__our_key__": "manyagent",
                    "__value__": {"command": "python", "args": ["-m", "manyagent._mcp"]},
                },
                description="register manyagent MCP server",
                merge_keys=("mcpServers.manyagent",),
            ),
        ],
    )


def test_apply_plan_writes_files_and_manifest(tmp_path: Path) -> None:
    plan = _two_file_plan(tmp_path)
    oma_home = tmp_path / "oma_home"
    m = apply_plan(plan, oma_home=oma_home)
    # files written
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    settings = tmp_path / "settings.json"
    assert skill.read_text() == "# demo skill"
    assert json.loads(settings.read_text())["mcpServers"]["manyagent"]["command"] == "python"
    # manifest persisted
    assert (oma_home / "installed" / "demo.json").is_file()
    loaded = load_manifest("demo", oma_home)
    assert loaded is not None and len(loaded.entries) == 2
    assert m.session_id == "S1"


def test_apply_plan_dry_run_writes_nothing(tmp_path: Path) -> None:
    plan = _two_file_plan(tmp_path)
    oma_home = tmp_path / "oma_home"
    apply_plan(plan, oma_home=oma_home, dry_run=True)
    assert not (tmp_path / "skills" / "demo" / "SKILL.md").exists()
    assert not (tmp_path / "settings.json").exists()
    assert not (oma_home / "installed" / "demo.json").exists()


def test_apply_plan_idempotent_twice_equals_once(tmp_path: Path) -> None:
    """The advisor's headline invariant: re-running install yields a
    byte-identical filesystem."""
    plan = _two_file_plan(tmp_path)
    oma_home = tmp_path / "oma_home"

    apply_plan(plan, oma_home=oma_home)
    skill_once = (tmp_path / "skills" / "demo" / "SKILL.md").read_bytes()
    settings_once = (tmp_path / "settings.json").read_bytes()

    apply_plan(plan, oma_home=oma_home)
    skill_twice = (tmp_path / "skills" / "demo" / "SKILL.md").read_bytes()
    settings_twice = (tmp_path / "settings.json").read_bytes()

    assert skill_once == skill_twice
    assert settings_once == settings_twice


def test_third_party_survives_install_and_uninstall_round_trip(tmp_path: Path) -> None:
    """The user already has another MCP server in `.mcp.json`. After install +
    uninstall the other server must be byte-identical to where it started."""
    settings = tmp_path / "settings.json"
    before = (
        json.dumps(
            {
                "mcpServers": {"other": {"command": "node", "args": ["other.js"]}},
                "permissions": {"allow": ["Bash(ls *)"]},
            },
            indent=2,
        )
        + "\n"
    )
    settings.write_text(before)

    plan = _two_file_plan(tmp_path)
    oma_home = tmp_path / "oma_home"
    apply_plan(plan, oma_home=oma_home)
    # after install: our key + theirs
    mid = json.loads(settings.read_text())
    assert mid["mcpServers"]["other"] == {"command": "node", "args": ["other.js"]}
    assert mid["mcpServers"]["manyagent"]["command"] == "python"

    # uninstall: pop our key only
    out_lines: list[str] = []
    rc = uninstall("demo", oma_home, output_fn=out_lines.append)
    assert rc == 0
    after = json.loads(settings.read_text())
    # third-party MCP server + permissions are byte-identical to before
    assert after["mcpServers"] == {"other": {"command": "node", "args": ["other.js"]}}
    assert after["permissions"] == {"allow": ["Bash(ls *)"]}


def test_uninstall_removes_created_files_we_still_own(tmp_path: Path) -> None:
    plan = _two_file_plan(tmp_path)
    oma_home = tmp_path / "oma_home"
    apply_plan(plan, oma_home=oma_home)
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    assert skill.is_file()
    uninstall("demo", oma_home, output_fn=lambda _s: None)
    assert not skill.exists()
    # manifest also gone
    assert not (oma_home / "installed" / "demo.json").exists()


def test_uninstall_keeps_user_edited_created_files(tmp_path: Path) -> None:
    plan = _two_file_plan(tmp_path)
    oma_home = tmp_path / "oma_home"
    apply_plan(plan, oma_home=oma_home)
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.write_text("# the user edited this after install")
    out: list[str] = []
    uninstall("demo", oma_home, output_fn=out.append)
    assert skill.is_file()  # NOT deleted — content changed since install
    assert any("KEPT" in line and "user-edited" in line for line in out)


def test_uninstall_missing_manifest_returns_1(tmp_path: Path) -> None:
    rc = uninstall("nope", tmp_path / "oma_home", output_fn=lambda _s: None)
    assert rc == 1


def test_list_installed(tmp_path: Path) -> None:
    plan = _two_file_plan(tmp_path)
    oma_home = tmp_path / "oma_home"
    assert list_installed(oma_home) == []
    apply_plan(plan, oma_home=oma_home)
    [m] = list_installed(oma_home)
    assert m.adapter == "demo" and len(m.entries) == 2


# --------------------------------------------------------------------------- #
# consent_prompt
# --------------------------------------------------------------------------- #


def _plan(tmp_path: Path) -> InstallPlan:
    return _two_file_plan(tmp_path)


def test_consent_prompt_manyagent_install_skills_auto_silent_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "auto")
    out: list[str] = []
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out.append)
    assert ok is True
    assert out == []  # silent


def test_consent_prompt_manyagent_install_skills_deny_silent_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "deny")
    out: list[str] = []
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "y", output_fn=out.append)
    assert ok is False
    assert any("skipping" in line for line in out)


def test_consent_prompt_default_asks_once_then_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    out: list[str] = []
    # first call: no manifest yet → ask. Reply 'y'.
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "y", output_fn=out.append, manifest_exists=False)
    assert ok is True
    blob = "\n".join(out)
    assert "1 created · 1 merged" in blob  # the plan summary was rendered
    assert "mcpServers.manyagent" in blob  # merge transparency: the keys we own are listed
    # second call: manifest exists → silent yes.
    out2: list[str] = []
    ok2 = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out2.append, manifest_exists=True)
    assert ok2 is True and out2 == []  # silent re-run


def test_consent_prompt_decline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    out: list[str] = []
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out.append)
    assert ok is False
    assert any("declined" in line for line in out)


def test_consent_prompt_diff_then_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    answers = iter(["d", "y"])
    out: list[str] = []
    ok = consent_prompt(
        _plan(tmp_path),
        input_fn=lambda _p: next(answers),
        output_fn=out.append,
    )
    assert ok is True
    assert any("===" in line for line in out)  # diff was rendered


# --------------------------------------------------------------------------- #
# advisory welcome panel (plans that declare `commands`) + decline memory
# --------------------------------------------------------------------------- #


def _command_plan(tmp_path: Path) -> InstallPlan:
    """A plan that declares `commands` — consent leads with the advisory
    panel; the file-by-file plan + diff live behind [d]etails."""
    return InstallPlan(
        adapter="demo",
        scope="user",
        session_id="S1",
        ops=[
            FileOp(
                kind="create",
                path=tmp_path / ".demo" / "skills" / "demo" / "SKILL.md",
                payload="# demo skill",
                description="`/demo` skill — do one demo thing",
            ),
            FileOp(
                kind="merge",
                path=tmp_path / ".demo" / "settings.json",
                payload={
                    "__top_key__": "hooks",
                    "__our_key__": "SessionStart",
                    "__list_item__": {"hooks": [{"type": "command", "command": "py -m manyagent._hook"}]},
                    "__list_purge__": "-m manyagent._hook",
                },
                description="SessionStart hook",
                merge_keys=("list:hooks.SessionStart",),
            ),
        ],
        cli_actions=[
            CLIAction(
                install_argv=("demo", "mcp", "add", "manyagent"),
                uninstall_argv=("demo", "mcp", "remove", "manyagent"),
                description="register the manyagent MCP server",
            )
        ],
        commands=[("/demo", "do one demo thing")],
    )


def test_consent_advisory_panel_leads_details_behind_d(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First screen: what the user gets + the undo — no paths, keys, or argvs.
    [d] reveals the full file-by-file plan AND the diff, then re-prompts."""
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    answers = iter(["d", "y"])
    out: list[str] = []
    seen_at_prompt: list[str] = []

    def _input(prompt: str) -> str:
        seen_at_prompt.append("\n".join(out))
        return next(answers)

    ok = consent_prompt(_command_plan(tmp_path), input_fn=_input, output_fn=out.append)
    assert ok is True
    first_screen = seen_at_prompt[0]
    assert "/demo" in first_screen and "do one demo thing" in first_screen
    assert "manyagent uninstall demo" in first_screen  # the undo is advisory, up front
    assert "SKILL.md" not in first_screen  # plumbing stays behind [d]
    assert "mcp add" not in first_screen
    assert "keys we own" not in first_screen
    # after pressing d: the full plan + diff were rendered
    details = seen_at_prompt[1]
    assert "SKILL.md" in details and "demo mcp add manyagent" in details
    assert "hooks.SessionStart" in details  # merge transparency survives in [d]
    assert "list:hooks" not in details  # ...without the manifest-encoding prefix
    assert "===" in details  # the diff followed the plan


def test_consent_decline_is_remembered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A first-run 'no' writes a declined marker; later runs print one dim
    line instead of re-walling the user with the panel."""
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    oma_home = tmp_path / "oma_home"
    out: list[str] = []
    ok = consent_prompt(_command_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out.append, oma_home=oma_home)
    assert ok is False
    assert (oma_home / "installed" / "demo.declined").is_file()
    assert any("won't ask again" in line for line in out)
    # second run: no prompt at all — input_fn raising proves it's never called
    out2: list[str] = []

    def _boom(_p: str) -> str:
        raise AssertionError("prompt should not be shown after a recorded decline")

    ok2 = consent_prompt(_command_plan(tmp_path), input_fn=_boom, output_fn=out2.append, oma_home=oma_home)
    assert ok2 is False
    assert len(out2) == 1 and "declined earlier" in out2[0]


def test_consent_prompt_mode_re_asks_and_yes_clears_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MANYAGENT_INSTALL_SKILLS=prompt re-offers past a recorded decline; accepting
    removes the marker (the manifest becomes the consent record)."""
    oma_home = tmp_path / "oma_home"
    marker = oma_home / "installed" / "demo.declined"
    marker.parent.mkdir(parents=True)
    marker.write_text("2026-06-09T00:00:00\n")
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "prompt")
    ok = consent_prompt(_command_plan(tmp_path), input_fn=lambda _p: "y", output_fn=lambda _s: None, oma_home=oma_home)
    assert ok is True
    assert not marker.exists()


def test_consent_auto_yes_and_uninstall_both_clear_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Any affirmative consent supersedes an old decline — including the
    MANYAGENT_INSTALL_SKILLS=auto fast path — and `manyagent uninstall` resets consent
    state fully, so the next run is a genuine first run again."""
    oma_home = tmp_path / "oma_home"
    marker = oma_home / "installed" / "demo.declined"
    # decline → marker recorded
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    consent_prompt(_command_plan(tmp_path), input_fn=lambda _p: "n", output_fn=lambda _s: None, oma_home=oma_home)
    assert marker.is_file()
    # auto-mode yes → marker cleared, install proceeds
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "auto")
    assert consent_prompt(_command_plan(tmp_path), output_fn=lambda _s: None, oma_home=oma_home) is True
    assert not marker.exists()
    apply_plan(_command_plan(tmp_path), oma_home=oma_home)
    # uninstall removes manifest AND any marker (re-decline first to plant one)
    marker.write_text("2026-06-09T00:00:00\n")
    uninstall("demo", oma_home, output_fn=lambda _s: None)
    assert not marker.exists()
    # next default-mode run prompts again
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    asked: list[str] = []

    def _input(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    consent_prompt(_command_plan(tmp_path), input_fn=_input, output_fn=lambda _s: None, oma_home=oma_home)
    assert asked  # the panel re-offered — not silently suppressed


def test_consent_dry_run_never_touches_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run consent honors the no-disk-writes contract: a 'n' answer
    records nothing, and a 'y' answer doesn't erase a real prior decline."""
    monkeypatch.delenv("MANYAGENT_INSTALL_SKILLS", raising=False)
    oma_home = tmp_path / "oma_home"
    marker = oma_home / "installed" / "demo.declined"
    out: list[str] = []
    ok = consent_prompt(
        _command_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out.append, oma_home=oma_home, dry_run=True
    )
    assert ok is False and not marker.exists()
    assert not any("won't ask again" in line for line in out)  # no false promise
    # plant a real decline; a dry-run 'y' must not erase it
    marker.parent.mkdir(parents=True)
    marker.write_text("2026-06-09T00:00:00\n")
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "prompt")
    ok2 = consent_prompt(
        _command_plan(tmp_path), input_fn=lambda _p: "y", output_fn=lambda _s: None, oma_home=oma_home, dry_run=True
    )
    assert ok2 is True and marker.is_file()


def test_consent_unknown_mode_warns_and_re_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown MANYAGENT_INSTALL_SKILLS value falls back to 'prompt' for real:
    the warning fires and the prompt shows even past a manifest or marker."""
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "yolo")
    oma_home = tmp_path / "oma_home"
    marker = oma_home / "installed" / "demo.declined"
    marker.parent.mkdir(parents=True)
    marker.write_text("2026-06-09T00:00:00\n")
    out: list[str] = []
    ok = consent_prompt(
        _command_plan(tmp_path),
        input_fn=lambda _p: "n",
        output_fn=out.append,
        manifest_exists=True,
        oma_home=oma_home,
    )
    assert ok is False  # asked (not silently True from the manifest)
    assert any("unknown MANYAGENT_INSTALL_SKILLS" in line for line in out)


def test_consent_decline_while_installed_writes_no_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Declining a re-offer while a manifest exists is not an uninstall: no
    marker (the manifest would silently override it anyway) — point at the
    real verb instead."""
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "prompt")
    oma_home = tmp_path / "oma_home"
    out: list[str] = []
    ok = consent_prompt(
        _command_plan(tmp_path),
        input_fn=lambda _p: "n",
        output_fn=out.append,
        manifest_exists=True,
        oma_home=oma_home,
    )
    assert ok is False
    assert not (oma_home / "installed" / "demo.declined").exists()
    assert any("manyagent uninstall demo" in line for line in out)


# --------------------------------------------------------------------------- #
# _run_cli: failure notes print by default, suppressed for failure_ok actions
# --------------------------------------------------------------------------- #


def test_run_cli_failure_prints_note_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    import sys

    from manyagent._installer import _run_cli

    _run_cli(
        [sys.executable, "-c", "import sys; sys.stderr.write('it broke\\n'); sys.exit(1)"],
        description="doomed action",
    )
    assert "manyagent: doomed action — exit 1: it broke" in capsys.readouterr().out


def test_run_cli_failure_ok_suppresses_note(capsys: pytest.CaptureFixture[str]) -> None:
    """A pre-clear whose target was never registered exits nonzero — that IS
    the expected fresh-install case and must not print scary noise
    (decision 2026-06-10: `manyagent: pre-clear ... — exit 1: No user-scoped MCP
    server found` on every first install)."""
    import sys

    from manyagent._installer import _run_cli

    _run_cli(
        [sys.executable, "-c", "import sys; sys.stderr.write('No user-scoped MCP server found\\n'); sys.exit(1)"],
        description="pre-clear any existing manyagent MCP server (--scope user)",
        failure_ok=True,
    )
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- #
# security: manyagent-owned dirs created with 0700 (fix 3)
# --------------------------------------------------------------------------- #


def test_save_manifest_creates_installed_dir_with_0700(tmp_path: Path) -> None:
    """The ``installed/`` dir under MANYAGENT_HOME must not be world-readable."""
    import os
    import sys

    if sys.platform == "win32" or os.getuid() == 0:  # type: ignore[attr-defined]
        pytest.skip("permission bits not meaningful on Windows or when running as root (root ignores mode)")

    from manyagent._installer import Manifest, save_manifest

    oma_home = tmp_path / "oma"
    manifest = Manifest(adapter="demo", scope="user", installed_at="2026-01-01T00:00:00+00:00", session_id=None)
    save_manifest(manifest, oma_home)
    installed_dir = oma_home / "installed"
    assert installed_dir.is_dir()
    mode = installed_dir.stat().st_mode & 0o777
    # The dir must NOT be world-readable or world-executable.
    assert not (mode & 0o007), f"installed/ dir is world-accessible: {oct(mode)}"
