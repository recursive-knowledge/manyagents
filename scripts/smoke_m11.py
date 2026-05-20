"""M11 smoke — drive every in-agent verb (and the installer round-trip)
against a FakeBank with a TMP HOME, so the real ~/.claude/ is never touched.

This is what the user would otherwise do manually with `oma claude` + typing
`/self-distill` inside Claude Code. Here every layer the in-agent flow uses
is exercised in isolation:

  1. The installer: writes 4 SKILL.md files + merges `~/.claude/settings.json`,
     records the manifest, and is byte-identical on re-run (idempotent).
  2. The four MCP tools: `self_distill_draft` → `commit_post` (reflection);
     `discuss_draft` → `commit_post` (reply); `cross_distill`;
     `inject_preview` → `inject_commit`. The commit/inject_commit tools are the
     human-gate moments inside the agent UI — here we just call them.
  3. The C1 invariant at the MCP layer: `commit_post` refuses a parser-failed
     payload (a draft + reject = no persistence, mechanically).
  4. Uninstall: removes our skills + pops our key from the merged
     settings.json. Any third-party content that was already there is
     byte-identical to where it started.

Run:  uv run python scripts/smoke_m11.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _hr(title: str) -> None:
    print(f"\n{'═' * 78}\n  {title}\n{'═' * 78}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    raise SystemExit(1)


async def main() -> None:
    tmp_home = Path(tempfile.mkdtemp(prefix="oma-smoke-m11-"))
    oma_home = tmp_home / ".oma"
    oma_home.mkdir(parents=True)

    # Redirect HOME so Path.home() points at our tmp — keeps the real ~/.claude
    # safe. The installer reads Path.home() / ".claude" / "skills" / ... so
    # this is the right knob.
    os.environ["HOME"] = str(tmp_home)
    os.environ["OMA_HOME"] = str(oma_home)
    os.environ["OMA_SESSION"] = "SMOKE-M11"
    os.environ["OMA_INSTALL_SKILLS"] = "auto"  # silent yes for the smoke

    # Wire a FakeBank in *before* importing the MCP tools so get_bank returns
    # the fake without ever touching Supabase.
    from oma.bank import FakeBank

    bank = FakeBank()

    import oma.bank as oma_bank

    oma_bank.get_bank = lambda *a, **k: bank  # type: ignore[assignment]

    # --- 1. installer round-trip ------------------------------------------ #
    _hr("STEP 1 — installer (write skills + merge settings.json; idempotent)")

    # Seed settings.json with a fake third-party MCP server so we can verify
    # it survives both install and uninstall byte-identically.
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    before = (
        json.dumps(
            {
                "mcpServers": {"other": {"command": "node", "args": ["other.js"]}},
                "permissions": {"allow": ["Bash(ls *)"]},
                "theme": "dark",
            },
            indent=2,
        )
        + "\n"
    )
    settings_path.write_text(before)
    _ok(f"seeded {settings_path} with a third-party MCP server + 'permissions' + 'theme'")

    from oma.adapters.skills.claude import install

    manifest = install(session_id="SMOKE-M11", oma_home=oma_home, scope="user")
    assert manifest is not None
    skills_root = tmp_home / ".claude" / "skills"
    for verb in ("self-distill", "discuss", "cross-distill", "inject"):
        skill = skills_root / f"oma-{verb}" / "SKILL.md"
        assert skill.is_file(), f"missing {skill}"
        body = skill.read_text()
        assert f"name: {verb}" in body
        assert "mcp__oma__" in body
        _ok(f"installed {skill}  ({len(body)} bytes)")

    merged = json.loads(settings_path.read_text())
    assert merged["mcpServers"]["other"] == {"command": "node", "args": ["other.js"]}, "third-party server clobbered!"
    assert merged["mcpServers"]["oma"] == {"command": sys.executable, "args": ["-m", "oma._mcp"]}, "our entry wrong"
    assert merged["permissions"] == {"allow": ["Bash(ls *)"]}, "unrelated keys clobbered!"
    assert merged["theme"] == "dark"
    _ok(f"merged {settings_path}: oma + other survive together; permissions + theme intact")

    # Idempotency: re-run; bytes must match.
    skill0 = (skills_root / "oma-self-distill" / "SKILL.md").read_bytes()
    settings0 = settings_path.read_bytes()
    install(session_id="SMOKE-M11", oma_home=oma_home, scope="user")
    assert (skills_root / "oma-self-distill" / "SKILL.md").read_bytes() == skill0
    assert settings_path.read_bytes() == settings0
    _ok("re-ran install: bytes identical (twice == once, the idempotency invariant)")

    # --- 2. /self-distill flow -------------------------------------------- #
    _hr("STEP 2 — /self-distill flow (draft → commit)")
    from oma._mcp import (
        commit_post,
        cross_distill,
        discuss_draft,
        inject_commit,
        inject_preview,
        self_distill_draft,
    )

    def _tool(t):  # FastMCP wraps decorated tools; .fn is the underlying coroutine
        return t.fn if hasattr(t, "fn") else t

    await bank.put_session("SMOKE-M11", goal="speed-up-the-parser")

    draft = await _tool(self_distill_draft)(guidance="focus on the tokenize hot loop")
    assert draft["session"] == "SMOKE-M11" and draft["kind"] == "reflection"
    assert "instruction_for_host_llm" in draft and draft["goal"] == "speed-up-the-parser"
    assert "commit_post" in draft["commit_via"]
    _ok(f"self_distill_draft → goal={draft['goal']!r}, prior_posts_count={draft['prior_posts_count']}")
    assert await bank.list_packets(type="post") == []
    _ok("draft did NOT persist (C1: nothing in Bank yet)")

    GOOD = {
        "load_bearing_assumption": "the `tokenize()` hot loop recompiles the regex per call",
        "evidence": "verbatim from a profile: 'cumtime 4.2s in tokenize()'",
        "evidence_ref": None,
        "proposed_next": "hoist the compiled pattern to scanner.py module scope",
        "predicted_outcome": "parse throughput ~1.8x; test_parse_speed passes",
        "confidence": "medium",
    }
    out = await _tool(commit_post)(kind="reflection", structured=GOOD, rating=4)
    assert out["ok"] is True and out["rating"] == 4
    _ok(f"commit_post → persisted {out['post_id']} (rating=4, kind=reflection)")
    [p] = await bank.list_packets(type="post")
    assert p["rating"] == 4 and p.get("preference") in (None,)  # C1: no preference on a post
    _ok("post in Bank has rating=4 and no `preference` field (C1)")

    bad = dict(GOOD)
    del bad["proposed_next"]
    out2 = await _tool(commit_post)(kind="reflection", structured=bad, rating=4)
    assert out2["ok"] is False and "parser refused" in out2["error"]
    _ok(f"commit_post(bad) → {out2['error']}  (refused, NOT persisted — C1 headline)")
    assert len(await bank.list_packets(type="post")) == 1  # still just the good one

    # --- 3. /discuss flow ------------------------------------------------- #
    _hr("STEP 3 — /discuss flow (retrieval-before-post → commit)")

    refl_id = (await bank.list_packets(type="post"))[0]["id"]
    ddraft = await _tool(discuss_draft)(stance="agree")
    assert ddraft["kind"] == "reply" and ddraft["stance"] == "agree"
    assert refl_id in ddraft["ranked_post_ids"] and ddraft["reply_to"] == refl_id
    _ok(f"discuss_draft(stance=agree) → reply_to={ddraft['reply_to']!r}")

    REPLY = {
        "load_bearing_assumption": "precompiling the `tokenize()` pattern at module scope removes the per-call cost",
        "evidence": "verbatim from a second profile: 'cumtime 0.3s in tokenize() after precompile'",
        "evidence_ref": None,
        "proposed_next": "land the precompile + add a regression test on parse throughput",
        "predicted_outcome": "parse throughput stays ~1.8x in CI",
        "confidence": "high",
    }
    out = await _tool(commit_post)(
        kind="reply",
        structured=REPLY,
        reply_to=ddraft["reply_to"],
        stance="agree",
    )
    assert out["ok"] is True and out["kind"] == "reply"
    _ok(f"commit_post(reply) → persisted {out['post_id']}")

    posts = await bank.list_packets(type="post")
    kinds = {p["kind"] for p in posts}
    assert kinds == {"reflection", "reply"} and len(posts) == 2
    _ok(f"Bank now has {len(posts)} posts: {kinds}")

    # --- 4. /cross-distill flow ------------------------------------------- #
    _hr("STEP 4 — /cross-distill (curator over goal-scoped posts)")

    class _FakeModel:
        def complete(self, _p: str, *, max_tokens: int | None = None) -> str:
            return json.dumps({
                "confirmed_constraints": [
                    {
                        "text": "precompile a regex used in a hot `tokenize()` loop",
                        "applies_when": "a parser recompiles the same pattern on every call",
                        "does_not_apply_when": "patterns used exactly once at startup",
                        "evidence": [{"post_id": refl_id, "quote": "recompiles the regex per call"}],
                        "confidence": "medium",
                    }
                ]
            })

    rmod = importlib.import_module("oma.distill.resolve")
    rmod._discover_local_model = lambda: _FakeModel()  # type: ignore[attr-defined]

    cd = await _tool(cross_distill)()
    assert cd["ok"] is True and cd["scope"] == "per_goal"
    assert cd["bundle_id"].startswith("curator/")
    assert cd["bucket_counts"]["confirmed_constraints"] == 1
    _ok(f"cross_distill → bundle {cd['bundle_id']}  ({cd['scope']}, parents={len(cd['parents'])})")
    bundle_id = cd["bundle_id"]

    # --- 5. /inject flow -------------------------------------------------- #
    _hr("STEP 5 — /inject flow (preview → commit; ledger row)")

    ip = await _tool(inject_preview)(packet=bundle_id)
    assert ip["ok"] is True and "preview" in ip and ip["target_session"] == "SMOKE-M11"
    _ok(f"inject_preview → preview shown ({len(ip['preview'])} chars), target=SMOKE-M11")
    assert await bank.list_injections() == []
    _ok("inject_preview did NOT write a ledger row (read-only)")

    ic = await _tool(inject_commit)(packet=bundle_id)
    assert ic["ok"] is True and ic["packet_id"] == bundle_id
    [row] = await bank.list_injections()
    assert row["packet_id"] == bundle_id and row["target_session_id"] == "SMOKE-M11"
    _ok(f"inject_commit → ledger row {row['packet_id']} → {row['target_session_id']}")

    # --- 6. uninstall round-trip ----------------------------------------- #
    _hr("STEP 6 — uninstall (reverse cleanly; third-party content survives)")
    from oma._installer import uninstall

    lines: list[str] = []
    rc = uninstall("claude", oma_home, output_fn=lines.append)
    assert rc == 0
    for line in lines:
        print(f"  {line}")

    for verb in ("self-distill", "discuss", "cross-distill", "inject"):
        path = skills_root / f"oma-{verb}" / "SKILL.md"
        assert not path.exists(), f"{path} should have been removed"
    _ok("all four oma-* skill files removed")

    after = json.loads(settings_path.read_text())
    expected = json.loads(before)
    assert after == expected, f"settings.json drifted!\nbefore={before!r}\nafter ={after!r}"
    _ok("settings.json is BYTE-IDENTICAL to before the install (third-party survived round-trip)")

    # --- 7. summary -------------------------------------------------------- #
    _hr("ALL SIX SMOKE STEPS PASSED — M11.2 surface is hot")
    print("  install ✓  /self-distill ✓  /discuss ✓  /cross-distill ✓  /inject ✓  uninstall ✓")
    print(f"\n  workspace: {tmp_home}  (auto-cleaned)")
    shutil.rmtree(tmp_home, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
