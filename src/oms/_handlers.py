"""oms._handlers — the four knowledge-loop verbs as plain async functions.

These used to live in ``oms.cli`` as ``_do_self_distill`` / ``_do_discuss`` /
``_do_cross_distill`` / ``_do_inject`` driven by argparse Namespaces. M11.4
hoists them out: the in-agent skills surface (oms._mcp) is the user-facing
path; ``oms.cli`` keeps only the *session lifecycle* verbs (``start`` /
``register`` / ``<name>`` / ``end`` / ``status`` / ``uninstall``).

Signatures are **plain kwargs** (no argparse Namespace coupling). Tests,
``scripts/simulate_story.py``, and any future programmatic caller use these
functions directly. The MCP server (``oms._mcp``) is a different surface —
its tools call into ``oms.forum`` / ``oms.distill`` / ``oms.bank`` for the
same effects, but the *gating* (C1 accept) lives in the agent UI's
permission prompt, not in this module's ``ask_yn``/``ask_rating`` calls.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from oms.bank import Bank
from oms.utils import config, messages, sid

# These three helpers stay in oms.cli (CLI-state and prompt helpers); import
# them lazily inside handlers to avoid circular import at module load.


async def parse_post_safely(record: dict[str, Any], *, bank: Bank) -> tuple[bool, dict[str, Any] | str]:
    from oms.forum import parse_post

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


def _adapter_cls(name: str) -> Any:
    from oms.adapters import resolve as resolve_adapter

    try:
        return resolve_adapter(name)
    except Exception as exc:  # registry: local → builtin → hub; not found
        raise SystemExit(f"unknown adapter {name!r}: {exc}") from exc


def _adapter_for(name: str, *, session_id: str, agent_id: str) -> Any:
    return _adapter_cls(name)(session_id=session_id, agent_id=agent_id)


def _validate_adapter(name: str) -> None:
    """Gate before minting a new agent row: ``name`` must resolve in the
    registry AND its wrapped binary must be on PATH — otherwise ``register``
    happily persists agents for CLIs that can never run (decision
    2026-06-10). ``oms.testing.Simulation`` patches this seam alongside
    ``_adapter_for``."""
    cls = _adapter_cls(name)
    if not cls.is_available():
        raise SystemExit(
            f"adapter {name!r} resolved but its CLI {cls.binary or name!r} is not on PATH — install it first"
        )


async def _resolve_agent(sid_: str, name: str, *, bank: Bank) -> str:
    """Latest registered agent for ``name`` in the session, else auto-register
    one (``register`` is explicit but optional — oms.cli.md)."""
    agents = [a for a in await bank.list_agents(sid_) if a.get("adapter") == name]
    if agents:
        return str(agents[-1]["id"])
    _validate_adapter(name)
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
    """Shared single-gate commit flow for a ``reflection``/``reply`` post:
    one allowance prompt carries both the commit decision and the ★ (user
    decision 2026-06-10 — no separate accept/reject question).
    **C1**: a declined or parser-refused post is NOT persisted (the record
    never carries ``preference``); the caller re-prompts."""
    from oms.cli import _noninteractive, ask_allow, ask_commit

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
        io[1](messages.POST_REJECTED_BY_DISCIPLINE.format(reason=res))
        return 1  # caller re-prompts (C1: nothing persisted)

    io[1](messages.POST_PROPOSED_HEADER)
    io[1](json.dumps(res.get("structured", {}), indent=2))
    # The commit gate is NOT deny-by-default: the mechanical parser already
    # gated quality, so an unattended (OMS_NONINTERACTIVE) run auto-commits —
    # the open-ended loop must keep running with no human present. Deny-by-
    # default (Open-Q §B5) is scoped to /inject + destructive confirms, not to
    # the agent's own parser-validated post (oms.cli.md: noninteractive →
    # unrated + no inject; it does not gate /self-distill).
    if ask_star:
        proposed = res.get("structured", {}).get("confidence")
        prop = {"high": 5, "medium": 3, "low": 2}.get(str(proposed), 3)
        accepted, rating = ask_commit(prop, input_fn=io[0], output_fn=io[1], noninteractive=_noninteractive())
        if accepted and rating is not None:
            res["rating"] = rating
    else:
        accepted = _noninteractive() or ask_allow(
            messages.REPLY_COMMIT_OFFER, input_fn=io[0], output_fn=io[1], noninteractive=False
        )
    if not accepted:
        io[1](messages.POST_DISCARDED)
        return 1  # C1: NOT stored, no preference key

    res.pop("preference", None)  # C1 belt-and-suspenders: a post never carries it
    await bank.put_packet(res)
    io[1](messages.POST_STORED.format(post_id=res["id"]))
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
    from oms.cli import _resolve_sid
    from oms.forum import render_post_prompt

    sid_ = _resolve_sid(session)
    session_row = await bank.get_session(sid_)
    goal = (session_row or {}).get("goal")
    agent_id = await _resolve_agent(sid_, adapter, bank=bank)
    adapter_obj = _adapter_for(adapter, session_id=sid_, agent_id=agent_id)
    prompt = render_post_prompt(kind="reflection", goal=goal, guidance=guidance)
    structured = await _agent_json(adapter_obj, prompt)
    if structured is None:
        io[1](messages.NO_PARSEABLE_POST)
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
    from oms.cli import _resolve_sid
    from oms.forum import enforce_retrieved_before_reply, render_post_prompt, retrieve

    sid_ = _resolve_sid(session)
    session_row = await bank.get_session(sid_)
    goal = (session_row or {}).get("goal")
    agent_id = await _resolve_agent(sid_, adapter, bank=bank)
    adapter_obj = _adapter_for(adapter, session_id=sid_, agent_id=agent_id)

    ranked = await retrieve(sid_, agent_id=agent_id, goal=goal, bank=bank)  # retrieval-before-post
    if not ranked:
        io[1](messages.DISCUSS_NO_POSTS)
        return 1
    reply_to = packet.lstrip("@") if packet else str(ranked[0]["id"])
    reason = enforce_retrieved_before_reply(sid_, agent_id, reply_to)
    if reason is not None:
        io[1](messages.DISCUSS_REFUSED.format(reason=reason))
        return 1  # not persisted (C1)
    prompt = render_post_prompt(kind="reply", goal=goal, prior_posts=ranked)
    structured = await _agent_json(adapter_obj, prompt)
    if structured is None:
        io[1](messages.NO_PARSEABLE_REPLY)
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
    from oms.cli import _resolve_sid
    from oms.distill import CurationError, NoPostsError, curate

    sid_ = _resolve_sid(session)
    session_row = await bank.get_session(sid_)
    goal = (session_row or {}).get("goal")
    if goal == config.resolve("OMS_DEFAULT_GOAL", config.OMS_DEFAULT_GOAL):
        goal = None  # the default bucket is the catch-all, not a curated goal
    scope = "per_goal" if goal else "cross_goal"
    mode = "server" if server else None
    try:
        pkt = await curate(scope=scope, goal=goal, bank=bank, mode=mode)
    except NoPostsError as exc:
        io[1](str(exc))  # exact "Run /self-distill first!"
        return 1
    except CurationError as exc:
        io[1](messages.CURATION_FAILED.format(reason=exc))
        return 1
    io[1](messages.CURATED_BUNDLE.format(scope=pkt.scope, bundle_id=pkt.id, curator=pkt.curator))
    return 0


async def do_inject(
    *,
    packet: str | None = None,
    session: str | None = None,
    bank: Bank,
    io: tuple[Any, Any],
) -> int:
    from oms.cli import _noninteractive, _resolve_sid, ask_allow, preview_tokens

    sid_ = _resolve_sid(session)
    if packet:
        pid = packet.lstrip("@")
    else:
        distills = await bank.list_packets(type="distill", include_quarantined=False)
        if not distills:
            io[1](messages.INJECT_NOTHING)
            return 1
        pid = str(distills[-1]["id"])
    rec = await bank.get_packet(pid)
    if rec is None:
        io[1](messages.INJECT_UNKNOWN_PACKET.format(packet_id=pid))
        return 1
    if rec.get("quarantined"):
        io[1](messages.INJECT_QUARANTINED.format(packet_id=pid))
        return 1  # refused BEFORE preview
    bundle_text = json.dumps(rec.get("bundle", {}), indent=2)
    io[1](messages.INJECT_PREVIEW_HEADER)
    io[1](
        preview_tokens(
            bundle_text,
            head=config.OMS_INJECT_PREVIEW_HEAD_TOKENS,
            tail=config.OMS_INJECT_PREVIEW_TAIL_TOKENS,
        )
    )
    if not ask_allow(
        messages.INJECT_OFFER.format(packet_id=pid, session_id=sid_),
        input_fn=io[0],
        output_fn=io[1],
        noninteractive=_noninteractive(),
    ):
        io[1](messages.INJECT_DECLINED)
        return 1
    await bank.record_injection(pid, sid_)
    io[1](messages.INJECT_RECORDED.format(packet_id=pid, session_id=sid_))
    return 0
