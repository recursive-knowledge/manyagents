"""manyagent._mcp — the in-agent MCP server (M11).

One Python MCP server exposing manyagent's knowledge-loop verbs as tools any
MCP-capable host (Claude Code, Codex, Gemini CLI) can invoke. The user types
``/self-distill`` (Claude/Gemini) or ``$self-distill`` (Codex) inside the
agent UI; the per-adapter skill instructs the **host LLM** (which is already
the agent we're wrapping) to:

  1. call ``self_distill_draft`` / ``discuss_draft`` to get the goal, prior
     posts, the retrieval gate (for /discuss) and the anti-meta rules;
  2. fill in the structured schema from the live conversation;
  3. show the structured payload to the user verbatim;
  4. call ``commit_post`` directly — the host UI's permission prompt on
     that call is the single accept gate (no separate accept question;
     user decision 2026-06-10).

This split preserves **C1** (a rejected post is *never* persisted — the host
LLM simply doesn't call ``commit_post``) without state on the server: the
draft tools are pure provisioners, ``commit_post`` runs the real
``parse_post`` validator + persists. The host agent's native MCP
**permission prompt** on ``commit_post`` / ``inject_commit`` *is* the human
gate — no string-parsing of y/n inside the chat (manyagent.web.md / advisor).

The verbs themselves live in ``manyagent._skills`` as a ``Skill`` registry (one
source of truth shared with the per-adapter SKILL.md renderers); this module is
the thin FastMCP surface that registers them and re-exports each tool under its
legacy name for back-compat.

Run: ``python -m manyagent._mcp`` (the per-adapter installer registers this).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from manyagent import setup_environment
from manyagent._skills import (
    REGISTRY,  # noqa: F401 — re-exported for callers that iterate the verbs
    _session_id,  # noqa: F401 — re-exported (tests + back-compat import site)
    register_all,
)

setup_environment()  # ./manyagent.env then ~/.manyagent/env (first wins) — Bank creds for the MCP child

app = FastMCP("manyagent")

# Register every verb's tools on the server (deduped — commit_post is shared by
# self-distill and discuss) and re-export each FastMCP-wrapped tool under its
# legacy module-level name. Importers (and tests) keep doing
# ``from manyagent._mcp import commit_post`` and calling ``.fn(...)``.
_TOOLS = register_all(app)
self_distill_draft = _TOOLS["self_distill_draft"]
discuss_draft = _TOOLS["discuss_draft"]
commit_post = _TOOLS["commit_post"]
cross_distill = _TOOLS["cross_distill"]
inject_preview = _TOOLS["inject_preview"]
inject_commit = _TOOLS["inject_commit"]


def main() -> None:
    """Run the MCP server over stdio (the transport every host expects)."""
    app.run()


if __name__ == "__main__":  # pragma: no cover — `python -m manyagent._mcp`
    main()
