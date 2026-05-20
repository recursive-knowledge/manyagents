"""oma._handlers — the four knowledge-loop verbs as plain async functions.

These used to live in ``oma.cli`` as ``_do_self_distill`` / ``_do_discuss`` /
``_do_cross_distill`` / ``_do_inject`` driven by argparse Namespaces. M11.4
hoists them out: the in-agent skills surface (oma._mcp) is the user-facing
path; ``oma.cli`` keeps only the *session lifecycle* verbs (``start`` /
``register`` / ``<name>`` / ``end`` / ``status`` / ``uninstall``).

Signatures are **plain kwargs** (no argparse Namespace coupling). Tests,
``scripts/simulate_story.py``, and any future programmatic caller use these
functions directly. The MCP server (``oma._mcp``) is a different surface —
its tools call into ``oma.forum`` / ``oma.distill`` / ``oma.bank`` for the
same effects, but the *gating* (C1 accept) lives in the agent UI's
permission prompt, not in this module's ``ask_yn``/``ask_rating`` calls.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from oma.bank import Bank
from oma.utils import sid

# These three helpers stay in oma.cli (CLI-state and prompt helpers); import
# them lazily inside handlers to avoid circular import at module load.


async def parse_post_safely(record: dict[str, Any], *, bank: Bank) -> tuple[bool, dict[str, Any] | str]:
    from oma.forum import parse_post

    return await parse_post(record, bank=bank)


async def _agent_json(adapter: Any, prompt: str) -> Any:
    """Shell the adapter's own headless model for the structured post. The
    model's ``.complete`` is synchronous (a CLI shell-out) so it is run via
    ``asyncio.to_thread`` — calling it directly would block the loop (the M5
    async-wrapper hazard)."""
    model = adapter.distill_model()
    if model is None:
        raise SystemExit(f"{adapter.name}: no headless model available; cannot generate a post")
    fn = model.complete
    raw = await fn(prompt) if asyncio.iscoroutinefunction(fn) else await asyncio.to_thread(fn, prompt)
    raw = str(raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        a, b = raw.find("{"), raw.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(raw[a : b + 1])
            except json.JSONDecodeError:
                return None
    return None


def _adapter_for(name: str, *, session_id: str, agent_id: str) -> Any:
    from oma.adapters import resolve as resolve_adapter

    try:
        cls = resolve_adapter(name)
    except Exception as exc:  # registry: local → builtin → hub; not found
        raise SystemExit(f"unknown adapter {name!r}: {exc}") from exc
    return cls(session_id=session_id, agent_id=agent_id)


async def _resolve_agent(sid_: str, name: str, *, bank: Bank) -> str:
    """Latest registered agent for ``name`` in the session, else auto-register
    one (``register`` is explicit but optional — oma.cli.md)."""
    agents = [a for a in await bank.list_agents(sid_) if a.get("adapter") == name]
    if agents:
        return str(agents[-1]["id"])
    seq = await bank.next_agent_seq(sid_)
    agent_id = f"{sid_}/agent-{seq:03d}-{name}"
    await bank.put_agent(agent_id, session_id=sid_, adapter=name, seq=seq)
    return agent_id


async def _emit_post(
    *,
    kind: str,
    sid_: str,
    agent_id: str,
    goal: str | None,
    structured: Any,
    reply_to: str | None,
    stance: str | None,
    bank: Bank,
    io: tuple[Any, Any],
    ask_star: bool,
) -> int:
    """Shared accept/reject + ★ flow for a ``reflection``/``reply`` post.
    **C1**: a rejected or parser-refused post is NOT persisted (the record
    never carries ``preference``); the caller re-prompts."""
    from oma.cli import _noninteractive, ask_rating, ask_yn

    record: dict[str, Any] = {
        "id": f"{sid_}/{sid.new().replace('-', '').lower()[:8]}",
        "session_id": sid_,
        "type": "post",
        "agent_id": agent_id,
        "kind": kind,
        "goal": goal,
        "structured": structured,
    }
    if kind == "reply":
        record["reply_to"] = reply_to
        record["stance"] = stance

    ok, res = await parse_post_safely(record, bank=bank)
    if not ok or not isinstance(res, dict):  # narrows res → dict for the rest
        io[1](f"post rejected by the discipline (not stored): {res}")
        return 1  # caller re-prompts (C1: nothing persisted)

    io[1]("--- proposed post ---")
    io[1](json.dumps(res.get("structured", {}), indent=2))
    # The accept gate is NOT deny-by-default: the mechanical parser already
    # gated quality, so an unattended (OMA_NONINTERACTIVE) run auto-accepts —
    # the open-ended loop must keep running with no human present. Deny-by-
    # default (Open-Q §B5) is scoped to /inject + destructive confirms, not to
    # the agent's own parser-validated post (oma.cli.md: noninteractive →
    # unrated + no inject; it does not gate /self-distill).
    accepted = (
        True
        if _noninteractive()
        else ask_yn("accept this post?", input_fn=io[0], output_fn=io[1], noninteractive=False)
    )
    if not accepted:
        io[1]("rejected — re-prompt the agent (not stored; C1)")
        return 1  # C1: NOT stored, no preference key

    if ask_star:
        proposed = res.get("structured", {}).get("confidence")
        prop = {"high": 5, "medium": 3, "low": 2}.get(str(proposed), 3)
        rating = ask_rating(prop, input_fn=io[0], output_fn=io[1], noninteractive=_noninteractive())
        if rating is not None:
            res["rating"] = rating

    res.pop("preference", None)  # C1 belt-and-suspenders: a post never carries it
    await bank.put_packet(res)
    io[1](f"stored post {res['id']}")
    return 0


# --------------------------------------------------------------------------- #
# the four knowledge-loop verbs (kwargs API — no argparse Namespace coupling)
# --------------------------------------------------------------------------- #


async def do_self_distill(
    *,
    adapter: str,
    guidance: str | None = None,
    session: str | None = None,
    bank: Bank,
    io: tuple[Any, Any],
) -> int:
    from oma.cli import _resolve_sid
    from oma.forum import render_post_prompt

    sid_ = _resolve_sid(session)
    session_row = await bank.get_session(sid_)
    goal = (session_row or {}).get("goal")
    agent_id = await _resolve_agent(sid_, adapter, bank=bank)
    adapter_obj = _adapter_for(adapter, session_id=sid_, agent_id=agent_id)
    prompt = render_post_prompt(kind="reflection", goal=goal, guidance=guidance)
    structured = await _agent_json(adapter_obj, prompt)
    if structured is None:
        io[1]("agent produced no parseable JSON post (not stored)")
        return 1
    return await _emit_post(
        kind="reflection",
        sid_=sid_,
        agent_id=agent_id,
        goal=goal,
        structured=structured,
        reply_to=None,
        stance=None,
        bank=bank,
        io=io,
        ask_star=True,
    )


async def do_discuss(
    *,
    adapter: str,
    stance: str = "synthesize",
    packet: str | None = None,
    session: str | None = None,
    bank: Bank,
    io: tuple[Any, Any],
) -> int:
    from oma.cli import _resolve_sid
    from oma.forum import enforce_retrieved_before_reply, render_post_prompt, retrieve

    sid_ = _resolve_sid(session)
    session_row = await bank.get_session(sid_)
    goal = (session_row or {}).get("goal")
    agent_id = await _resolve_agent(sid_, adapter, bank=bank)
    adapter_obj = _adapter_for(adapter, session_id=sid_, agent_id=agent_id)

    ranked = await retrieve(sid_, agent_id=agent_id, goal=goal, bank=bank)  # retrieval-before-post
    if not ranked:
        io[1]("no related posts to engage — run /self-distill first")
        return 1
    reply_to = packet.lstrip("@") if packet else str(ranked[0]["id"])
    reason = enforce_retrieved_before_reply(sid_, agent_id, reply_to)
    if reason is not None:
        io[1](f"/discuss refused: {reason}")
        return 1  # not persisted (C1)
    prompt = render_post_prompt(kind="reply", goal=goal, prior_posts=ranked)
    structured = await _agent_json(adapter_obj, prompt)
    if structured is None:
        io[1]("agent produced no parseable JSON reply (not stored)")
        return 1
    return await _emit_post(
        kind="reply",
        sid_=sid_,
        agent_id=agent_id,
        goal=goal,
        structured=structured,
        reply_to=reply_to,
        stance=stance,
        bank=bank,
        io=io,
        ask_star=False,
    )


async def do_cross_distill(
    *,
    server: bool = False,
    session: str | None = None,
    bank: Bank,
    io: tuple[Any, Any],
) -> int:
    from oma.cli import _resolve_sid
    from oma.distill import CurationError, NoPostsError, curate

    sid_ = _resolve_sid(session)
    session_row = await bank.get_session(sid_)
    goal = (session_row or {}).get("goal")
    scope = "per_goal" if goal else "cross_goal"
    mode = "server" if server else None
    try:
        pkt = await curate(scope=scope, goal=goal, bank=bank, mode=mode)
    except NoPostsError as exc:
        io[1](str(exc))  # exact "Run /self-distill first!"
        return 1
    except CurationError as exc:
        io[1](f"curation failed (nothing stored, resumable): {exc}")
        return 1
    io[1](f"curated {pkt.scope} bundle {pkt.id} (curator={pkt.curator}) — /inject @{pkt.id} to seed")
    return 0


async def do_inject(
    *,
    packet: str | None = None,
    session: str | None = None,
    bank: Bank,
    io: tuple[Any, Any],
) -> int:
    from oma.cli import _noninteractive, _resolve_sid, ask_yn, preview_tokens
    from oma.utils import config

    sid_ = _resolve_sid(session)
    if packet:
        pid = packet.lstrip("@")
    else:
        distills = await bank.list_packets(type="distill", include_quarantined=False)
        if not distills:
            io[1]("no distill bundle to inject — run /cross-distill first")
            return 1
        pid = str(distills[-1]["id"])
    rec = await bank.get_packet(pid)
    if rec is None:
        io[1](f"no packet {pid!r}")
        return 1
    if rec.get("quarantined"):
        io[1](f"refused: {pid} is quarantined (excluded from /inject)")
        return 1  # refused BEFORE preview
    bundle_text = json.dumps(rec.get("bundle", {}), indent=2)
    io[1]("--- inject preview ---")
    io[1](
        preview_tokens(
            bundle_text,
            head=config.OMA_INJECT_PREVIEW_HEAD_TOKENS,
            tail=config.OMA_INJECT_PREVIEW_TAIL_TOKENS,
        )
    )
    if not ask_yn(
        f"inject {pid} into session {sid_}?", input_fn=io[0], output_fn=io[1], noninteractive=_noninteractive()
    ):
        io[1]("inject declined")
        return 1
    await bank.record_injection(pid, sid_)
    io[1](f"injected {pid} → session {sid_} (injections row written)")
    return 0
