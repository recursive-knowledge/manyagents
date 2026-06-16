"""M1 tests for manyagent.utils — sid codec, config precedence, provider, rate
limit detection, log prefixes (manyagent.utils.md Verification)."""

from __future__ import annotations

import datetime
import logging
import sys

import httpx
import pytest
import respx

from manyagent.utils import config, messages, provider, sid, ui
from manyagent.utils.log import get_logger
from manyagent.utils.provider import (
    OpenAICompatibleProvider,
    ProviderUnavailable,
    RateLimit,
    rate_limit_signal,
)

# The raw-fd key reader is POSIX-only (termios/tty; select + os.read on a raw
# pipe fd), like ``cli._pty_spawn``. The public ``read_key()`` already raises
# NotImplementedError on Windows; these decorate the helper's direct tests.
_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX raw-fd key reader (select/os.read on pipes)")

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


def test_sid_new_is_a_canonical_uuid4() -> None:
    import uuid as _uuid

    s = sid.new()
    assert _uuid.UUID(s).version == 4
    assert s == s.lower() and len(s) == 36 and s.count("-") == 4
    assert "/" not in s  # keeps {session_id}/{suffix} packet ids unambiguous


def test_sid_parse_normalizes_case_whitespace_and_hyphenless() -> None:
    canonical = sid.new()
    assert sid.parse(canonical.upper()) == canonical  # case
    assert sid.parse(f"  {canonical.upper()}  ") == canonical  # whitespace
    assert sid.parse(canonical) == canonical  # idempotent on canonical
    assert sid.parse(canonical.replace("-", "")) == canonical  # no-hyphen 32-hex form


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "CMA1-FJ2P",  # the old Crockford shape is no longer valid
        "12345678-1234-1234-1234-12345678",  # last group too short
        "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz",  # non-hex
    ],
)
def test_sid_is_valid_rejects_non_canonical(bad: str) -> None:
    assert sid.is_valid(bad) is False


def test_sid_is_valid_rejects_parseable_but_non_canonical_forms() -> None:
    s = sid.new()
    assert sid.is_valid(s.upper()) is False  # canonical is lowercase
    assert sid.is_valid("{" + s + "}") is False  # brace form
    assert sid.is_valid(s.replace("-", "")) is False  # no-hyphen 32-hex form


def test_sid_is_valid_accepts_canonical_and_roundtrips() -> None:
    s = sid.new()
    assert sid.is_valid(s) is True
    assert sid.parse(s) == s  # idempotent on canonical


def test_sid_parse_rejects_unfixable() -> None:
    with pytest.raises(ValueError, match="UUID"):
        sid.parse("definitely not a uuid")
    with pytest.raises(ValueError, match="UUID"):
        sid.parse("SHORT")


# --------------------------------------------------------------------------- #
# config precedence
# --------------------------------------------------------------------------- #


def test_config_precedence_cli_over_env_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANYAGENT_X", raising=False)
    assert config.resolve("MANYAGENT_X", "dflt") == "dflt"  # default
    monkeypatch.setenv("MANYAGENT_X", "from_env")
    assert config.resolve("MANYAGENT_X", "dflt") == "from_env"  # env > default
    assert config.resolve("MANYAGENT_X", "dflt", cli_value="from_cli") == "from_cli"  # cli > env


def test_config_casts() -> None:
    assert config.resolve("MANYAGENT_MISSING", 42, cast=int) == 42
    assert config.resolve("MANYAGENT_MISSING", 1.5, cast=float) == 1.5


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
    assert config.MANYAGENT_DISTILL_TIMEOUT_S == 600
    assert config.MANYAGENT_TRACE_MAX_BYTES == 2 * 1024 * 1024
    assert config.MANYAGENT_CURATOR_MODE == "auto"
    assert config.MANYAGENT_RATING_PROMPT is True
    assert config.MANYAGENT_NONINTERACTIVE is False


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
    monkeypatch.delenv("MANYAGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MANYAGENT_LLM_MODEL", raising=False)
    p = provider.resolve(adapter=_AdapterWithModel())
    assert p.complete("hi") == "echo:hi"


def test_provider_resolve_openai_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("MANYAGENT_LLM_MODEL", "gpt-test")
    monkeypatch.setenv("MANYAGENT_LLM_API_KEY", "sk-test")
    p = provider.resolve(adapter=None)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.model == "gpt-test"


def test_provider_resolve_hard_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANYAGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MANYAGENT_LLM_MODEL", raising=False)
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
    assert logger.name == "manyagent.m1test"
    handler = logging.getLogger("manyagent").handlers[0]
    fmt = handler.formatter
    assert fmt is not None
    for level, tag in ((logging.INFO, "[INFO]"), (logging.DEBUG, "[DEBUG]")):
        rec = logging.LogRecord("manyagent.x", level, __file__, 1, "hello", None, None)
        assert fmt.format(rec).startswith(f"{tag} hello")


# --------------------------------------------------------------------------- #
# ui (rich presentation layer)
# --------------------------------------------------------------------------- #


def test_ui_render_is_plain_text_when_color_never(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_COLOR", "never")
    from rich.text import Text

    assert ui.render(Text("hello", style="bold red")) == "hello"


def test_ui_render_emits_ansi_when_color_always(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANYAGENT_COLOR", "always")
    monkeypatch.delenv("NO_COLOR", raising=False)  # rich itself strips colors (not attributes) under NO_COLOR
    monkeypatch.setenv("TERM", "xterm-256color")
    from rich.text import Text

    out = ui.render(Text("hello", style="bold red"))
    assert "hello" in out and "\x1b[" in out


def test_ui_no_color_env_downgrades_auto_to_never(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR (no-color.org) forces plain output in auto mode — even if the
    stream were a TTY. An explicit MANYAGENT_COLOR=always wins over it (the spec's
    software-level-config precedence)."""
    monkeypatch.setenv("MANYAGENT_COLOR", "auto")
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.console().is_terminal is False  # forced off, not auto-detected


def test_ui_render_soft_wrap_keeps_long_lines_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-line message longer than the 80-col non-TTY width must not be
    wrapped — `grep` and substring assertions over CLI output rely on it."""
    monkeypatch.setenv("MANYAGENT_COLOR", "never")
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
    monkeypatch.setenv("MANYAGENT_COLOR", "never")
    assert ui.render(ui.style_diff(diff)) == diff  # plain rendering is byte-identical
    monkeypatch.setenv("MANYAGENT_COLOR", "always")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert "\x1b[" in ui.render(ui.style_diff(diff))


# --------------------------------------------------------------------------- #
# ui.pick_star — the ★ number-line commit gate (2026-06-10)
# --------------------------------------------------------------------------- #


def _run_picker(propose: int, *keys: str) -> tuple[tuple[bool, int | None], str]:
    from manyagent.utils import ui

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
    from manyagent.utils import messages

    _, screen = _run_picker(3, "enter")
    assert messages.COMMIT_PICKER_SCALE_LOW in screen and messages.COMMIT_PICKER_SCALE_HIGH in screen


def test_render_post_labels_every_schema_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commit-gate preview renders the post-mortem as a labeled panel —
    human field names, values verbatim, confidence as the subtitle — instead
    of a raw json.dumps blob."""
    monkeypatch.setenv("MANYAGENT_COLOR", "never")
    structured = {
        "load_bearing_assumption": "the regex recompiled per call — that's the slowdown",
        "evidence": "cumtime 4.2s in tokenize()",
        "evidence_ref": None,
        "proposed_next": "hoist the compiled pattern to module scope",
        "predicted_outcome": "p95 drops below 80ms",
        "confidence": "medium",
    }
    out = ui.render_post(structured, kind="reflection")
    assert "proposed reflection" in out
    for _, label in ui._POST_FIELDS:
        if label == "evidence ref":
            continue
        assert label in out
    assert "evidence ref" not in out  # null evidence_ref is a valid, unrendered state
    assert "cumtime 4.2s in tokenize()" in out
    assert messages.POST_CONFIDENCE_PREFIX + "medium" in out
    assert '"confidence"' not in out  # no raw JSON keys in the panel


def test_render_post_wraps_to_the_cap_then_truncates_with_a_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A field wraps in full up to MANYAGENT_POST_PREVIEW_FIELD_CHARS, then is cut
    at a word boundary with the dim `… (+N chars)` marker; ``full=True`` (the
    `d` expansion) renders every character with no marker."""
    monkeypatch.setenv("MANYAGENT_COLOR", "never")
    long_evidence = ("word " * 200).strip()  # 999 chars, far past the 280 cap
    structured = {
        "load_bearing_assumption": "short",
        "evidence": long_evidence,
        "evidence_ref": None,
        "proposed_next": "short",
        "predicted_outcome": "short",
        "confidence": "low",
    }
    preview = ui.render_post(structured, kind="reflection")
    assert "chars)" in preview  # the truncation marker
    assert preview.count("word") < 200  # genuinely cut, not just wrapped
    full = ui.render_post(structured, kind="reflection", full=True)
    assert "chars)" not in full
    assert full.count("word") == 200
    # the cap is a tunable: 0 disables truncation entirely
    monkeypatch.setenv("MANYAGENT_POST_PREVIEW_FIELD_CHARS", "0")
    assert "chars)" not in ui.render_post(structured, kind="reflection")


def test_pick_star_d_expands_the_full_post() -> None:
    """With a truncated preview, `d` prints the untruncated rendering and the
    picker resumes; without one, `d` is a no-op and the hint never offers it."""
    feed = ["d", "enter"]
    frames: list[str] = []
    commit, rating = ui.pick_star(4, key_fn=lambda: feed.pop(0), out=frames.append, detail="THE FULL POST")
    assert (commit, rating) == (True, 4)
    screen = "".join(frames)
    assert "THE FULL POST" in screen
    assert "d=full text" in screen
    (commit, rating), screen = _run_picker(4, "d", "enter")  # no detail
    assert (commit, rating) == (True, 4)
    assert "d=full text" not in screen


def test_render_post_falls_back_to_highlighted_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body that is not the post-mortem shape (defensive: the parser ran
    first) falls back to the plain header + JSON — em-dashes and quotes stay
    human-readable (ensure_ascii=False), never \\u-escaped."""
    monkeypatch.setenv("MANYAGENT_COLOR", "never")
    out = ui.render_post({"weird": "shape — kept readable"}, kind="reflection")
    assert out.startswith(messages.POST_PROPOSED_HEADER)
    assert '"weird"' in out and "shape — kept readable" in out
    assert "\\u2014" not in out


@_posix_only
def test_read_key_decodes_arrow_bursts_from_the_raw_fd() -> None:
    """Regression (2026-06-10): read_key read buffered ``sys.stdin``, whose
    readahead swallowed an arrow's trailing ``[X`` bytes — the select() poll
    saw an empty fd and every arrow collapsed to a lone ESC, silently
    DISCARDING the post at the commit gate. The decoder works on the raw fd
    (os.read), so the full escape burst is seen even when written in one
    chunk, exactly as a terminal delivers it."""
    import os

    r, w = os.pipe()
    try:
        os.write(w, b"\x1b[D\x1b[C\r3\x1b")
        assert ui._read_key_fd(r) == "left"
        assert ui._read_key_fd(r) == "right"
        assert ui._read_key_fd(r) == "enter"
        assert ui._read_key_fd(r) == "3"
        assert ui._read_key_fd(r) == "esc"  # lone ESC: the 0.05s poll times out
    finally:
        os.close(r)
        os.close(w)


@_posix_only
def test_read_key_drains_full_csi_sequences_without_leaking_tail_bytes() -> None:
    """A modified arrow (Shift-Left = ``\\x1b[1;2D``) still decodes by its
    final byte, and a non-arrow CSI (Home = ``\\x1b[1~``) collapses to esc —
    in both cases the parameter bytes are DRAINED, never returned as fake
    literal keypresses on the next call (a leaked '1' would yank the picker's
    rating to 1★)."""
    import os

    r, w = os.pipe()
    try:
        os.write(w, b"\x1b[1;2D\r\x1b[1~\r")
        assert ui._read_key_fd(r) == "left"  # Shift-Left is still left
        assert ui._read_key_fd(r) == "enter"  # ';2D' tail did not leak
        assert ui._read_key_fd(r) == "esc"  # Home: drained, esc (= no-op/discard)
        assert ui._read_key_fd(r) == "enter"  # '~' tail did not leak
    finally:
        os.close(r)
        os.close(w)


# --------------------------------------------------------------------------- #
# messages — the user-facing text catalog (2026-06-10)
# --------------------------------------------------------------------------- #


def test_messages_catalog_is_pure_text() -> None:
    """Every public constant is a plain string; every template formats with
    its documented fields (a rename in the catalog must fail loudly here)."""
    from manyagent.utils import messages

    consts = {k: v for k, v in vars(messages).items() if k.isupper()}
    assert consts and all(isinstance(v, str) for v in consts.values())
    # spot-format the field-bearing templates
    messages.START_CROSS_NUDGE_OFFER.format(goal="g", n=3, n_s="s")
    messages.END_INJECT_FOLLOWUP_GUIDANCE.format(packet_id="curator/x")
    messages.START_QUARANTINE_NOTE.format(n=1, n_s="", goal="g")
    messages.COMMIT_TYPED_HINT.format(propose=3)


def test_demo_jwt_is_derived_and_well_formed() -> None:
    """The demo-stack fallback keys are DERIVED from Supabase's public demo
    secret at runtime — no key-shaped literal lives in the repo. Assert the
    claim shape and that the privileged service_role is never minted by the
    constants (only anon + authenticated are wired as defaults)."""
    import base64
    import json

    anon = config._demo_jwt("anon")
    trusted = config._demo_jwt("authenticated")
    assert anon != trusted and anon.count(".") == 2

    def claims(tok: str) -> dict[str, object]:
        body = tok.split(".")[1]
        return dict(json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))))

    assert claims(anon) == {"iss": "supabase-demo", "role": "anon", "exp": 1983812996}
    assert claims(trusted)["role"] == "authenticated"
    assert config.MANYAGENT_BANK_URL_DEFAULT == "https://db-swarms.formulacode.org"
