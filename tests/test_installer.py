"""M11 tests for ``oms._installer`` — the transparency contract.

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

from oms._installer import (
    FileOp,
    InstallPlan,
    apply_plan,
    consent_prompt,
    list_installed,
    load_manifest,
    merge_json_keys,
    merge_toml_section,
    uninstall,
)

# --------------------------------------------------------------------------- #
# merge_json_keys
# --------------------------------------------------------------------------- #


def test_merge_json_keys_creates_when_absent(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    text, prev = merge_json_keys(p, "mcpServers", "oms", {"command": "python"})
    assert prev is None
    assert json.loads(text) == {"mcpServers": {"oms": {"command": "python"}}}


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
    text, _prev = merge_json_keys(p, "mcpServers", "oms", {"command": "python"})
    data = json.loads(text)
    assert data["mcpServers"]["other"] == {"command": "node"}  # third-party survives
    assert data["mcpServers"]["oms"] == {"command": "python"}
    assert data["permissions"] == {"allow": ["Bash"]}  # unrelated top-level keys survive


def test_merge_json_keys_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    payload = {"command": "python", "args": ["-m", "oms._mcp"]}
    once, _ = merge_json_keys(p, "mcpServers", "oms", payload)
    p.write_text(once)
    twice, _ = merge_json_keys(p, "mcpServers", "oms", payload)
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
        "mcp_servers.oms",
        {"command": "python", "args": ["-m", "oms._mcp"], "env_vars": ["OMS_SESSION"]},
    )
    assert "# user's codex config" in text  # top comment survives
    assert "# a comment on the args line" in text  # mid-document comment survives
    assert "docs-server" in text  # third-party server survives
    assert "oms._mcp" in text  # our entry landed


def test_merge_toml_section_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    value = {"command": "python", "args": ["-m", "oms._mcp"]}
    once, _ = merge_toml_section(p, "mcp_servers.oms", value)
    p.write_text(once)
    twice, _ = merge_toml_section(p, "mcp_servers.oms", value)
    assert once == twice


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
                    "__our_key__": "oms",
                    "__value__": {"command": "python", "args": ["-m", "oms._mcp"]},
                },
                description="register oms MCP server",
                merge_keys=("mcpServers.oms",),
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
    assert json.loads(settings.read_text())["mcpServers"]["oms"]["command"] == "python"
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
    assert mid["mcpServers"]["oms"]["command"] == "python"

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


def test_consent_prompt_oms_install_skills_auto_silent_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_INSTALL_SKILLS", "auto")
    out: list[str] = []
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out.append)
    assert ok is True
    assert out == []  # silent


def test_consent_prompt_oms_install_skills_deny_silent_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_INSTALL_SKILLS", "deny")
    out: list[str] = []
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "y", output_fn=out.append)
    assert ok is False
    assert any("skipping" in line for line in out)


def test_consent_prompt_default_asks_once_then_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMS_INSTALL_SKILLS", raising=False)
    out: list[str] = []
    # first call: no manifest yet → ask. Reply 'y'.
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "y", output_fn=out.append, manifest_exists=False)
    assert ok is True and any("Proceed?" not in line and "MERGE" in line for line in out)
    # second call: manifest exists → silent yes.
    out2: list[str] = []
    ok2 = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out2.append, manifest_exists=True)
    assert ok2 is True and out2 == []  # silent re-run


def test_consent_prompt_decline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMS_INSTALL_SKILLS", raising=False)
    out: list[str] = []
    ok = consent_prompt(_plan(tmp_path), input_fn=lambda _p: "n", output_fn=out.append)
    assert ok is False
    assert any("declined" in line for line in out)


def test_consent_prompt_diff_then_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMS_INSTALL_SKILLS", raising=False)
    answers = iter(["d", "y"])
    out: list[str] = []
    ok = consent_prompt(
        _plan(tmp_path),
        input_fn=lambda _p: next(answers),
        output_fn=out.append,
    )
    assert ok is True
    assert any("===" in line for line in out)  # diff was rendered
