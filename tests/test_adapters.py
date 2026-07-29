"""M5 tests for manyagent.adapters — ABC enforcement, registry resolution order,
the PromptPrefixInjector round-trip, per-builtin conformance against sample
native traces, capture()-is-raw (scrub happens in manyagent.capture), and the
distill_model() provider seam (manyagent.adapters.md Verification)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from manyagent.adapters import Adapter, AdapterError, NotInstalled, available, resolve, terminate_all_agents
from manyagent.adapters import base as adapters_base
from manyagent.adapters.base import PromptPrefixInjector
from manyagent.adapters.builtin.claude import ClaudeAdapter
from manyagent.adapters.builtin.codex import CodexAdapter
from manyagent.adapters.builtin.gemini import GeminiAdapter
from manyagent.adapters.builtin.qwen import QwenAdapter
from manyagent.adapters.registry import _hub_fetch
from manyagent.capture import validate
from manyagent.utils import provider

_SAMPLES = Path(__file__).parent / "fixtures" / "adapter_samples"
_BUILTIN_FIDELITY = {ClaudeAdapter: "structured", CodexAdapter: "structured", GeminiAdapter: "pty"}


# --------------------------------------------------------------------------- #
# the ABC contract
# --------------------------------------------------------------------------- #


def test_abc_rejects_subclass_missing_a_method() -> None:
    class Bad(Adapter):  # missing invoke/capture/inject/retrieve
        name = "bad"
        binary = "bad"
        version = "0"

    with pytest.raises(TypeError, match="abstract"):
        Bad()  # type: ignore[abstract]


def test_name_binary_version_are_class_attrs_no_instantiation() -> None:
    assert (ClaudeAdapter.name, ClaudeAdapter.binary) == ("claude", "claude")
    assert (CodexAdapter.name, GeminiAdapter.name, QwenAdapter.name) == ("codex", "gemini", "qwen")
    assert ClaudeAdapter.source_fidelity == "structured" and GeminiAdapter.source_fidelity == "pty"


def test_qwen_stub_satisfies_abc_but_operations_raise() -> None:
    q = QwenAdapter()  # instantiable: stub still honors the full contract
    assert isinstance(q, Adapter)
    with pytest.raises(NotInstalled):
        q.invoke([])
    with pytest.raises(NotInstalled):
        q.capture()


def test_prompt_prefix_injector_round_trip_and_clears() -> None:
    class A(PromptPrefixInjector):
        pass

    a = A()
    assert a.retrieve() is None
    a.inject("PRIOR CONTEXT")
    assert a.retrieve() == "PRIOR CONTEXT"
    assert a.retrieve() is None  # cleared after one read
    a.inject("ctx")
    assert a._consume_prefix("do X").startswith("ctx\n\ndo X")


def test_terminate_all_agents_is_safe_with_no_procs() -> None:
    terminate_all_agents()  # no tracked procs → no error
    terminate_all_agents(force=True)


# --------------------------------------------------------------------------- #
# registry resolution order: local > builtin > hub
# --------------------------------------------------------------------------- #


def test_resolve_returns_builtin_class() -> None:
    assert resolve("claude") is ClaudeAdapter
    assert resolve("qwen") is QwenAdapter
    assert available() == ["claude", "codex", "gemini", "qwen"]


def _write_local_adapter(root: Path, name: str, marker: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(
        "from manyagent.adapters.base import Adapter\n"
        "import subprocess\n"
        f"class _Local(Adapter):\n"
        f"    name = {name!r}\n"
        f"    binary = {marker!r}\n"
        "    def invoke(self, args): raise RuntimeError\n"
        "    def capture(self): raise RuntimeError\n"
        "    def inject(self, context): ...\n"
        "    def retrieve(self): return None\n"
        "ADAPTER = _Local\n"
    )


def test_local_install_overrides_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path))
    _write_local_adapter(tmp_path, "claude", "local-claude-bin")
    resolved = resolve("claude")
    assert resolved is not ClaudeAdapter and resolved.binary == "local-claude-bin"


def test_unknown_adapter_with_offline_hub_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path))
    assert _hub_fetch("acme") is None  # offline default
    with pytest.raises(AdapterError, match="no adapter 'acme'"):
        resolve("acme")


def test_hub_not_found_then_installed_then_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path))

    def fake_hub(name: str) -> Path | None:
        _write_local_adapter(tmp_path, name, f"hub-{name}")
        return tmp_path / name

    monkeypatch.setattr("manyagent.adapters.registry._hub_fetch", fake_hub)
    resolved = resolve("acme")  # not local, not builtin → hub installs → local load
    assert resolved.binary == "hub-acme" and resolved.name == "acme"


# --------------------------------------------------------------------------- #
# per-builtin conformance: sample native trace → CanonicalTrace, correct fidelity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("cls", "fidelity"), list(_BUILTIN_FIDELITY.items()))
def test_builtin_sample_normalizes_with_correct_fidelity(cls: type[Adapter], fidelity: str) -> None:
    a = cls(session_id="S", agent_id="S/agent-001-x", trace_source=_SAMPLES / f"{cls.name}.json")  # type: ignore[call-arg]
    trace = a.capture()
    validate(trace)  # the M5 obligation: conformant + fidelity matches
    assert trace.source_fidelity == fidelity == cls.source_fidelity
    assert trace.adapter == cls.name and trace.session_id == "S"
    assert trace.events and all(e.kind in {"user", "agent", "tool_call", "tool_result", "system"} for e in trace.events)
    assert trace.bytes_in > 0 and trace.bytes_out == 0  # raw: manyagent.capture sets bytes_out


def test_structured_builtins_map_turn_structure() -> None:
    c = ClaudeAdapter(session_id="S", agent_id="S/a", trace_source=_SAMPLES / "claude.json")
    kinds = {e.kind for e in c.capture().events}
    assert {"user", "agent", "tool_call", "tool_result"} <= kinds  # claude turns preserved

    x = CodexAdapter(session_id="S", agent_id="S/a", trace_source=_SAMPLES / "codex.json")
    xk = {e.kind for e in x.capture().events}
    assert "user" in xk and "agent" in xk and "tool_call" in xk


def test_pty_builtin_is_unstructured_single_blob() -> None:
    g = GeminiAdapter(session_id="S", agent_id="S/a", trace_source=_SAMPLES / "gemini.json")
    evs = g.capture().events
    assert len(evs) == 1 and evs[0].kind == "system"  # pty: no tool-call structure
    assert "Peak RSS" in evs[0].text


def test_capture_is_raw_scrub_happens_in_manyagent_capture() -> None:
    # The claude sample embeds a fake key; capture() must NOT scrub it (that is
    # manyagent.capture's centralized job — a careless adapter cannot weaken safety).
    c = ClaudeAdapter(session_id="S", agent_id="S/a", trace_source=_SAMPLES / "claude.json")
    body = " ".join(e.text for e in c.capture().events)
    assert "sk-proj-LEAKLEAKLEAKLEAKLEAKLEAK" in body  # raw, faithful, un-scrubbed


def test_capture_without_trace_source_errors() -> None:
    with pytest.raises(AdapterError, match="no trace_source"):
        ClaudeAdapter(session_id="S", agent_id="S/a").capture()


async def test_capture_then_manyagent_capture_persist_scrubs_and_lands_raw_packet(fake_bank: object) -> None:
    from manyagent.bank import FakeBank
    from manyagent.capture import persist

    bank = fake_bank if isinstance(fake_bank, FakeBank) else FakeBank()
    await bank.put_session("S")
    c = ClaudeAdapter(session_id="S", agent_id="S/agent-001-claude", trace_source=_SAMPLES / "claude.json")
    pid = await persist(c.capture(), bank=bank)

    pkt = await bank.get_packet(pid)
    assert pkt is not None and pkt["type"] == "raw"
    tr = await bank.get_trace(pid)
    assert tr is not None and "sk-proj-LEAKLEAKLEAKLEAKLEAKLEAK" not in tr["body"]  # scrubbed centrally
    assert json.loads(tr["body"])["scrub_report"]["counts"].get("env_kv", 0) >= 1


# --------------------------------------------------------------------------- #
# distill_model() — the manyagent.utils.provider seam
# --------------------------------------------------------------------------- #


def test_distill_model_none_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters_base.shutil, "which", lambda _b: None)
    assert ClaudeAdapter().distill_model() is None
    monkeypatch.delenv("MANYAGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MANYAGENT_LLM_MODEL", raising=False)
    with pytest.raises(provider.ProviderUnavailable):
        provider.resolve(adapter=ClaudeAdapter())  # no model, no OpenAI fallback


def test_distill_model_is_a_provider_when_binary_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters_base.shutil, "which", lambda b: f"/usr/bin/{b}")
    model = CodexAdapter().distill_model()
    assert model is not None and callable(model.complete) and callable(model.rate_limit_signal)  # type: ignore[attr-defined]
    p = provider.resolve(adapter=CodexAdapter())  # picked up via the adapter hook
    assert callable(p.complete) and callable(p.rate_limit_signal)


def test_headless_complete_strips_manyagent_session_from_child_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The distiller is NOT the wrapped session: with MANYAGENT_SESSION inherited,
    the user-scope manyagent._hook inside the spawned CLI would bind the distiller's
    own harness session into the session's bindings file (the 2026-06-10
    wrong-session contamination) and receive its inject stash."""
    import manyagent.adapters.builtin as builtin

    monkeypatch.setenv("MANYAGENT_SESSION", "FA04-ESNF")
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> tuple[int, str, str, float]:
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return 0, "ok", "", 0.0

    monkeypatch.setattr(builtin, "run_agent_subprocess", fake_run)
    out = builtin._HeadlessModel("claude", ["claude", "-p"]).complete("hi")
    assert out == "ok"
    env = captured["env"]
    assert isinstance(env, dict) and "MANYAGENT_SESSION" not in env
    assert env.get("PATH")  # the rest of the environment is preserved


def test_headless_complete_runs_in_hermetic_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """The distiller runs from an EMPTY temp cwd (2026-06-11): in the repo
    cwd the spawned CLI loads its own project context (CLAUDE.md, git status)
    and blends it into the post as if it were session evidence."""
    import os as _os

    import manyagent.adapters.builtin as builtin

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> tuple[int, str, str, float]:
        captured["cwd"] = kw.get("cwd")
        captured["cwd_listing"] = _os.listdir(str(kw.get("cwd")))
        return 0, "ok", "", 0.0

    monkeypatch.setattr(builtin, "run_agent_subprocess", fake_run)
    assert builtin._HeadlessModel("claude", ["claude", "-p"]).complete("hi") == "ok"
    cwd = captured["cwd"]
    assert isinstance(cwd, str) and cwd != _os.getcwd()
    assert captured["cwd_listing"] == []  # empty — no CLAUDE.md / .git to load
    assert not Path(cwd).exists()  # cleaned up after the shell-out


def test_claude_distill_prefix_requests_json_envelope() -> None:
    assert ClaudeAdapter()._distill_cmd_prefix() == ["claude", "-p", "--output-format", "json"]


def test_claude_distill_extract_unwraps_result_envelope() -> None:
    a = ClaudeAdapter()
    envelope = json.dumps({"type": "result", "subtype": "success", "result": '{"confidence": "high"}'})
    assert a._distill_extract(envelope) == '{"confidence": "high"}'
    assert a._distill_extract("plain prose, not an envelope") == "plain prose, not an envelope"
    assert a._distill_extract('["not", "a", "dict"]') == '["not", "a", "dict"]'
    # an error envelope with no result text yields "" (→ NO_PARSEABLE_POST, not garbage)
    assert a._distill_extract(json.dumps({"type": "result", "subtype": "error", "result": None})) == ""


def test_headless_complete_applies_adapter_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    import manyagent.adapters.builtin as builtin

    monkeypatch.setattr(adapters_base.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(
        builtin,
        "run_agent_subprocess",
        lambda cmd, **kw: (0, json.dumps({"type": "result", "result": '{"a": 1}'}), "", 0.0),
    )
    model = ClaudeAdapter().distill_model()
    assert model is not None and model.complete("p") == '{"a": 1}'  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# security: local adapter loader trust-boundary (fix 1)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.name != "posix", reason="creating symlinks needs privilege on Windows")
def test_local_adapter_symlinked_init_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A symlinked __init__.py must be rejected before exec_module is called."""
    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path))
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "init.py").write_text("ADAPTER = None\n")
    adapter_dir = tmp_path / "myadapter"
    adapter_dir.mkdir()
    # Symlink the init file to the real file
    (adapter_dir / "__init__.py").symlink_to(real_dir / "init.py")
    with pytest.raises(AdapterError, match="symlink"):
        resolve("myadapter")


@pytest.mark.skipif(os.name != "posix", reason="world-writable bit is POSIX-only (Windows uses ACLs)")
def test_local_adapter_world_writable_init_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A world-writable __init__.py must be rejected before exec_module is called."""
    if os.getuid() == 0:  # type: ignore[attr-defined]
        pytest.skip("root ignores the world-write bit (the check still fires but chmod can't represent owner-only)")

    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path))
    _write_local_adapter(tmp_path, "badperm", "bad-bin")
    init = tmp_path / "badperm" / "__init__.py"
    current_mode = init.stat().st_mode
    init.chmod(current_mode | 0o002)  # set world-writable bit
    try:
        with pytest.raises(AdapterError, match="world-writable"):
            resolve("badperm")
    finally:
        init.chmod(current_mode)  # restore so tmp_path cleanup doesn't fail


def test_local_adapter_normal_file_still_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain, owner-only, non-symlinked adapter is accepted unchanged."""
    monkeypatch.setenv("MANYAGENT_ADAPTERS_DIR", str(tmp_path))
    _write_local_adapter(tmp_path, "goodperm", "good-bin")
    resolved = resolve("goodperm")
    assert resolved.binary == "good-bin"


# --------------------------------------------------------------------------- #
# security: distill prompt via stdin, not argv (fix 2)
# --------------------------------------------------------------------------- #


def test_headless_complete_sends_prompt_via_stdin_not_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt must arrive in stdin_input, not in the argv list, so it is
    invisible to /proc/<pid>/cmdline and ps(1) output."""
    import manyagent.adapters.builtin as builtin

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> tuple[int, str, str, float]:
        captured["cmd"] = cmd
        captured["stdin_input"] = kw.get("stdin_input")
        return 0, "result", "", 0.0

    monkeypatch.setattr(builtin, "run_agent_subprocess", fake_run)
    model = builtin._HeadlessModel("claude", ["claude", "-p", "--output-format", "json"])
    model.complete("my long prompt")
    assert captured["stdin_input"] == "my long prompt"
    assert "my long prompt" not in captured["cmd"]  # NOT in argv


def test_codex_adapter_keeps_prompt_in_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """``codex exec`` requires the task as a positional argument; stdin must
    stay None so the codex CLI's own interactive I/O is not broken."""
    import manyagent.adapters.builtin as builtin
    from manyagent.adapters.builtin.codex import CodexAdapter

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> tuple[int, str, str, float]:
        captured["cmd"] = cmd
        captured["stdin_input"] = kw.get("stdin_input")
        return 0, "done", "", 0.0

    monkeypatch.setattr(builtin, "run_agent_subprocess", fake_run)
    model = builtin._HeadlessModel("codex", ["codex", "exec"], prompt_via_stdin=False)
    model.complete("the task")
    assert captured["stdin_input"] is None
    assert "the task" in captured["cmd"]

    # Confirm CodexAdapter opts out of stdin via its class attribute
    assert CodexAdapter._distill_via_stdin is False


def test_claude_and_gemini_use_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """ClaudeAdapter and GeminiAdapter's distill_model() must use stdin."""
    import manyagent.adapters.builtin as builtin
    from manyagent.adapters.builtin.claude import ClaudeAdapter
    from manyagent.adapters.builtin.gemini import GeminiAdapter

    monkeypatch.setattr(adapters_base.shutil, "which", lambda b: f"/usr/bin/{b}")
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> tuple[int, str, str, float]:
        captured["cmd"] = cmd
        captured["stdin_input"] = kw.get("stdin_input")
        return 0, "{}", "", 0.0

    monkeypatch.setattr(builtin, "run_agent_subprocess", fake_run)

    ClaudeAdapter().distill_model().complete("hello")  # type: ignore[union-attr]
    assert captured["stdin_input"] == "hello"
    assert "hello" not in captured["cmd"]

    GeminiAdapter().distill_model().complete("hello")  # type: ignore[union-attr]
    assert captured["stdin_input"] == "hello"
    assert "hello" not in captured["cmd"]
