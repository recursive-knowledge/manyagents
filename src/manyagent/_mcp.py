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

Run: ``python -m manyagent._mcp`` (the per-adapter installer registers this).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from manyagent import setup_environment

setup_environment()  # ~/.manyagent/env then ./manyagent.env — Bank creds for the MCP child

app = FastMCP("manyagent")


# --------------------------------------------------------------------------- #
# session id resolution: MANYAGENT_SESSION env wins, else ~/.manyagent/active.
# This dual-source lets `manyagent <name>` thread the session at PTY-spawn time AND
# lets the agent (opened later/anywhere after one-time `manyagent start`) still work.
# --------------------------------------------------------------------------- #


def _session_id() -> str:
    sid = os.environ.get("MANYAGENT_SESSION", "").strip()
    if sid:
        return sid
    home = Path(os.environ.get("MANYAGENT_HOME", str(Path.home() / ".manyagent"))).expanduser()
    p = home / "active"
    if p.is_file():
        s = p.read_text(encoding="utf-8").strip()
        if s:
            return s
    raise RuntimeError(
        "no active manyagent session — run `manyagent start` first (writes ~/.manyagent/active), "
        "or export MANYAGENT_SESSION=<id> for this MCP server"
    )


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #


@app.tool()
async def self_distill_draft(guidance: str = "") -> dict[str, Any]:
    """Provision the substrate to draft ONE reflection post for the active
    session. **Does not persist.** Returns the goal, prior in-session posts,
    the anti-meta rules + structured schema the host LLM must fill in, and a
    recommended ★ (based on the conversation's apparent confidence). The host
    LLM then shows the filled-in structured payload to the user verbatim and
    calls `commit_post` directly — the host UI's permission prompt on that
    call is the single accept gate.

    A denied post is **never persisted** because an unapproved
    `commit_post` never runs (C1: manyagent.cli.md:48; manyagent.core.md:147)."""
    from manyagent.bank import get_bank
    from manyagent.forum.prompt import render_post_prompt

    sid = _session_id()
    bank = get_bank()
    session = await bank.get_session(sid)
    goal = (session or {}).get("goal")
    prior = await bank.list_packets(session_id=sid, type="post", goal=goal)
    return {
        "session": sid,
        "goal": goal,
        "kind": "reflection",
        "guidance": guidance,
        "prior_posts_count": len(prior),
        "instruction_for_host_llm": render_post_prompt(kind="reflection", goal=goal, guidance=guidance),
        "commit_via": "commit_post(kind='reflection', structured={...}, rating=N)",
    }


@app.tool()
async def discuss_draft(stance: str, packet: str | None = None) -> dict[str, Any]:
    """Provision the substrate to draft ONE stance reply. Runs
    retrieval-before-post (the mechanical gate `manyagent.forum` enforces). Returns
    the ranked prior in-session posts under the current goal and the chosen
    ``reply_to`` (the `@packet` arg if given, else the most-under-engaged
    post). The host LLM drafts the reply, shows it to the user, then calls
    ``commit_post(kind='reply', ..., reply_to=..., stance=...)`` directly —
    the permission prompt on that call is the single accept gate.

    Refuses up-front if no in-session posts exist under this goal (retrieve
    would be empty) — the host should call `self_distill_draft` first."""
    if stance not in ("agree", "disagree", "synthesize"):
        raise ValueError(f"bad stance {stance!r}; expected agree|disagree|synthesize")
    from manyagent.bank import get_bank
    from manyagent.forum import retrieve
    from manyagent.forum.prompt import render_post_prompt

    sid = _session_id()
    bank = get_bank()
    session = await bank.get_session(sid)
    goal = (session or {}).get("goal")
    # agent_id for the gate: use the MCP-host pseudo-agent — one per session is
    # fine for the in-agent flow (the wrapper-registered agent is who's driving).
    agent_id = f"{sid}/mcp"
    ranked = await retrieve(sid, agent_id=agent_id, goal=goal, bank=bank)
    if not ranked:
        return {
            "error": "no related posts to engage — call self_distill_draft first (retrieval-before-post; manyagent.forum)",
        }
    reply_to = (packet or "").lstrip("@") or str(ranked[0]["id"])
    if reply_to not in {str(p["id"]) for p in ranked}:
        raise ValueError(f"reply_to {reply_to!r} was not among retrieved posts (engage a retrieved one)")
    return {
        "session": sid,
        "goal": goal,
        "kind": "reply",
        "stance": stance,
        "reply_to": reply_to,
        "agent_id": agent_id,
        "ranked_post_ids": [str(p["id"]) for p in ranked],
        "instruction_for_host_llm": render_post_prompt(kind="reply", goal=goal, prior_posts=ranked),
        "commit_via": (f"commit_post(kind='reply', structured={{...}}, reply_to={reply_to!r}, stance={stance!r})"),
    }


@app.tool()
async def commit_post(
    kind: str,
    structured: dict[str, Any],
    rating: int | None = None,
    reply_to: str | None = None,
    stance: str | None = None,
) -> dict[str, Any]:
    """Persist a structured post (reflection or reply). **This is the human
    gate**: the host agent's MCP permission prompt fires on this call — the
    user sees exactly the structured payload that will be persisted and
    approves/denies in the agent UI. ``parse_post`` runs the mechanical
    anti-meta + verbatim-evidence + retrieval-before-reply checks; a
    parser-refused payload is NOT persisted (C1)."""
    from manyagent.bank import get_bank
    from manyagent.forum import parse_post
    from manyagent.utils import sid as sid_

    if kind not in ("reflection", "reply"):
        raise ValueError(f"bad kind {kind!r}; expected reflection|reply")
    if rating is not None and not (1 <= rating <= 5):
        raise ValueError(f"rating must be None or 1..5, got {rating!r}")

    session_id = _session_id()
    agent_id = f"{session_id}/mcp"
    record: dict[str, Any] = {
        "id": f"{session_id}/{sid_.new().replace('-', '').lower()[:8]}",
        "session_id": session_id,
        "type": "post",
        "agent_id": agent_id,
        "kind": kind,
        "goal": (await (get_bank()).get_session(session_id) or {}).get("goal"),
        "structured": structured,
    }
    if kind == "reply":
        if reply_to is None or stance is None:
            raise ValueError("a reply requires reply_to and stance")
        record["reply_to"] = reply_to
        record["stance"] = stance

    bank = get_bank()
    ok, res = await parse_post(record, bank=bank)
    if not ok or not isinstance(res, dict):
        return {"ok": False, "error": f"parser refused (not stored): {res}"}
    if rating is not None:
        res["rating"] = rating
    res.pop("preference", None)  # C1 belt-and-suspenders: a post never carries preference
    await bank.put_packet(res)
    return {"ok": True, "post_id": res["id"], "kind": kind, "rating": rating}


@app.tool()
async def cross_distill(server: bool = False) -> dict[str, Any]:
    """Curate goal-scoped posts (corpus-wide across sessions) into one
    6-bucket Insight bundle. Idempotent: same posts ⇒ same content-addressed
    packet, no re-spend. Not human-gated — the curator is mechanical and
    its output is itself non-destructive; the gate fires later at
    ``inject_commit``."""
    from manyagent.bank import get_bank
    from manyagent.distill import CurationError, NoPostsError, curate

    sid = _session_id()
    bank = get_bank()
    session = await bank.get_session(sid)
    goal = (session or {}).get("goal")
    scope = "per_goal" if goal else "cross_goal"
    mode = "server" if server else None
    try:
        pkt = await curate(scope=scope, goal=goal, bank=bank, mode=mode)
    except NoPostsError:
        return {"ok": False, "error": "Run /self-distill first!"}
    except CurationError as exc:
        return {"ok": False, "error": f"curation failed (resumable): {exc}"}
    return {
        "ok": True,
        "bundle_id": pkt.id,
        "scope": pkt.scope,
        "goal": pkt.goal,
        "curator": pkt.curator,
        "parents": list(pkt.parents),
        "bucket_counts": {k: len(v) for k, v in (pkt.bundle or {}).items()},
    }


@app.tool()
async def inject_preview(packet: str | None = None) -> dict[str, Any]:
    """Head/tail token preview of a distill bundle. **Does not persist.** The
    host LLM should call this first, show the preview in chat, then call
    ``inject_commit`` only on user assent — at which point the agent's native
    MCP permission prompt fires (the real human gate)."""
    from manyagent.bank import get_bank
    from manyagent.utils import config

    sid = _session_id()
    bank = get_bank()
    if packet:
        pid = packet.lstrip("@")
    else:
        distills = await bank.list_packets(type="distill", include_quarantined=False)
        if not distills:
            return {"ok": False, "error": "no distill bundle — call cross_distill first"}
        pid = str(distills[-1]["id"])
    rec = await bank.get_packet(pid)
    if rec is None:
        return {"ok": False, "error": f"no packet {pid!r}"}
    if rec.get("quarantined"):
        return {"ok": False, "error": f"refused: {pid} is quarantined"}

    import json as _json

    bundle_text = _json.dumps(rec.get("bundle", {}), indent=2)
    toks = bundle_text.split()
    head = config.MANYAGENT_INJECT_PREVIEW_HEAD_TOKENS
    tail = config.MANYAGENT_INJECT_PREVIEW_TAIL_TOKENS
    if len(toks) <= head + tail:
        preview = bundle_text
    else:
        preview = f"{' '.join(toks[:head])} … [elided {len(toks) - head - tail} tokens] … {' '.join(toks[-tail:])}"
    return {
        "ok": True,
        "packet_id": pid,
        "target_session": sid,
        "scope": rec.get("scope"),
        "preview": preview,
        "commit_via": f"inject_commit(packet={pid!r})",
    }


@app.tool()
async def inject_commit(packet: str) -> dict[str, Any]:
    """Inject a distill bundle into the active session: writes an
    `injections` ledger row so behavioural reuse can score it later. **This
    is the human gate** — the host agent's MCP permission prompt fires on
    this call; the user has already seen the preview from `inject_preview`.
    Quarantined packets are refused."""
    from manyagent.bank import get_bank

    sid = _session_id()
    bank = get_bank()
    pid = packet.lstrip("@")
    rec = await bank.get_packet(pid)
    if rec is None:
        return {"ok": False, "error": f"no packet {pid!r}"}
    if rec.get("quarantined"):
        return {"ok": False, "error": f"refused: {pid} is quarantined (excluded from inject)"}
    await bank.record_injection(pid, sid)
    return {"ok": True, "packet_id": pid, "target_session": sid}


def main() -> None:
    """Run the MCP server over stdio (the transport every host expects)."""
    app.run()


if __name__ == "__main__":  # pragma: no cover — `python -m manyagent._mcp`
    main()
