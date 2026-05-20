"""M5 tests for oms.adapters — ABC enforcement, registry resolution order,
the PromptPrefixInjector round-trip, per-builtin conformance against sample
native traces, capture()-is-raw (scrub happens in oms.capture), and the
distill_model() provider seam (oms.adapters.md Verification)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oms.adapters import Adapter, AdapterError, NotInstalled, available, resolve, terminate_all_agents
from oms.adapters import base as adapters_base
from oms.adapters.base import PromptPrefixInjector
from oms.adapters.builtin.claude import ClaudeAdapter
from oms.adapters.builtin.codex import CodexAdapter
from oms.adapters.builtin.gemini import GeminiAdapter
from oms.adapters.builtin.qwen import QwenAdapter
from oms.adapters.registry import _hub_fetch
from oms.capture import validate
from oms.utils import provider

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
        "from oms.adapters.base import Adapter\n"
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
    monkeypatch.setenv("OMS_ADAPTERS_DIR", str(tmp_path))
    _write_local_adapter(tmp_path, "claude", "local-claude-bin")
    resolved = resolve("claude")
    assert resolved is not ClaudeAdapter and resolved.binary == "local-claude-bin"


def test_unknown_adapter_with_offline_hub_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_ADAPTERS_DIR", str(tmp_path))
    assert _hub_fetch("acme") is None  # offline default
    with pytest.raises(AdapterError, match="no adapter 'acme'"):
        resolve("acme")


def test_hub_not_found_then_installed_then_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_ADAPTERS_DIR", str(tmp_path))

    def fake_hub(name: str) -> Path | None:
        _write_local_adapter(tmp_path, name, f"hub-{name}")
        return tmp_path / name

    monkeypatch.setattr("oms.adapters.registry._hub_fetch", fake_hub)
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
    assert trace.bytes_in > 0 and trace.bytes_out == 0  # raw: oms.capture sets bytes_out


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


def test_capture_is_raw_scrub_happens_in_oms_capture() -> None:
    # The claude sample embeds a fake key; capture() must NOT scrub it (that is
    # oms.capture's centralized job — a careless adapter cannot weaken safety).
    c = ClaudeAdapter(session_id="S", agent_id="S/a", trace_source=_SAMPLES / "claude.json")
    body = " ".join(e.text for e in c.capture().events)
    assert "sk-proj-LEAKLEAKLEAKLEAKLEAKLEAK" in body  # raw, faithful, un-scrubbed


def test_capture_without_trace_source_errors() -> None:
    with pytest.raises(AdapterError, match="no trace_source"):
        ClaudeAdapter(session_id="S", agent_id="S/a").capture()


async def test_capture_then_oms_capture_persist_scrubs_and_lands_raw_packet(fake_bank: object) -> None:
    from oms.bank import FakeBank
    from oms.capture import persist

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
# distill_model() — the oms.utils.provider seam
# --------------------------------------------------------------------------- #


def test_distill_model_none_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters_base.shutil, "which", lambda _b: None)
    assert ClaudeAdapter().distill_model() is None
    monkeypatch.delenv("OMS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OMS_LLM_MODEL", raising=False)
    with pytest.raises(provider.ProviderUnavailable):
        provider.resolve(adapter=ClaudeAdapter())  # no model, no OpenAI fallback


def test_distill_model_is_a_provider_when_binary_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters_base.shutil, "which", lambda b: f"/usr/bin/{b}")
    model = CodexAdapter().distill_model()
    assert model is not None and callable(model.complete) and callable(model.rate_limit_signal)  # type: ignore[attr-defined]
    p = provider.resolve(adapter=CodexAdapter())  # picked up via the adapter hook
    assert callable(p.complete) and callable(p.rate_limit_signal)
