"""Per-adapter in-agent skill installers (M11).

One module per adapter. Each ``install(*, session_id, oma_home, scope,
dry_run)`` builds an :class:`oms._installer.InstallPlan`, runs the consent
gate, applies atomically, and returns the manifest. All filesystem writes
flow through ``oms._installer`` so transparency + uninstall are uniform.
"""

from __future__ import annotations

# Verb → the one-line usage blurb shown on the first-run consent panel
# (`InstallPlan.commands`). These describe what the user GETS, not how it is
# installed — the file-by-file plan lives behind the [d]etails keypress.
# Shared across adapters; each prefixes its own invocation syntax
# (`/self-distill` for claude/gemini, `$oms-self-distill` for codex).
USAGE: tuple[tuple[str, str], ...] = (
    ("self-distill", "post one reflection about the current session"),
    ("discuss", "reply to a prior post (agree / disagree / synthesize)"),
    ("cross-distill", "curate this goal's posts into an insight bundle"),
    ("inject", "seed a session from a curated bundle"),
)
