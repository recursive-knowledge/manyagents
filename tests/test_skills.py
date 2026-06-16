"""M11 tests for ``manyagent._skills`` — the ``Skill`` registry the MCP server and
the per-adapter SKILL.md renderers both consume.

The headline invariants: the four verbs are the single source of truth (order +
slugs); ``register_all`` exposes exactly the six tools deduped (``commit_post``
is shared by self-distill and discuss); and one dialect-substituted procedure
body renders correctly for each host's tokens (Claude / Codex / Gemini).
"""

from __future__ import annotations

from typing import Any

from manyagent._skills import (
    REGISTRY,
    CrossDistill,
    Dialect,
    Discuss,
    Inject,
    SelfDistill,
    Skill,
    commit_post,
    cross_distill,
    discuss_draft,
    inject_commit,
    inject_preview,
    register_all,
    self_distill_draft,
)

_ALL_TOOLS = {
    "self_distill_draft",
    "discuss_draft",
    "commit_post",
    "cross_distill",
    "inject_preview",
    "inject_commit",
}


# --------------------------------------------------------------------------- #
# registry shape — order + slugs are load-bearing (the installers iterate it)
# --------------------------------------------------------------------------- #


def test_registry_order_and_slugs() -> None:
    assert [s.slug for s in REGISTRY] == ["self-distill", "discuss", "cross-distill", "inject"]


def test_registry_types() -> None:
    assert [type(s) for s in REGISTRY] == [SelfDistill, Discuss, CrossDistill, Inject]
    assert all(isinstance(s, Skill) for s in REGISTRY)


def test_registry_slugs_unique() -> None:
    slugs = [s.slug for s in REGISTRY]
    assert len(slugs) == len(set(slugs))


def test_every_skill_declares_identity_and_blurb() -> None:
    for s in REGISTRY:
        assert s.slug and s.title and s.description and s.blurb and s.allowed_tool
        assert s.mcp_tools  # at least one tool


# --------------------------------------------------------------------------- #
# tool wiring — allowed/gated tools, sharing, dedup
# --------------------------------------------------------------------------- #


def test_allowed_and_gated_tools_are_owned_by_the_skill() -> None:
    for s in REGISTRY:
        names = {fn.__name__ for fn in s.mcp_tools}
        assert s.allowed_tool in names
        if s.gated_tool is not None:
            assert s.gated_tool in names


def test_gated_tool_none_only_for_cross_distill() -> None:
    ungated = [s.slug for s in REGISTRY if s.gated_tool is None]
    assert ungated == ["cross-distill"]  # the curator is mechanical; gate fires at inject


def test_allowed_tool_is_never_the_gated_tool() -> None:
    # The un-gated tool the host may call freely must differ from the tool whose
    # permission prompt IS the human gate (else the gate never fires).
    for s in REGISTRY:
        assert s.allowed_tool != s.gated_tool


def test_commit_post_is_shared_by_self_distill_and_discuss() -> None:
    self_distill = next(s for s in REGISTRY if s.slug == "self-distill")
    discuss = next(s for s in REGISTRY if s.slug == "discuss")
    assert commit_post in self_distill.mcp_tools
    assert commit_post in discuss.mcp_tools


def test_register_all_returns_six_deduped_tools() -> None:
    class _FakeApp:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def tool(self) -> Any:
            def deco(fn: Any) -> Any:
                self.calls.append(fn.__name__)
                return fn

            return deco

    app = _FakeApp()
    tools = register_all(app)
    # Six distinct tools exposed under their function names …
    assert set(tools) == _ALL_TOOLS
    assert len(tools) == 6
    # … and each registered on the app exactly once (commit_post not twice,
    # even though both self-distill and discuss list it).
    assert app.calls.count("commit_post") == 1
    assert sorted(app.calls) == sorted(_ALL_TOOLS)
    assert tools["self_distill_draft"] is self_distill_draft
    assert tools["commit_post"] is commit_post


def test_union_of_skill_tools_is_exactly_the_six() -> None:
    union = {fn.__name__ for s in REGISTRY for fn in s.mcp_tools}
    assert union == _ALL_TOOLS
    # The concrete callables are the module-level impls (so _mcp can re-export).
    impls = {self_distill_draft, discuss_draft, commit_post, cross_distill, inject_preview, inject_commit}
    assert {fn for s in REGISTRY for fn in s.mcp_tools} == impls


# --------------------------------------------------------------------------- #
# dialect rendering — one procedure body, per-host tokens
# --------------------------------------------------------------------------- #

_CLAUDE = Dialect(
    tool_ref=lambda n: f"mcp__manyagent__{n}",
    invocation=lambda s: f"/{s}",
    args="$ARGUMENTS",
    gate="Claude Code's permission prompt",
)
_CODEX = Dialect(
    tool_ref=lambda n: f"manyagent.{n}",
    invocation=lambda s: f"$manyagent-{s}",
    args="the user's request",
    gate="Codex's per-tool approval prompt",
)
_GEMINI = Dialect(
    tool_ref=lambda n: f"mcp__manyagent__{n}",
    invocation=lambda s: f"/{s}",
    args="{{args}}",
    gate="Gemini's permission UI",
)


def test_bodies_never_leave_unrendered_placeholders() -> None:
    for s in REGISTRY:
        for d in (_CLAUDE, _CODEX, _GEMINI):
            body = s.body(d)
            assert "{d." not in body  # no f-string field leaked
            assert "d.tool_ref" not in body and "d.invocation" not in body


def test_self_distill_body_renders_claude_tokens() -> None:
    body = SelfDistill().body(_CLAUDE)
    assert "mcp__manyagent__self_distill_draft" in body  # step 1, allowed tool
    assert "mcp__manyagent__commit_post" in body  # step 4, the gated tool
    assert "$ARGUMENTS" in body
    assert "Claude Code's permission prompt" in body


def test_self_distill_body_renders_codex_tokens() -> None:
    body = SelfDistill().body(_CODEX)
    assert "manyagent.self_distill_draft" in body
    assert "manyagent.commit_post" in body
    assert "mcp__manyagent__" not in body  # codex uses dotted refs, not mcp__ prefix
    assert "the user's request" in body
    assert "Codex's per-tool approval prompt" in body


def test_discuss_body_cross_references_self_distill_invocation() -> None:
    # /discuss tells the user to run self-distill first — in the host's own syntax.
    assert "`/self-distill`" in Discuss().body(_CLAUDE)
    assert "$manyagent-self-distill" in Discuss().body(_CODEX)


def test_cross_distill_quotes_literal_sentinel_but_dialect_instruction() -> None:
    body = CrossDistill().body(_CODEX)
    # The tool's literal sentinel stays verbatim (it's what the tool returns) …
    assert '"Run /self-distill first!"' in body
    # … but the instruction to the user uses the host invocation.
    assert "tell the user to run `$manyagent-self-distill` first" in body
    # cross-distill points at the inject verb in the host's syntax.
    assert "$manyagent-inject @<bundle_id>" in body


def test_inject_body_renders_gemini_args_and_tools() -> None:
    body = Inject().body(_GEMINI)
    assert "{{args}}" in body  # gemini's arg placeholder, literal in the prompt
    assert "mcp__manyagent__inject_preview" in body
    assert "mcp__manyagent__inject_commit" in body
    assert "Gemini's permission UI" in body


def test_real_adapter_dialects_match_their_hosts() -> None:
    # The dialects the installers actually use produce the right tokens.
    from manyagent.adapters.skills.claude import _DIALECT as claude_d
    from manyagent.adapters.skills.codex import _DIALECT as codex_d
    from manyagent.adapters.skills.gemini import _DIALECT as gemini_d

    assert claude_d.tool_ref("commit_post") == "mcp__manyagent__commit_post"
    assert claude_d.invocation("self-distill") == "/self-distill"
    assert codex_d.tool_ref("commit_post") == "manyagent.commit_post"
    assert codex_d.invocation("self-distill") == "$manyagent-self-distill"
    assert gemini_d.tool_ref("commit_post") == "mcp__manyagent__commit_post"
    assert gemini_d.args == "{{args}}"
