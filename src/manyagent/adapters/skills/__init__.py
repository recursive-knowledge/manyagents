"""Per-adapter in-agent skill installers (M11).

One module per adapter. Each ``install(*, session_id, oma_home, scope,
dry_run)`` builds an :class:`manyagent._installer.InstallPlan`, runs the consent
gate, applies atomically, and returns the manifest. All filesystem writes
flow through ``manyagent._installer`` so transparency + uninstall are uniform.
"""

from __future__ import annotations

from manyagent._skills import REGISTRY

# Verb → the one-line usage blurb shown on the first-run consent panel
# (`InstallPlan.commands`). These describe what the user GETS, not how it is
# installed — the file-by-file plan lives behind the [d]etails keypress.
# Derived from the single ``Skill`` registry (manyagent._skills) so the verbs, their
# order, and their blurbs have one source of truth; each adapter prefixes its
# own invocation syntax (`/self-distill` for claude/gemini,
# `$manyagent-self-distill` for codex).
USAGE: tuple[tuple[str, str], ...] = tuple((s.slug, s.blurb) for s in REGISTRY)
