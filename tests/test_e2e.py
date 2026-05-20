"""M10 end-to-end: the Overview transcript driven through ``oma.cli``.

`test_cli.py` already covers each `_do_*` verb in isolation. This file adds the
*only* thing isolation cannot: that the verbs **compose** — one shared
FakeBank, the real handlers in sequence, every cross-verb state hand-off
asserted (active-session file, auto-registered agent, the four packet types,
the injection ledger row). Plus the two-stage SIGINT exercised from inside the
run-agent leg (the one place `oma` wraps a live child).

Seams are substituted, never the verbs: `_adapter_for` → a fake adapter whose
headless model returns canned JSON (so the real `_agent_json`/forum parser
run); `_pty_spawn` → a recorder; `_discover_local_model` → a fake curator
model (the trap: `_do_cross_distill` calls `curate()` with **no** `model=`, so
the curator resolves a real local model unless this symbol is patched).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from oma import cli
from oma.bank import FakeBank
from oma.capture.models import CanonicalTrace, TraceEvent
from oma.core import clear_packet_cache


@pytest.fixture(autouse=True)
def _env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMA_HOME", str(tmp_path / ".oma"))
    monkeypatch.delenv("OMA_NONINTERACTIVE", raising=False)
    monkeypatch.delenv("OMA_SESSION", raising=False)
    monkeypatch.setenv("OMA_INSTALL_SKILLS", "deny")  # E2E never touches ~/.claude
    from oma.forum import clear_discuss_gate

    clear_discuss_gate()
    clear_packet_cache()


class Scripted:
    """Captured-output io with scripted ``input()`` responses."""

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


class _FakeModel:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, _prompt: str, *, max_tokens: int | None = None) -> str:
        return self.payload


class _FakeAdapter:
    """The fake-adapter seam: a headless model that returns canned JSON, and a
    `capture()` returning a minimal CanonicalTrace so the real
    `oma.capture.persist` writes the `raw` packet (the run-agent leg)."""

    name = "claude"
    binary = "claude"

    def __init__(self, payload: str) -> None:
        self._model = _FakeModel(payload)

    def distill_model(self) -> Any:
        return self._model

    def capture(self) -> CanonicalTrace:
        return CanonicalTrace(
            session_id="S-E2E",
            agent_id="S-E2E/agent-001-claude",
            adapter="claude",
            events=[TraceEvent(ts=0.0, kind="agent", text="ran the parser fix")],
            source_fidelity="pty",
        )


def _args(*argv: str) -> Any:
    return cli._build_parser().parse_args(list(argv))


async def _run(handler: Any, args: Any, bank: FakeBank, *responses: str) -> tuple[int, list[str]]:
    s = Scripted(*responses)
    rc = await handler(args, bank=bank, io=s.io())
    return rc, s.out


async def test_overview_transcript_composes_across_verbs(fake_bank: FakeBank, monkeypatch: pytest.MonkeyPatch) -> None:
    # M11.4: the four knowledge-loop verbs live in ``oma._handlers``; the bash
    # subcommands are gone. The E2E drives them DIRECTLY via the kwargs API —
    # which is exactly how the MCP server (oma._mcp) and any future
    # programmatic caller invokes them.
    from oma import _handlers as h

    adapter = _FakeAdapter(json.dumps(_GOOD))
    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: adapter)
    spawned: list[list[str]] = []
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: spawned.append(argv))

    rcs: list[int] = []

    # 1. start --goal  → writes the active-session file, persists the goal.
    rc, _ = await _run(cli._do_start, _args("start", "S-E2E", "--goal", "ship the parser fix"), fake_bank)
    rcs.append(rc)
    assert cli._read_active() == "S-E2E"
    assert fake_bank._sessions["S-E2E"]["goal"] == "ship the parser fix"

    # 2. register  → resolves sid from the active file, auto-registers seq=1.
    rc, _ = await _run(cli._do_register, _args("register", "claude"), fake_bank)
    rcs.append(rc)

    # 3. <name>  → PTY spawn + real capture pipeline → a raw packet.
    rc = await cli._do_run_agent("claude", ["--help"], None, bank=fake_bank, io=Scripted().io())
    rcs.append(rc)
    assert spawned == [["claude", "--help"]]

    # 4. /self-distill  → agent writes a reflection; human accepts; ★ skipped.
    s4 = Scripted("y", "skip")
    rc = await h.do_self_distill(adapter="claude", bank=fake_bank, io=s4.io())
    rcs.append(rc)
    out = s4.out
    assert any("stored post" in line for line in out)

    # The curator is called with NO model= → patch the discovery symbol (the
    # trap). Cite the real reflection post id with a verbatim quote.
    refl = next(p for p in fake_bank._packets.values() if p.get("type") == "post" and p.get("kind") == "reflection")
    bundle = json.dumps({
        "transferable_insights": [
            {
                "text": "precompile a regex used in a hot tokenizer loop",
                "applies_when": "a parser recompiles the same pattern on every call",
                "does_not_apply_when": "a pattern used exactly once at startup",
                "evidence": [{"post_id": refl["id"], "quote": "recompiled the regex per call"}],
                "confidence": "medium",
            }
        ]
    })
    # `oma.distill.resolve` the *name* is the re-exported function — it shadows
    # the submodule in the package namespace, and `import … as` resolves the
    # shadowed attribute too. Fetch the real submodule from sys.modules.
    import importlib

    _resolve_mod = importlib.import_module("oma.distill.resolve")
    monkeypatch.setattr(_resolve_mod, "_discover_local_model", lambda: _FakeModel(bundle))

    # 5. /discuss --stance disagree  → retrieval-before-post enforced internally.
    s5 = Scripted("y")
    rc = await h.do_discuss(adapter="claude", stance="disagree", bank=fake_bank, io=s5.io())
    rcs.append(rc)

    # 6. /cross-distill  → goal set ⇒ per_goal; curator resolves the fake model.
    s6 = Scripted()
    rc = await h.do_cross_distill(bank=fake_bank, io=s6.io())
    rcs.append(rc)
    assert any("curated per_goal" in line for line in s6.out)

    # 7. /inject  → picks the last distill, preview, [y/n] → injection row.
    s7 = Scripted("y")
    rc = await h.do_inject(bank=fake_bank, io=s7.io())
    rcs.append(rc)
    assert any("injections row written" in line for line in s7.out)

    # 8. end  → marks ended, ★ skipped, clears the active file.
    rc, _ = await _run(cli._do_end, _args("end"), fake_bank, "skip")
    rcs.append(rc)

    # --- the discriminating cross-verb assertions ---------------------------
    assert rcs == [0] * 8  # every leg succeeded

    assert fake_bank._sessions["S-E2E"]["status"] == "ended"
    assert fake_bank._sessions["S-E2E"]["goal"] == "ship the parser fix"

    agents = await fake_bank.list_agents("S-E2E")
    assert len(agents) == 1 and agents[0]["seq"] == 1  # auto-registered exactly once

    by_type: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for p in fake_bank._packets.values():
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
        if p["type"] == "post":
            kinds[p.get("kind", "?")] = kinds.get(p.get("kind", "?"), 0) + 1
    assert by_type.get("raw") == 1
    assert by_type.get("distill") == 1
    assert kinds.get("reflection") == 1 and kinds.get("reply") == 1

    injections = await fake_bank.list_injections()
    assert len(injections) == 1 and injections[0]["target_session_id"] == "S-E2E"

    assert not cli.active_session_path().exists()  # end cleared the active file


def test_sigint_two_stage_during_run_agent_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two-stage child SIGINT, exercised from inside the run-agent leg —
    the one place `oma` wraps a live child (datasmith precedent)."""
    forces: list[bool] = []
    monkeypatch.setattr("oma.adapters.terminate_all_agents", lambda *, force=False: forces.append(force))

    class _Exit(SystemExit):
        pass

    monkeypatch.setattr(cli.os, "_exit", lambda code: (_ for _ in ()).throw(_Exit(code)))
    monkeypatch.setattr(cli, "_sigint_count", 0)
    # First Ctrl-C arrives *while the wrapped agent is running* (inside the PTY
    # leg): the handler SIGTERMs the child, then KeyboardInterrupt unwinds.
    monkeypatch.setattr(cli, "_pty_spawn", lambda argv, tee=None: cli._sigint_handler(2, None))
    # M11.4: `_adapter_for` lives in `oma._handlers` now.
    from oma import _handlers as h

    monkeypatch.setattr(h, "_adapter_for", lambda *a, **k: _FakeAdapter("{}"))

    import asyncio

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(cli._do_run_agent("claude", [], "S-SIG", bank=FakeBank(), io=Scripted().io()))
    assert forces == [False]  # stage 1: graceful SIGTERM of the child

    with pytest.raises(_Exit):  # stage 2: a second Ctrl-C → SIGKILL + hard exit
        cli._sigint_handler(2, None)
    assert forces == [False, True]
