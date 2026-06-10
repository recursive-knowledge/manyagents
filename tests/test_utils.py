"""M1 tests for oms.utils — sid codec, config precedence, provider, rate
limit detection, log prefixes (oms.utils.md Verification)."""

from __future__ import annotations

import datetime
import logging

import httpx
import pytest
import respx

from oms.utils import config, provider, sid, ui
from oms.utils.log import get_logger
from oms.utils.provider import (
    OpenAICompatibleProvider,
    ProviderUnavailable,
    RateLimit,
    rate_limit_signal,
)

# --------------------------------------------------------------------------- #
# sid codec
# --------------------------------------------------------------------------- #


def test_sid_new_10k_unique_and_valid() -> None:
    seen: set[str] = set()
    for _ in range(10_000):
        s = sid.new(exists=seen.__contains__)  # deterministic uniqueness
        assert sid.is_valid(s), s
        assert s not in seen
        seen.add(s)
    assert len(seen) == 10_000


def test_sid_new_retries_on_forced_collision() -> None:
    calls: list[str] = []

    def exists(candidate: str) -> bool:
        calls.append(candidate)
        return len(calls) == 1  # first candidate "taken", retry once

    out = sid.new(exists=exists)
    assert len(calls) == 2
    assert out == calls[1]
    assert sid.is_valid(out)


def test_sid_parse_normalizes_case_hyphen_and_crockford_aliases() -> None:
    canonical = sid.new()
    body = canonical.replace("-", "")
    assert sid.parse(canonical.lower()) == canonical  # lowercase
    assert sid.parse(body) == canonical  # missing hyphen
    assert sid.parse(f"  {canonical.lower()}  ") == canonical  # whitespace
    # Crockford aliases: I/L -> 1, O -> 0
    assert sid.parse("OIL0OIL0") == sid.parse("01100110") == "0110-0110"


@pytest.mark.parametrize(
    "bad",
    [
        "CMA1FJ2P",  # missing hyphen (not canonical)
        "CMA1-FJ2",  # too short
        "CMA1-FJ2PP",  # too long
        "cma1-fj2p",  # lowercase (not canonical)
        "CMAI-FJ2P",  # contains 'I' (excluded from encode alphabet)
        "CMAL-FJ2P",  # contains 'L'
        "CMAO-FJ2P",  # contains 'O'
        "CMAU-FJ2P",  # contains 'U'
        "CMA1FJ-2P",  # hyphen misplaced
    ],
)
def test_sid_is_valid_rejects_bad_forms(bad: str) -> None:
    assert sid.is_valid(bad) is False


def test_sid_is_valid_accepts_canonical_and_roundtrips() -> None:
    s = sid.new()
    assert sid.is_valid(s) is True
    assert sid.parse(s) == s  # idempotent on canonical


def test_sid_parse_rejects_unfixable() -> None:
    with pytest.raises(ValueError, match=r"symbols?"):
        sid.parse("CMAU-FJ2P")  # U has no Crockford decode alias
    with pytest.raises(ValueError, match=r"symbols"):
        sid.parse("SHORT")


# --------------------------------------------------------------------------- #
# config precedence
# --------------------------------------------------------------------------- #


def test_config_precedence_cli_over_env_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMS_X", raising=False)
    assert config.resolve("OMS_X", "dflt") == "dflt"  # default
    monkeypatch.setenv("OMS_X", "from_env")
    assert config.resolve("OMS_X", "dflt") == "from_env"  # env > default
    assert config.resolve("OMS_X", "dflt", cli_value="from_cli") == "from_cli"  # cli > env


def test_config_casts() -> None:
    assert config.resolve("OMS_MISSING", 42, cast=int) == 42
    assert config.resolve("OMS_MISSING", 1.5, cast=float) == 1.5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_config_as_bool(raw: str, expected: bool) -> None:
    assert config.as_bool(raw) is expected


def test_config_snapshot_constants_have_expected_defaults() -> None:
    assert config.OMS_DISTILL_TIMEOUT_S == 600
    assert config.OMS_TRACE_MAX_BYTES == 2 * 1024 * 1024
    assert config.OMS_CURATOR_MODE == "auto"
    assert config.OMS_RATING_PROMPT is True
    assert config.OMS_NONINTERACTIVE is False


# --------------------------------------------------------------------------- #
# provider resolution (3 paths)
# --------------------------------------------------------------------------- #


class _FakeModel:
    name = "fake"

    def complete(self, prompt: str, *, max_tokens: int | None = None) -> str:
        return f"echo:{prompt}"

    def rate_limit_signal(self, raw_error: str) -> RateLimit | None:
        return None


class _AdapterWithModel:
    name = "fakeadapter"

    def distill_model(self) -> _FakeModel:
        return _FakeModel()


def test_provider_resolve_adapter_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OMS_LLM_MODEL", raising=False)
    p = provider.resolve(adapter=_AdapterWithModel())
    assert p.complete("hi") == "echo:hi"


def test_provider_resolve_openai_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OMS_LLM_MODEL", "gpt-test")
    monkeypatch.setenv("OMS_LLM_API_KEY", "sk-test")
    p = provider.resolve(adapter=None)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.model == "gpt-test"


def test_provider_resolve_hard_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OMS_LLM_MODEL", raising=False)
    with pytest.raises(ProviderUnavailable, match="ships no keys"):
        provider.resolve(adapter=None)


@respx.mock
def test_openai_compatible_complete_hits_stubbed_endpoint() -> None:
    route = respx.post("https://llm.example/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok!"}}]}),
    )
    p = OpenAICompatibleProvider(base_url="https://llm.example/v1", model="m", api_key="k")
    assert p.complete("ping", max_tokens=8) == "ok!"
    assert route.called
    sent = route.calls.last.request
    assert b'"model": "m"' in sent.content or b'"model":"m"' in sent.content


# --------------------------------------------------------------------------- #
# rate-limit detection (canned Codex / Claude payloads)
# --------------------------------------------------------------------------- #


def test_rate_limit_signal_codex_with_reset() -> None:
    raw = '{"type":"error","message":"You\'ve hit your usage limit. Please try again at Apr 11th, 2026 2:32 PM."}'
    rl = rate_limit_signal(raw)
    assert rl is not None
    assert rl.provider == "codex"
    assert rl.reset_at == datetime.datetime(2026, 4, 11, 14, 32, tzinfo=datetime.UTC)


def test_rate_limit_signal_claude_structured() -> None:
    raw = '{"type":"rate_limit_event","rate_limit_info":{"status":"exceeded","resetsAt":1775559600}}'
    rl = rate_limit_signal(raw, provider="claude")
    assert rl is not None
    assert rl.provider == "claude"
    assert rl.reset_at == datetime.datetime.fromtimestamp(1775559600, tz=datetime.UTC)


def test_rate_limit_signal_claude_allowed_is_none() -> None:
    raw = '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1775559600}}'
    assert rate_limit_signal(raw, provider="claude") is None


def test_rate_limit_signal_codex_no_reset_is_rate_limited() -> None:
    raw = '{"type":"error","message":"You have hit your usage limit for this week."}'
    rl = rate_limit_signal(raw, provider="codex")
    assert rl is not None
    assert rl.reset_at is None
    assert rl.retry_after_s() is None


def test_rate_limit_signal_non_ratelimit_is_none() -> None:
    assert rate_limit_signal("") is None
    assert rate_limit_signal("just a normal error trace, nothing to see") is None


def test_rate_limit_retry_after_seconds() -> None:
    reset = datetime.datetime(2026, 4, 11, 14, 32, tzinfo=datetime.UTC)
    rl = RateLimit("codex", reset)
    now = datetime.datetime(2026, 4, 11, 14, 30, tzinfo=datetime.UTC)
    assert rl.retry_after_s(now=now) == pytest.approx(120.0)
    past = datetime.datetime(2026, 4, 11, 15, 0, tzinfo=datetime.UTC)
    assert rl.retry_after_s(now=past) == 0.0  # never negative


# --------------------------------------------------------------------------- #
# log prefixes
# --------------------------------------------------------------------------- #


def test_log_emits_bracketed_level_prefixes() -> None:
    logger = get_logger("m1test")
    assert logger.name == "oms.m1test"
    handler = logging.getLogger("oms").handlers[0]
    fmt = handler.formatter
    assert fmt is not None
    for level, tag in ((logging.INFO, "[INFO]"), (logging.DEBUG, "[DEBUG]")):
        rec = logging.LogRecord("oms.x", level, __file__, 1, "hello", None, None)
        assert fmt.format(rec).startswith(f"{tag} hello")


# --------------------------------------------------------------------------- #
# ui (rich presentation layer)
# --------------------------------------------------------------------------- #


def test_ui_render_is_plain_text_when_color_never(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_COLOR", "never")
    from rich.text import Text

    assert ui.render(Text("hello", style="bold red")) == "hello"


def test_ui_render_emits_ansi_when_color_always(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMS_COLOR", "always")
    monkeypatch.delenv("NO_COLOR", raising=False)  # rich itself strips colors (not attributes) under NO_COLOR
    monkeypatch.setenv("TERM", "xterm-256color")
    from rich.text import Text

    out = ui.render(Text("hello", style="bold red"))
    assert "hello" in out and "\x1b[" in out


def test_ui_no_color_env_downgrades_auto_to_never(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR (no-color.org) forces plain output in auto mode — even if the
    stream were a TTY. An explicit OMS_COLOR=always wins over it (the spec's
    software-level-config precedence)."""
    monkeypatch.setenv("OMS_COLOR", "auto")
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.console().is_terminal is False  # forced off, not auto-detected


def test_ui_render_soft_wrap_keeps_long_lines_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-line message longer than the 80-col non-TTY width must not be
    wrapped — `grep` and substring assertions over CLI output rely on it."""
    monkeypatch.setenv("OMS_COLOR", "never")
    from rich.text import Text

    long_line = "x" * 300
    assert ui.render(Text(long_line)) == long_line


def test_ui_tilde_abbreviates_home_for_display_only() -> None:
    from pathlib import Path

    assert ui.tilde(Path.home() / ".claude" / "skills") == "~/.claude/skills"
    assert ui.tilde(Path("/etc/hosts")) == "/etc/hosts"  # outside $HOME: unchanged
    assert ui.tilde(Path.home()) == "~"  # $HOME itself, not "~/."


def test_ui_style_diff_preserves_content_and_colors_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    diff = "=== f ===\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n context"
    monkeypatch.setenv("OMS_COLOR", "never")
    assert ui.render(ui.style_diff(diff)) == diff  # plain rendering is byte-identical
    monkeypatch.setenv("OMS_COLOR", "always")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert "\x1b[" in ui.render(ui.style_diff(diff))


# --------------------------------------------------------------------------- #
# ui.pick_star — the ★ number-line commit gate (2026-06-10)
# --------------------------------------------------------------------------- #


def _run_picker(propose: int, *keys: str) -> tuple[tuple[bool, int | None], str]:
    from oms.utils import ui

    feed = list(keys)
    frames: list[str] = []
    result = ui.pick_star(propose, key_fn=lambda: feed.pop(0), out=frames.append)
    return result, "".join(frames)


def test_pick_star_arrows_move_and_enter_commits() -> None:
    (commit, rating), screen = _run_picker(3, "right", "right", "enter")
    assert (commit, rating) == (True, 5)
    assert "❰5★❱" in screen  # the selection rendered at 5 before commit


def test_pick_star_bounds_clamp() -> None:
    (commit, rating), _ = _run_picker(5, "right", "right", "enter")
    assert (commit, rating) == (True, 5)  # cannot move past 5★
    (commit, rating), _ = _run_picker(1, "left", "enter")
    assert (commit, rating) == (True, 1)  # cannot move below 1★


def test_pick_star_digit_jump_skip_and_discard() -> None:
    (commit, rating), _ = _run_picker(3, "2", "enter")
    assert (commit, rating) == (True, 2)
    (commit, rating), _ = _run_picker(3, "s")
    assert (commit, rating) == (True, None)  # unrated is first-class
    for discard in ("n", "esc"):
        (commit, rating), _ = _run_picker(3, discard)
        assert (commit, rating) == (False, None)


def test_pick_star_legend_says_which_end_is_best() -> None:
    from oms.utils import messages

    _, screen = _run_picker(3, "enter")
    assert messages.COMMIT_PICKER_SCALE_LOW in screen and messages.COMMIT_PICKER_SCALE_HIGH in screen


# --------------------------------------------------------------------------- #
# messages — the user-facing text catalog (2026-06-10)
# --------------------------------------------------------------------------- #


def test_messages_catalog_is_pure_text() -> None:
    """Every public constant is a plain string; every template formats with
    its documented fields (a rename in the catalog must fail loudly here)."""
    from oms.utils import messages

    consts = {k: v for k, v in vars(messages).items() if k.isupper()}
    assert consts and all(isinstance(v, str) for v in consts.values())
    # spot-format the field-bearing templates
    messages.START_CROSS_NUDGE_OFFER.format(goal="g", n=3, n_s="s")
    messages.END_INJECT_FOLLOWUP_GUIDANCE.format(packet_id="curator/x")
    messages.START_QUARANTINE_NOTE.format(n=1, n_s="", goal="g")
    messages.COMMIT_TYPED_HINT.format(propose=3)
