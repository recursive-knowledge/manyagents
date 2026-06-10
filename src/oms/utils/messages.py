"""oms.utils.messages — the catalog of user-facing interface text.

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
- Templates use named ``{fields}``; call sites pass keywords, so a reworded
  message can reorder fields freely.
- Plain text only: rich styling stays at the call site, so editing a message
  never requires touching markup.

This is deliberately NOT gettext: oms has no translation requirement. If one
ever appears, wrap these constants in ``gettext.gettext`` — the catalog
shape is exactly what an extractor wants.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# gate suffixes / shared hints
# --------------------------------------------------------------------------- #

ALLOW_SUFFIX = " [Enter=yes / n=no]:"
NONINTERACTIVE_DENIED = "  (OMS_NONINTERACTIVE: '{prompt}' → denied)"

# --------------------------------------------------------------------------- #
# committing a post (the single commit gate; ``cli.ask_commit`` / ui.pick_star)
# --------------------------------------------------------------------------- #

COMMIT_QUESTION = "commit post?"
# Typed fallback (no TTY): one line, everything spelled out.
COMMIT_TYPED_HINT = "[Enter=commit ★{propose} · 1-5=set ★ (5★ best) · skip=unrated · n=discard]:"
# Interactive star picker legend (1★ worst … 5★ best is carried by the scale).
COMMIT_PICKER_HINT = "←/→ or 1-5 to rate · Enter=commit · s=skip ★ · n/Esc=discard"
COMMIT_PICKER_SCALE_LOW = "1★ = poor"
COMMIT_PICKER_SCALE_HIGH = "5★ = best"
COMMIT_UNRECOGNIZED = "  (unrecognized — committing unrated)"
POST_PROPOSED_HEADER = "--- proposed post ---"
POST_DISCARDED = "discarded — re-prompt the agent (not stored; C1)"
POST_REJECTED_BY_DISCIPLINE = "post rejected by the discipline (not stored): {reason}"
POST_STORED = "stored post {post_id}"

# --------------------------------------------------------------------------- #
# ★ rating (legacy standalone prompt — `oms end` on an unrated reflection)
# --------------------------------------------------------------------------- #

RATING_HINT = "(Enter=accept, 'skip'=unrated):"
RATING_UNRECOGNIZED = "  (unrecognized — leaving unrated)"

# --------------------------------------------------------------------------- #
# `oms start` — session-start offers and notes
# --------------------------------------------------------------------------- #

START_CONTINUE_GOAL_OFFER = "your last session worked on /{goal} — continue that goal here?"
START_DEFAULT_GOAL_NOTE = "no goal given — filed under /{goal} (next time: `oms start <goal>`)"
START_GOAL_KNOWLEDGE_NOTE = "/{goal} already has {bundles} bundle{bundles_s} · {posts} post{posts_s}"
START_INJECT_OFFER = "inject latest bundle {packet_id} into this session?"
START_INJECTED_NOTE = "injected {packet_id} — delivered to the agent's context at harness start (oms._hook)"
START_QUARANTINE_NOTE = "{n} quarantined packet{n_s} under /{goal} awaiting review (never injected)"
START_CROSS_NUDGE_OFFER = "/{goal} has {n} insight{n_s} newer than its last bundle — cross-distill them now?"

# --------------------------------------------------------------------------- #
# agent exit / `oms end` — session-end offers
# --------------------------------------------------------------------------- #

AGENT_EXIT_END_OFFER = "agent finished — end session {session_id} now?"

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
