"""M11.4 tests for ``manyagent._handlers`` — the four knowledge-loop verbs.

These moved out of ``manyagent.cli`` when M11.4 ripped the bash slash subcommands;
the same C1 / retrieval / accept-reject behaviour is now tested by calling
the handlers directly with kwargs (no argparse Namespace). The verbs are
exposed to users **inside the wrapped agent** via the MCP server +
per-adapter skills — this module covers the underlying handler functions
that both the in-agent surface (via ``manyagent._mcp``) and any future
programmatic caller rely on.

The headline case stays **C1**: a rejected/parser-refused post is NOT
persisted and the record never carries ``preference``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from manyagent import _handlers as h
from manyagent.bank import FakeBank


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_HOME", str(tmp_path / ".manyagent"))
    monkeypatch.delenv("MANYAGENT_NONINTERACTIVE", raising=False)
    monkeypatch.delenv("MANYAGENT_SESSION", raising=False)
    monkeypatch.setenv("MANYAGENT_INSTALL_SKILLS", "deny")
    from manyagent.forum import clear_discuss_gate

    clear_discuss_gate()


class Scripted:
    def __init__(self, *responses: str) -> None:
        self._r = list(responses)
        self.out: list[str] = []

    def __call__(self, _prompt: str = "") -> str:
        return self._r.pop(0)

    def io(self) -> tuple[Any, Any]:
        return (self, self.out.append)


_GOOD = {
    "load_bearing_assumption": "the tokenize() hot loop recompiled the regex per call; precompiling fixed it",
    "evidence": "verbatim from trace: 'cumtime 4.2s in tokenize()'",
    "evidence_ref": None,
    "proposed_next": "hoist the compiled pattern to scanner.py module scope",
    "predicted_outcome": "parse throughput ~1.8x; test_parse_speed passes",
    "confidence": "medium",
}


class FakeModel:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.prompts.append(prompt)
        return self.payload


class FakeAdapter:
    name = "claude"
    binary = "claude"

    def __init__(self, payload: str = "") -> None:
        self._model = FakeModel(payload) if payload else None
        self.injected: list[str] = []

    def distill_model(self) -> Any:
        return self._model

    def inject(self, context: str) -> None:
        self.injected.append(context)

    def is_available(self) -> bool:
        return True


def _patch_adapter(monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: adapter)


async def _seed_session(bank: FakeBank, *, sid: str = "S1", goal: str | None = "speed") -> None:
    await bank.put_session(sid, goal=goal)
    from manyagent.cli import _write_active

    _write_active(sid)


# --------------------------------------------------------------------------- #
# C1: rejected / parser-refused /self-distill post is NEVER persisted
# --------------------------------------------------------------------------- #


async def test_c1_parser_refused_post_not_persisted(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_session(fake_bank)
    bad = dict(_GOOD)
    del bad["proposed_next"]  # missing required field
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(bad)))
    s = Scripted("y", "skip")
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=s.io())
    assert rc == 1  # parser refused
    assert await fake_bank.list_packets(type="post") == []  # NOT persisted (C1)


async def test_c1_human_reject_not_persisted_no_preference(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    s = Scripted("n")  # reject
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=s.io())
    assert rc == 1
    posts = await fake_bank.list_packets(type="post")
    assert posts == []  # NOT persisted, no `preference` field anywhere (C1)


async def test_self_distill_accept_stores_post_with_star_no_preference(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    s = Scripted("4")  # single commit gate: a bare 1-5 commits WITH that star
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=s.io())
    assert rc == 0
    [p] = await fake_bank.list_packets(type="post")
    assert p["rating"] == 4 and p["kind"] == "reflection"
    assert "preference" not in p or p.get("preference") is None  # C1


async def test_noninteractive_self_distill_accepts_unrated(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MANYAGENT_NONINTERACTIVE", "1")
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted().io())
    assert rc == 0  # auto-accept under noninteractive (parser already gated quality)
    [p] = await fake_bank.list_packets(type="post")
    assert p.get("rating") is None  # noninteractive ⇒ unrated


# --------------------------------------------------------------------------- #
# /cross-distill — the exact zero-posts sentinel
# --------------------------------------------------------------------------- #


async def test_cross_distill_zero_posts_exact_sentinel(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank)
    s = Scripted()
    rc = await h.do_cross_distill(bank=fake_bank, io=s.io())
    assert rc == 1
    assert s.out[-1] == "Run /self-distill first!"  # the exact text


# --------------------------------------------------------------------------- #
# /inject — quarantine refusal + preview + ledger + noninteractive deny
# --------------------------------------------------------------------------- #


async def _seed_distill(bank: FakeBank, *, quarantined: bool = False) -> str:
    pid = "curator/abc123"
    await bank.put_packet({
        "id": pid,
        "type": "distill",
        "agent_id": "curator",
        "scope": "per_goal",
        "goal": "speed",
        "bundle": {"confirmed_constraints": [{"text": "x", "applies_when": "a", "does_not_apply_when": "b"}]},
        "parents": ["S1/p1"],
        "quarantined": quarantined,
    })
    return pid


async def test_inject_quarantined_refused_before_preview(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank)
    pid = await _seed_distill(fake_bank, quarantined=True)
    s = Scripted()
    rc = await h.do_inject(packet=f"@{pid}", bank=fake_bank, io=s.io())
    assert rc == 1
    assert any("quarantined" in line for line in s.out)
    assert "--- inject preview ---" not in s.out  # refused BEFORE preview


async def test_inject_preview_then_yes_writes_ledger_row(fake_bank: FakeBank) -> None:
    await _seed_session(fake_bank)
    pid = await _seed_distill(fake_bank)
    s = Scripted("y")
    rc = await h.do_inject(packet=pid, bank=fake_bank, io=s.io())
    assert rc == 0
    [row] = await fake_bank.list_injections()
    assert row["packet_id"] == pid and row["target_session_id"] == "S1"


async def test_inject_noninteractive_denied_no_ledger(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MANYAGENT_NONINTERACTIVE", "1")
    await _seed_session(fake_bank)
    pid = await _seed_distill(fake_bank)
    s = Scripted()
    rc = await h.do_inject(packet=pid, bank=fake_bank, io=s.io())
    assert rc == 1  # deny-by-default under noninteractive (Open-Q §B5)
    assert await fake_bank.list_injections() == []


# --------------------------------------------------------------------------- #
# /discuss — retrieval-before-post, then accept-store-no-preference
# --------------------------------------------------------------------------- #


async def test_discuss_happy_path_stores_reply_no_preference(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    # First a reflection (so /discuss has something to engage).
    await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    # Then the reply.
    rc = await h.do_discuss(adapter="claude", stance="agree", bank=fake_bank, io=Scripted("y").io())
    assert rc == 0
    posts = await fake_bank.list_packets(type="post")
    kinds = {p["kind"] for p in posts}
    assert kinds == {"reflection", "reply"}
    for p in posts:
        assert "preference" not in p or p.get("preference") is None  # C1


async def test_discuss_retrieval_before_post_no_prior_refused(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    s = Scripted()
    rc = await h.do_discuss(adapter="claude", stance="agree", bank=fake_bank, io=s.io())
    assert rc == 1
    assert any("no related posts" in line for line in s.out)
    assert await fake_bank.list_packets(type="post") == []  # nothing persisted (C1)


async def test_discuss_packet_not_in_retrieved_refused(
    fake_bank: FakeBank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_session(fake_bank)
    _patch_adapter(monkeypatch, FakeAdapter(json.dumps(_GOOD)))
    # Seed a reflection so retrieve is non-empty.
    await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    # Reference a packet id NOT in the retrieved set.
    s = Scripted()
    rc = await h.do_discuss(
        adapter="claude",
        stance="agree",
        packet="S1/not-retrieved",
        bank=fake_bank,
        io=s.io(),
    )
    assert rc == 1
    assert any("not among the retrieved posts" in line for line in s.out)


# --------------------------------------------------------------------------- #
# trace-grounded prompts (2026-06-10): once the wrapped agent exits, the
# session lives in the bound transcript / raw packet, not in any model's head
# — the headless distill prompt must carry it (the "no parseable JSON" fix)
# --------------------------------------------------------------------------- #


def _write_binding(
    tmp_path: Any,
    *,
    sid: str = "S1",
    harness_id: str = "H1",
    transcript_lines: list[dict[str, Any]],
    ts: float = 100.0,
) -> None:
    tp = tmp_path / f"transcript-{harness_id}.jsonl"
    tp.write_text("\n".join(json.dumps(ln) for ln in transcript_lines), encoding="utf-8")
    bindings = Path(os.environ["MANYAGENT_HOME"]).expanduser() / "bindings"
    bindings.mkdir(parents=True, exist_ok=True)
    rec = {
        "manyagent_session": sid,
        "event": "SessionEnd",
        "harness_session_id": harness_id,
        "transcript_path": str(tp),
        "ts": ts,
    }
    with (bindings / f"{sid}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _dialogue(*texts: tuple[str, str]) -> list[dict[str, Any]]:
    """(role, text) pairs → harness-transcript-shaped jsonl records."""
    return [{"type": role, "message": {"content": text}} for role, text in texts]


async def test_self_distill_prompt_grounded_in_bound_transcript(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    await _seed_session(fake_bank)
    _write_binding(
        tmp_path,
        transcript_lines=_dialogue(("user", "profile the tokenizer"), ("assistant", "cumtime 4.2s in tokenize()")),
    )
    adapter = FakeAdapter(json.dumps(_GOOD))
    _patch_adapter(monkeypatch, adapter)
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    assert rc == 0
    [prompt] = adapter._model.prompts  # type: ignore[union-attr]
    assert "--- BEGIN TRACE ---" in prompt
    assert "user: profile the tokenizer" in prompt
    assert "agent: cumtime 4.2s in tokenize()" in prompt


async def test_self_distill_since_scopes_to_this_runs_transcripts(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A `--resume`d/earlier run's transcript must not be what gets distilled:
    `since` (the run-start clock) excludes bindings from before this run."""
    await _seed_session(fake_bank)
    _write_binding(tmp_path, harness_id="OLD", ts=100.0, transcript_lines=_dialogue(("user", "the OLD run")))
    _write_binding(tmp_path, harness_id="NEW", ts=200.0, transcript_lines=_dialogue(("user", "the NEW run")))
    adapter = FakeAdapter(json.dumps(_GOOD))
    _patch_adapter(monkeypatch, adapter)
    rc = await h.do_self_distill(adapter="claude", since=150.0, bank=fake_bank, io=Scripted("4").io())
    assert rc == 0
    [prompt] = adapter._model.prompts  # type: ignore[union-attr]
    assert "the NEW run" in prompt and "the OLD run" not in prompt


async def test_self_distill_falls_back_to_raw_packet(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_session(fake_bank)
    await fake_bank.put_packet({"id": "S1/raw00001", "type": "raw", "session_id": "S1", "agent_id": "S1/a1"})
    body = json.dumps({"events": [{"ts": 0.0, "kind": "system", "text": "PTY tee: rg failed with exit 2"}]})
    await fake_bank.put_trace("S1/raw00001", body, scrub_version="v1", complete=True)
    adapter = FakeAdapter(json.dumps(_GOOD))
    _patch_adapter(monkeypatch, adapter)
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    assert rc == 0
    [prompt] = adapter._model.prompts  # type: ignore[union-attr]
    assert "rg failed with exit 2" in prompt


async def test_trace_context_scrubbed_and_bounded(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("MANYAGENT_DISTILL_CONTEXT_MAX_BYTES", "600")
    await _seed_session(fake_bank)
    secret = "sk-ant-api03-" + "A" * 24
    _write_binding(
        tmp_path,
        transcript_lines=_dialogue(("user", f"key is {secret}"), ("assistant", "x " * 2000)),
    )
    adapter = FakeAdapter(json.dumps(_GOOD))
    _patch_adapter(monkeypatch, adapter)
    await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    [prompt] = adapter._model.prompts  # type: ignore[union-attr]
    assert secret not in prompt and "[REDACTED:anthropic]" in prompt
    assert "elided for context budget" in prompt  # head+tail bounded, gap explicit


async def test_trace_context_prefers_harness_rendition_with_tool_turns(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The mined rendition wins over the transcript flatten (2026-06-11): it
    is the only source carrying tool turns — a session whose story is its
    tool activity otherwise distills into narration-only noise."""
    await _seed_session(fake_bank)
    await fake_bank.put_packet({"id": "S1/raw00001", "type": "raw", "session_id": "S1", "agent_id": "S1/a1"})
    mined = {
        "miner_version": "claude-v1",
        "segments": [
            {
                "harness_session_id": "H1",
                "transcript": "t.jsonl",
                "turns": [
                    {"role": "user", "text": "profile it"},
                    {"role": "user", "text": "Base directory for this skill: /x/y"},  # scaffold — dropped
                    {"role": "tool", "text": "", "tool": {"name": "Bash", "input_preview": '{"command": "cProfile"}'}},
                    {"role": "assistant", "text": "cumtime 4.2s in tokenize()"},
                ],
            }
        ],
    }
    await fake_bank.put_rendition("S1/raw00001", "harness", json.dumps(mined))
    _write_binding(tmp_path, transcript_lines=_dialogue(("user", "TRANSCRIPT-ONLY LINE")))
    adapter = FakeAdapter(json.dumps(_GOOD))
    _patch_adapter(monkeypatch, adapter)
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    assert rc == 0
    [prompt] = adapter._model.prompts  # type: ignore[union-attr]
    assert "tool: Bash" in prompt and "cProfile" in prompt  # tool turns travel
    assert "cumtime 4.2s in tokenize()" in prompt
    assert "TRANSCRIPT-ONLY LINE" not in prompt  # rendition wins over flatten
    assert "Base directory for this skill" not in prompt  # scaffold dropped


async def test_trace_context_drops_harness_scaffold_turns(
    fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Slash-command envelopes and injected skill bodies are harness plumbing,
    not dialogue — left in, they dominate a short session and the distiller
    reflects on manyagent itself (observed 2026-06-11)."""
    await _seed_session(fake_bank)
    _write_binding(
        tmp_path,
        transcript_lines=_dialogue(
            ("user", "<command-message>self-distill</command-message>\n<command-name>/self-distill</command-name>"),
            ("user", "Base directory for this skill: /home/u/.claude/skills/self-distill\n\n# procedure"),
            ("user", "fix the tokenizer"),
            ("assistant", "hoisted the compiled regex"),
        ),
    )
    adapter = FakeAdapter(json.dumps(_GOOD))
    _patch_adapter(monkeypatch, adapter)
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    assert rc == 0
    [prompt] = adapter._model.prompts  # type: ignore[union-attr]
    assert "fix the tokenizer" in prompt and "hoisted the compiled regex" in prompt
    assert "<command-message>" not in prompt and "Base directory for this skill" not in prompt


async def test_self_distill_no_trace_no_section(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_session(fake_bank)
    adapter = FakeAdapter(json.dumps(_GOOD))
    _patch_adapter(monkeypatch, adapter)
    await h.do_self_distill(adapter="claude", bank=fake_bank, io=Scripted("4").io())
    [prompt] = adapter._model.prompts  # type: ignore[union-attr]
    assert "BEGIN TRACE" not in prompt  # nothing captured → no fabricated section


# --------------------------------------------------------------------------- #
# register gate: minting an agent row requires a real, runnable adapter
# (decision 2026-06-10 — `manyagent register agent` used to persist a Bank row +
# viewer URL for a name that resolves to nothing and isn't on PATH)
# --------------------------------------------------------------------------- #


async def test_resolve_agent_unknown_adapter_persists_nothing(
    fake_bank: FakeBank,
    adapter_gate: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path / "adapters"))
    monkeypatch.setattr(h, "_validate_adapter", adapter_gate)  # the real gate
    await _seed_session(fake_bank)
    with pytest.raises(SystemExit, match="unknown adapter 'agent'"):
        await h._resolve_agent("S1", "agent", bank=fake_bank)
    assert await fake_bank.list_agents("S1") == []  # no phantom row


async def test_validate_adapter_binary_off_path_exits(
    adapter_gate: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import manyagent.adapters.base as base

    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path / "adapters"))
    monkeypatch.setattr(base.shutil, "which", lambda _b: None)
    with pytest.raises(SystemExit, match="not on PATH"):
        adapter_gate("claude")


async def test_validate_adapter_ok_when_binary_present(
    adapter_gate: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import manyagent.adapters.base as base

    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path / "adapters"))
    monkeypatch.setattr(base.shutil, "which", lambda _b: "/usr/bin/claude")
    adapter_gate("claude")  # builtin + binary on PATH: no raise


async def test_resolve_agent_existing_row_skips_gate(
    fake_bank: FakeBank, adapter_gate: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-registered agent resolves without re-validating — the gate
    guards minting only, so an ended-CLI session can still be inspected."""
    monkeypatch.setattr(h, "_validate_adapter", adapter_gate)
    await _seed_session(fake_bank)
    await fake_bank.put_agent("S1/agent-001-claude", session_id="S1", adapter="claude", seq=1)
    assert await h._resolve_agent("S1", "claude", bank=fake_bank) == "S1/agent-001-claude"
