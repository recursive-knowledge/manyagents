"""manyagent.utils.messages — the catalog of user-facing interface text.

Every string a human reads at an interactive moment (prompts, offers, hints,
one-line statuses) lives HERE, as a named constant or ``str.format`` template
— never inline at the call site. Rationale (user decision 2026-06-10): the
interface wording must be editable in one place without hunting through
handlers, and reviewable as a whole for tone/clarity.

Conventions:

- ``*_OFFER``  — a question put behind a single allowance gate
  (``cli.ask_allow``: Enter=yes). Phrase as a plain question; the gate
  helper appends the ``[Enter=yes / n=no]`` suffix itself.
- ``*_NOTE``   — a one-line informational status (no gate).
- ``*_HINT``   — key-legend text shown alongside an interactive control.
- ``*_PROMPT`` — the label of a free-text input prompt.
- Templates use named ``{fields}``; call sites pass keywords, so a reworded
  message can reorder fields freely.
- Plain text only: rich styling stays at the call site, so editing a message
  never requires touching markup.

This is deliberately NOT gettext: manyagent has no translation requirement. If one
ever appears, wrap these constants in ``gettext.gettext`` — the catalog
shape is exactly what an extractor wants.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# gate suffixes / shared hints
# --------------------------------------------------------------------------- #

ALLOW_SUFFIX = " [Enter=yes / n=no]:"
ALLOW_SUFFIX_DETAIL = " [Enter=yes / d=full text / n=no]:"
NONINTERACTIVE_DENIED = "  (MANYAGENT_NONINTERACTIVE: '{prompt}' → denied)"

# --------------------------------------------------------------------------- #
# committing a post (the single commit gate; ``cli.ask_commit`` / ui.pick_star)
# --------------------------------------------------------------------------- #

COMMIT_QUESTION = "commit post?"
# Typed fallback (no TTY): one line, everything spelled out.
COMMIT_TYPED_HINT = "[Enter=commit ★{propose} · 1-5=set ★ (5★ best) · skip=unrated · n=discard]:"
# Interactive star picker legend (1★ worst … 5★ best is carried by the scale).
COMMIT_PICKER_HINT = "←/→ or 1-5 to rate · Enter=commit · s=skip ★ · n/Esc=discard"
# Variants offered when a truncated preview has full text behind `d`.
COMMIT_PICKER_HINT_DETAIL = "←/→ or 1-5 to rate · Enter=commit · s=skip ★ · d=full text · n/Esc=discard"
COMMIT_TYPED_HINT_DETAIL = "[Enter=commit ★{propose} · 1-5=set ★ (5★ best) · skip=unrated · d=full text · n=discard]:"
COMMIT_PICKER_SCALE_LOW = "1★ = poor"
COMMIT_PICKER_SCALE_HIGH = "5★ = best"
COMMIT_UNRECOGNIZED = "  (unrecognized — committing unrated)"
POST_PROPOSED_HEADER = "--- proposed post ---"
# Labeled rendering of a parser-validated post (``ui.render_post``): the human
# reading of each schema key. Display order lives with the layout in
# ``ui._POST_FIELDS``; ``confidence`` is the panel subtitle, not a body field.
POST_PANEL_TITLE = "proposed {kind}"
POST_FIELD_LABEL_ASSUMPTION = "assumption"
POST_FIELD_LABEL_EVIDENCE = "evidence"
POST_FIELD_LABEL_EVIDENCE_REF = "evidence ref"
POST_FIELD_LABEL_PROPOSED_NEXT = "proposed next"
POST_FIELD_LABEL_PREDICTED_OUTCOME = "predicted outcome"
POST_CONFIDENCE_PREFIX = "confidence: "
# Appended (dim) to a field cut at MANYAGENT_POST_PREVIEW_FIELD_CHARS; the gate's
# hint advertises `d` for the full text.
POST_FIELD_MORE = " … (+{n} chars)"
POST_DISCARDED = "discarded — re-prompt the agent (not stored; C1)"
POST_REJECTED_BY_DISCIPLINE = "post rejected by the discipline (not stored): {reason}"
POST_STORED = "stored post {post_id}"

# --------------------------------------------------------------------------- #
# ★ rating (legacy standalone prompt — `manyagent end` on an unrated reflection)
# --------------------------------------------------------------------------- #

RATING_HINT = "(Enter=accept, 'skip'=unrated):"
RATING_UNRECOGNIZED = "  (unrecognized — leaving unrated)"

# --------------------------------------------------------------------------- #
# `manyagent init` — first-run setup (writes the user-level env file) + the
# CLI-boundary failure hint (`cli._guard`)
# --------------------------------------------------------------------------- #

GUARD_BANK_NOTE = (
    "`ma dev init` writes {env_path} (loaded from any directory); `ma dev preflight` "
    "checks env/Bank/keys. A repo checkout can instead set "
    "MANYAGENT_BANK_TRUSTED_KEY in ./manyagent.env or start a local Bank "
    "with `make bank-up`. Set MANYAGENT_DEBUG=1 for a full traceback."
)
INIT_URL_PROMPT = "Bank URL"
INIT_KEY_PROMPT = "MANYAGENT_BANK_TRUSTED_KEY"
INIT_KEEP_HINT = "[Enter=keep current]:"
INIT_SKIP_HINT = "[Enter=skip — no key yet]:"
INIT_DEFAULT_HINT = "[{default}]:"
INIT_OVERWRITE_OFFER = "{path} exists — overwrite it with the new values?"
INIT_WRITTEN_NOTE = "wrote {path} — run `ma dev preflight` to validate it"
INIT_NO_KEY_NOTE = "no trusted key written to {path} — using the built-in public demo key (hosted pre-alpha Bank)"
INIT_FETCHED_NOTE = "fetched the current Bank connection from the deployment's well-known document"
INIT_OFFLINE_NOTE = (
    "could not fetch the published Bank connection — using built-in defaults (re-run `ma dev init` online to refresh)"
)
INIT_CUSTOM_BANK_NOTE = "custom Bank configured — keeping it (the published public connection was not applied)"

# --------------------------------------------------------------------------- #
# `manyagent start` — session-start offers and notes
# --------------------------------------------------------------------------- #

START_CONTINUE_GOAL_OFFER = "your last session worked on /{goal} — continue that goal here?"
START_DEFAULT_GOAL_NOTE = (
    'no goal given — filed under /{goal} (next time: `ma "<goal>" claude` or `ma session start <goal>`)'
)
START_GOAL_KNOWLEDGE_NOTE = "/{goal} already has {bundles} bundle{bundles_s} · {posts} post{posts_s}"
START_INJECT_OFFER = "inject latest bundle {packet_id} into this session?"
START_INJECTED_NOTE = "injected {packet_id} — delivered to the agent's context at harness start (manyagent._hook)"
START_QUARANTINE_NOTE = "{n} quarantined packet{n_s} under /{goal} awaiting review (never injected)"
START_CROSS_NUDGE_OFFER = "/{goal} has {n} insight{n_s} newer than its last bundle — cross-distill them now?"

# --------------------------------------------------------------------------- #
# agent exit / `manyagent end` — session-end offers
# --------------------------------------------------------------------------- #

AGENT_TRACE_READY = "view trace at {url}"
# Ephemeral run (no sticky `ma session start` session): the session closes with
# the agent window — no end gate, just this dim note before `_do_end` runs.
AGENT_EXIT_AUTO_END_NOTE = (
    "agent finished — auto-ending session {session_id} (start `ma session start` to keep one open)"
)

# --------------------------------------------------------------------------- #
# `ma <agent>` run dispatch — goal/agent sniffing + moved-verb hints
# --------------------------------------------------------------------------- #

# No token in the run line resolved to an installed/builtin agent.
RUN_NO_AGENT = (
    "no agent in `ma {line}` — run an agent with `ma <agent>` (e.g. `ma claude`), "
    'or start a named session with `ma session start "{line}"`'
)
# A removed top-level verb was typed; point at where it lives now.
RUN_VERB_MOVED = "`ma {old}` has moved — use `ma {new}`"

# 00013 per-injection "did this help?" tap (capture-only — does NOT feed reuse_score).
END_INJECT_HELPFUL_PROMPT = "Did the injected knowledge help this session?"
END_SELF_DISTILL_OFFER = "this session has no saved insight yet — summarize what it learned into a reflection post?"
END_INJECT_FOLLOWUP_OFFER = "bundle {packet_id} was injected into this session — reflect on whether it held up?"
END_INJECT_FOLLOWUP_GUIDANCE = (
    "Evaluate whether the injected bundle {packet_id} held up in this session: "
    "cite concrete evidence from the trace for or against its claims, and set "
    "evidence_ref to {packet_id}."
)

# --------------------------------------------------------------------------- #
# inject (in-session verb)
# --------------------------------------------------------------------------- #

INJECT_PREVIEW_HEADER = "--- inject preview ---"
INJECT_OFFER = "inject {packet_id} into session {session_id}?"
INJECT_DECLINED = "inject declined"
INJECT_RECORDED = "injected {packet_id} → session {session_id} (injections row written)"
INJECT_NOTHING = "no distill bundle to inject — run /cross-distill first"
INJECT_QUARANTINED = "refused: {packet_id} is quarantined (excluded from /inject)"
INJECT_UNKNOWN_PACKET = "no packet {packet_id!r}"

# --------------------------------------------------------------------------- #
# discuss / cross-distill (in-session verbs)
# --------------------------------------------------------------------------- #

REPLY_COMMIT_OFFER = "commit reply?"
DISCUSS_NO_POSTS = "no related posts to engage — run /self-distill first"
DISCUSS_REFUSED = "/discuss refused: {reason}"
NO_PARSEABLE_POST = "agent produced no parseable JSON post (not stored)"
NO_PARSEABLE_REPLY = "agent produced no parseable JSON reply (not stored)"
CURATION_FAILED = "curation failed (nothing stored, resumable): {reason}"
CURATED_BUNDLE = "curated {scope} bundle {bundle_id} (curator={curator}) — /inject @{bundle_id} to seed"
