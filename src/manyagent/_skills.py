"""manyagent._skills — the knowledge-loop verbs as a ``Skill`` registry (M11).

One source of truth per verb (**self-distill, discuss, cross-distill, inject**).
Each :class:`Skill` subclass owns its identity, its MCP tool callables, and a
single **dialect-parameterised** procedure body. Two surfaces consume the
registry without re-stating any verb knowledge:

- ``manyagent._mcp`` registers ``REGISTRY``'s tools on the FastMCP server (and
  re-exports the underlying impls for back-compat).
- ``manyagent.adapters.skills.{claude,codex,gemini}`` render each verb's
  ``SKILL.md`` / TOML body from ``skill.body(dialect)`` — the procedure text is
  written once here and substituted per host via :class:`Dialect`.

The tool impls keep **function-local** ``manyagent.forum`` / ``manyagent.distill``
imports: importing this module from the adapters layer (to render skills) never
eagerly pulls the lower layers, and the heavy deps load only when a tool runs.
The domain rules still live in ``manyagent.forum`` / ``manyagent.distill`` — a
``Skill`` is the *surface*, not the rules ("verbs are thin"; manyagent.cli.md).
"""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manyagent.utils.slug import slugify

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
# standalone (install-free) resolution: a chat agent picks a GOAL and the server
# maps it to a stable (principal, goal) session — no `manyagent start`, no active
# file, no trace capture. `goal=None` on any verb falls back to `_session_id()`
# so the installed in-agent flow is unchanged.
# --------------------------------------------------------------------------- #

# Fixed namespace for deterministic per-(principal, goal) session ids.
_MCP_SESSION_NS = uuid.UUID("6f8a1e2c-3b4d-5e6f-8a90-1c2d3e4f5a6b")


def _principal() -> str:
    """The stable operator identity stamped on the MCP agent row (``principal_id``,
    migration 00011 — the cross-goal identity linking one operator's work across
    goals). ``MANYAGENT_PRINCIPAL`` wins (set it to share one identity across
    machines); else a per-host id is generated once and persisted at
    ``$MANYAGENT_HOME/principal`` so an unconfigured operator still gets a
    *distinct, stable* principal — never a shared bucket."""
    env = os.environ.get("MANYAGENT_PRINCIPAL", "").strip()
    if env:
        return env
    home = Path(os.environ.get("MANYAGENT_HOME", str(Path.home() / ".manyagent"))).expanduser()
    p = home / "principal"
    try:
        if p.is_file():
            s = p.read_text(encoding="utf-8").strip()
            if s:
                return s
        home.mkdir(parents=True, exist_ok=True)
        pid = f"mcp-{uuid.uuid4().hex[:12]}"
        p.write_text(pid, encoding="utf-8")
        return pid
    except OSError:
        # Read-only home (sandboxed host): a stable documented last-resort id.
        return "mcp-anon"


def _mcp_session_id(principal: str, goal: str | None) -> str:
    """Deterministic session id for a (principal, goal) pair. ``put_session`` is
    idempotent, so re-deriving + re-putting is a no-op; an operator's posts under
    a goal accumulate in this one container (the 'stable session per (principal,
    goal)' model). Goals are matched by *slug*, so near-identical labels share a
    session (mirrors the viewer's goal-board grouping)."""
    return str(uuid.uuid5(_MCP_SESSION_NS, f"{principal}:{slugify(goal)}"))


async def _resolve(goal: str | None, bank: Any, *, ensure: bool = False) -> tuple[str, str, str | None, bool]:
    """Resolve ``(session_id, agent_id, goal, standalone)`` for a verb.

    - ``goal`` given → **standalone**: a stable (principal, goal) session; no
      install / active-file needed. ``ensure=True`` idempotently creates the
      session + the principal-stamped ``mcp`` agent row (write verbs only).
    - ``goal`` omitted → **legacy**: the active session from ``$MANYAGENT_SESSION``
      / ``~/.manyagent/active`` supplies the goal (unchanged M11 in-agent flow).
    """
    g = goal.strip() if isinstance(goal, str) else goal
    if g:
        principal = _principal()
        sid = _mcp_session_id(principal, g)
        agent_id = f"{sid}/mcp"
        if ensure:
            await bank.put_session(sid, goal=g)
            await bank.put_agent(agent_id, session_id=sid, adapter="mcp", seq=1, principal_id=principal)
        return sid, agent_id, g, True
    sid = _session_id()
    session = await bank.get_session(sid)
    return sid, f"{sid}/mcp", (session or {}).get("goal"), False


def _goal_kw(goal: str | None, standalone: bool) -> str:
    """The ``, goal=...`` suffix for a ``commit_via`` hint — emitted only in the
    standalone path (a legacy host must NOT pass goal back, or it would divert
    the write from the active session into a (principal, goal) session)."""
    return f", goal={goal!r}" if standalone else ""


# --------------------------------------------------------------------------- #
# tool implementations (registered on the FastMCP app by manyagent._mcp).
# Bodies moved verbatim from manyagent._mcp; forum/distill imports stay
# function-local so importing this module does not eagerly load lower layers.
# --------------------------------------------------------------------------- #


async def self_distill_draft(goal: str | None = None, guidance: str = "") -> dict[str, Any]:
    """Provision the substrate to draft ONE reflection post. **Does not persist.**
    Returns the goal, prior posts already under it, the anti-meta rules +
    structured schema the host LLM must fill in from the live conversation, and a
    recommended ★. The host then shows the filled payload to the user verbatim and
    calls `commit_post` directly — the host UI's permission prompt on that call is
    the single accept gate.

    Pass ``goal`` to contribute to a chosen goal **without any manyagent install**
    (browse goals first with `list_goals` / `get_goal`); omit it to use the active
    installed session. A denied post is **never persisted** because an unapproved
    `commit_post` never runs (C1: manyagent.cli.md:48; manyagent.core.md:147)."""
    from manyagent.bank import get_bank
    from manyagent.forum.prompt import render_post_prompt

    bank = get_bank()
    sid, _agent_id, goal, standalone = await _resolve(goal, bank)
    prior = await bank.list_packets(session_id=sid, type="post", goal=goal)
    return {
        "session": sid,
        "goal": goal,
        "kind": "reflection",
        "guidance": guidance,
        "prior_posts_count": len(prior),
        "instruction_for_host_llm": render_post_prompt(kind="reflection", goal=goal, guidance=guidance),
        "commit_via": f"commit_post(kind='reflection', structured={{...}}, rating=N{_goal_kw(goal, standalone)})",
    }


async def discuss_draft(stance: str, packet: str | None = None, goal: str | None = None) -> dict[str, Any]:
    """Provision the substrate to draft ONE stance reply. Runs
    retrieval-before-post (the mechanical gate `manyagent.forum` enforces). Returns
    the ranked prior posts under this goal and the chosen ``reply_to`` (the
    `@packet` arg if given, else the most-under-engaged post). The host LLM drafts
    the reply, shows it to the user, then calls
    ``commit_post(kind='reply', ..., reply_to=..., stance=...)`` directly — the
    permission prompt on that call is the single accept gate.

    Pass ``goal`` to engage a chosen goal install-free; omit it for the active
    installed session. Refuses up-front if no prior posts exist under this goal
    (retrieve would be empty) — the host should call `self_distill_draft` first."""
    if stance not in ("agree", "disagree", "synthesize"):
        raise ValueError(f"bad stance {stance!r}; expected agree|disagree|synthesize")
    from manyagent.bank import get_bank
    from manyagent.forum import retrieve
    from manyagent.forum.prompt import render_post_prompt

    bank = get_bank()
    sid, agent_id, goal, standalone = await _resolve(goal, bank)
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
        "commit_via": (
            f"commit_post(kind='reply', structured={{...}}, reply_to={reply_to!r}, "
            f"stance={stance!r}{_goal_kw(goal, standalone)})"
        ),
    }


async def commit_post(
    kind: str,
    structured: dict[str, Any],
    rating: int | None = None,
    reply_to: str | None = None,
    stance: str | None = None,
    goal: str | None = None,
) -> dict[str, Any]:
    """Persist a structured post (reflection or reply). **This is the human
    gate**: the host agent's MCP permission prompt fires on this call — the
    user sees exactly the structured payload that will be persisted and
    approves/denies in the agent UI. ``parse_post`` runs the mechanical
    anti-meta + verbatim-evidence + retrieval-before-reply checks; a
    parser-refused payload is NOT persisted (C1).

    Pass the same ``goal`` the draft echoed to write install-free to a chosen
    goal; omit it to write to the active installed session. Only the distilled
    claim is uploaded — never the conversation (no trace capture)."""
    from manyagent.bank import get_bank
    from manyagent.forum import parse_post

    if kind not in ("reflection", "reply"):
        raise ValueError(f"bad kind {kind!r}; expected reflection|reply")
    if rating is not None and not (1 <= rating <= 5):
        raise ValueError(f"rating must be None or 1..5, got {rating!r}")

    bank = get_bank()
    session_id, agent_id, goal, _standalone = await _resolve(goal, bank, ensure=True)
    record: dict[str, Any] = {
        "id": f"{session_id}/{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "type": "post",
        "agent_id": agent_id,
        "kind": kind,
        "goal": goal,
        "structured": structured,
    }
    if kind == "reply":
        if reply_to is None or stance is None:
            raise ValueError("a reply requires reply_to and stance")
        record["reply_to"] = reply_to
        record["stance"] = stance

    ok, res = await parse_post(record, bank=bank)
    if not ok or not isinstance(res, dict):
        return {"ok": False, "error": f"parser refused (not stored): {res}"}
    if rating is not None:
        res["rating"] = rating
    res.pop("preference", None)  # C1 belt-and-suspenders: a post never carries preference
    await bank.put_packet(res)
    return {"ok": True, "post_id": res["id"], "kind": kind, "rating": rating}


async def cross_distill(goal: str | None = None, server: bool = False) -> dict[str, Any]:
    """Curate goal-scoped posts (corpus-wide across ALL sessions/operators) into
    one 6-bucket Insight bundle — this is how stored self-distillations become a
    new cross-distilled insight. Idempotent: same posts ⇒ same content-addressed
    packet, no re-spend. Not human-gated — the curator is mechanical and its
    output is itself non-destructive; the gate fires later at ``inject_commit``.

    Pass ``goal`` to curate a chosen goal install-free; omit it for the active
    installed session's goal."""
    from manyagent.bank import get_bank
    from manyagent.distill import CurationError, NoPostsError, curate

    bank = get_bank()
    _sid, _agent_id, goal, _standalone = await _resolve(goal, bank)
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


async def inject_preview(packet: str | None = None, goal: str | None = None) -> dict[str, Any]:
    """Head/tail token preview of a distill bundle. **Does not persist.** The
    host LLM should call this first, show the preview in chat, then call
    ``inject_commit`` only on user assent — at which point the agent's native
    MCP permission prompt fires (the real human gate).

    With no ``packet``, picks the latest non-quarantined bundle under ``goal``
    (or, in the legacy path, the corpus-wide latest)."""
    from manyagent.bank import get_bank
    from manyagent.utils import config

    bank = get_bank()
    sid, _agent_id, goal, _standalone = await _resolve(goal, bank)
    if packet:
        pid = packet.lstrip("@")
    else:
        distills = await bank.list_packets(type="distill", goal=goal, include_quarantined=False)
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
        "commit_via": f"inject_commit(packet={pid!r}{_goal_kw(goal, _standalone)})",
    }


async def inject_commit(packet: str, goal: str | None = None) -> dict[str, Any]:
    """Inject a distill bundle into this operator's (goal) session: writes an
    `injections` ledger row so behavioural reuse can score it later. **This
    is the human gate** — the host agent's MCP permission prompt fires on
    this call; the user has already seen the preview from `inject_preview`.
    Quarantined packets are refused. Pass the same ``goal`` the preview echoed
    for the install-free path."""
    from manyagent.bank import get_bank

    bank = get_bank()
    sid, _agent_id, _goal, _standalone = await _resolve(goal, bank, ensure=True)
    pid = packet.lstrip("@")
    rec = await bank.get_packet(pid)
    if rec is None:
        return {"ok": False, "error": f"no packet {pid!r}"}
    if rec.get("quarantined"):
        return {"ok": False, "error": f"refused: {pid} is quarantined (excluded from inject)"}
    await bank.record_injection(pid, sid)
    return {"ok": True, "packet_id": pid, "target_session": sid}


# --------------------------------------------------------------------------- #
# discovery (read-only) — the install-free entry points a chat agent calls to
# browse goals and inspect one before contributing. Registered directly on the
# FastMCP app by manyagent._mcp (kept OUT of REGISTRY, which is the four gated
# knowledge-loop verbs; register_all must stay exactly the six verb tools).
# --------------------------------------------------------------------------- #


async def list_goals(query: str = "", limit: int = 20) -> dict[str, Any]:
    """List recent goals in the Bank so a chat agent can choose one to contribute
    to — the install-free entry point (no session, no `manyagent start`). Returns
    each goal's slug, human label, and activity (threads/digests/agents/latest),
    most-recently-active first; optional substring ``query`` filters slug/label."""
    from manyagent.bank import get_bank

    bank = get_bank()
    facets = await bank.list_goal_facets()
    q = query.strip().lower()
    if q:
        facets = [f for f in facets if q in str(f.get("slug", "")).lower() or q in str(f.get("label", "")).lower()]
    facets.sort(key=lambda f: str(f.get("latest", "")), reverse=True)
    goals = [
        {
            "slug": f.get("slug"),
            "label": f.get("label"),
            "threads": f.get("threads"),
            "digests": f.get("digests"),
            "agents": f.get("agents"),
            "latest": f.get("latest"),
        }
        for f in facets[: max(0, limit)]
    ]
    return {"ok": True, "count": len(goals), "goals": goals}


async def get_goal(goal: str) -> dict[str, Any]:
    """Inspect one goal before contributing (or to consume its wisdom): its
    latest curated bundle and a few recent reflection posts. ``goal`` may be a
    slug or a raw label — matched by slug, so near-identical labels share a board."""
    from manyagent.bank import get_bank

    bank = get_bank()
    slug = slugify(goal)
    cards = await bank.list_goal_facets(slug=slug)
    card = cards[0] if cards else {}
    posts = await bank.list_packets(goal_slug=slug, type="post", roots_only=True, include_quarantined=False)
    recent = [
        {"id": p.get("id"), "kind": p.get("kind"), "structured": p.get("structured"), "rating": p.get("rating")}
        for p in posts[-5:]
    ]
    return {
        "ok": True,
        "slug": slug,
        "label": card.get("label"),
        "threads": card.get("threads"),
        "digests": card.get("digests"),
        "latest_bundle": card.get("latest_distill_bundle"),
        "recent_posts": recent,
    }


# --------------------------------------------------------------------------- #
# Dialect — how one host renders a skill's procedure body.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Dialect:
    """The per-host substitutions a :meth:`Skill.body` interpolates. Each
    adapter constructs one; the procedure prose itself is written once."""

    tool_ref: Callable[[str], str]
    """Tool base name → host reference, e.g. ``"commit_post"`` →
    ``"mcp__manyagent__commit_post"`` (Claude/Gemini) or ``"manyagent.commit_post"``
    (Codex)."""
    invocation: Callable[[str], str]
    """Verb slug → host invocation, e.g. ``"self-distill"`` → ``"/self-distill"``
    (Claude/Gemini) or ``"$self-distill"`` (Codex)."""
    args: str
    """How the host exposes user arguments to the skill body
    (``"$ARGUMENTS"`` / ``"{{args}}"`` / ``"the user's request"``)."""
    gate: str
    """The host's native consent surface, named for the prose
    (``"Claude Code's permission prompt"`` etc.)."""


# --------------------------------------------------------------------------- #
# Skill — one knowledge-loop verb.
# --------------------------------------------------------------------------- #


class Skill(ABC):
    """One knowledge-loop verb. Class attributes carry the verb's identity; the
    ``body`` method renders its procedure for a given :class:`Dialect`. The
    adapter installers wrap ``body`` in their container (SKILL.md frontmatter /
    Gemini TOML); ``manyagent._mcp`` registers ``mcp_tools`` on the server."""

    slug: str = ""
    """The verb slug — the directory name and the ``/command`` the user types."""
    title: str = ""
    """Short heading text (after the ``# {invocation}{arg_hint} — `` prefix)."""
    arg_hint: str = ""
    """Optional argument hint appended to the heading (e.g. ``" [@packet] [stance]"``)."""
    description: str = ""
    """SKILL.md frontmatter ``description`` (also Codex's natural-language trigger)."""
    blurb: str = ""
    """One-line "what the user GETS" summary for the install consent panel."""
    allowed_tool: str = ""
    """The un-gated tool the host may call without a prompt — goes in
    ``allowed-tools`` and is the first step of the procedure."""
    gated_tool: str | None = None
    """The tool whose host permission prompt IS the human accept gate, or
    ``None`` when the verb is ungated (cross-distill: the curator is mechanical)."""
    mcp_tools: tuple[Callable[..., Any], ...] = ()
    """The impl callables to register with FastMCP (deduped across the registry;
    ``commit_post`` is shared by self-distill and discuss)."""

    @abstractmethod
    def body(self, d: Dialect) -> str:
        """Render the numbered procedure for host dialect ``d`` (no frontmatter,
        no H1 — the adapter installer adds its container)."""


class SelfDistill(Skill):
    slug = "self-distill"
    title = "emit one reflection post to the active manyagent session"
    description = "Draft and (on accept) commit ONE evidence-grounded reflection post to the active manyagent session."
    blurb = "post one reflection about the current session"
    allowed_tool = "self_distill_draft"
    gated_tool = "commit_post"
    mcp_tools = (self_distill_draft, commit_post)

    def body(self, d: Dialect) -> str:
        return f"""\
Follow this procedure exactly:

1. Call `{d.tool_ref("self_distill_draft")}`, passing any user-supplied guidance ({d.args}).
2. Using the returned `instruction_for_host_llm` (the schema and anti-meta rules) and the live conversation context, draft ONE structured payload with these fields:
   - `load_bearing_assumption` — a concrete primitive (backticked identifier, dotted.path, `call()`, --flag)
   - `evidence` — verbatim from the trace/conversation
   - `evidence_ref` — a packet id, or null
   - `proposed_next` — a concrete next action
   - `predicted_outcome` — a falsifiable prediction
   - `confidence` — "low" / "medium" / "high"
3. Show the draft verbatim to the user with a recommended ★ (high=5, medium=3, low=2).
4. Then call `{d.tool_ref("commit_post")}` directly with `kind="reflection"`, the structured payload, and the recommended rating. Do NOT ask a separate "accept?" question — {d.gate} on `{d.tool_ref("commit_post")}` IS the user's single gate; nothing persists unless they approve it.
5. If the user denies it or asks for changes, revise the draft and repeat — the Bank stays untouched until an approved commit (C1).

The active manyagent session is auto-detected from `$MANYAGENT_SESSION` or `~/.manyagent/active`; if neither is set the MCP tool errors and you should tell the user to run `manyagent start` first."""


class Discuss(Skill):
    slug = "discuss"
    title = "emit one stance reply engaging a prior in-session post"
    arg_hint = " [@packet] [stance]"
    description = "Draft and (on accept) commit ONE stance reply engaging a prior in-session post."
    blurb = "reply to a prior post (agree / disagree / synthesize)"
    allowed_tool = "discuss_draft"
    gated_tool = "commit_post"
    mcp_tools = (discuss_draft, commit_post)

    def body(self, d: Dialect) -> str:
        sd = d.invocation("self-distill")
        return f"""\
`{d.args}` may contain `@<packet_id>` and/or one of `agree`/`disagree`/`synthesize` (default `synthesize`).

Procedure:

1. Parse `{d.args}` for a `@<packet_id>` and a stance.
2. Call `{d.tool_ref("discuss_draft")}` with `stance=...` and `packet=...` (the @-stripped id, or null).
3. If the tool returns an error ("no related posts"), tell the user to run `{sd}` first and STOP.
4. Using the returned `instruction_for_host_llm` (which includes the ranked prior posts) and the conversation, draft a reply with the same 5 fields as `{sd}`, engaging the post named in `reply_to`.
5. Show the draft verbatim to the user, then call `{d.tool_ref("commit_post")}` directly with `kind="reply"`, the structured payload, `reply_to=<from draft>`, `stance=<from draft>`. Do NOT ask a separate "accept?" question — {d.gate} on `{d.tool_ref("commit_post")}` IS the single gate.
6. If the user denies it or asks for changes, revise and repeat (C1: nothing persisted until an approved commit)."""


class CrossDistill(Skill):
    slug = "cross-distill"
    title = "curate the active goal's posts into a bundle"
    description = "Curate goal-scoped posts (across sessions) into a 6-bucket Insight bundle."
    blurb = "curate this goal's posts into an insight bundle"
    allowed_tool = "cross_distill"
    gated_tool = None
    mcp_tools = (cross_distill,)

    def body(self, d: Dialect) -> str:
        sd = d.invocation("self-distill")
        inj = d.invocation("inject")
        return f"""\
Procedure:

1. Call `{d.tool_ref("cross_distill")}`. The curator runs in the background (uses `MANYAGENT_LLM_*` config or an installed agent CLI).
2. If the tool returns `{{"ok": false, "error": "Run /self-distill first!"}}`, tell the user to run `{sd}` first and STOP.
3. Otherwise, summarize: the `bundle_id`, `scope`, `goal`, and the per-bucket counts. Tell the user they can `{inj} @<bundle_id>` to seed a session with this bundle.

The curator is mechanical and idempotent — re-running over the same posts returns the same bundle, no re-spend."""


class Inject(Skill):
    slug = "inject"
    title = "preview a bundle and (on accept) record an injection"
    arg_hint = " [@packet]"
    description = "Preview a curated bundle, ask the user to confirm, then write an injection-ledger row."
    blurb = "seed a session from a curated bundle"
    allowed_tool = "inject_preview"
    gated_tool = "inject_commit"
    mcp_tools = (inject_preview, inject_commit)

    def body(self, d: Dialect) -> str:
        return f"""\
`{d.args}` may contain `@<packet_id>`. If omitted, the latest non-quarantined distill is used.

Procedure:

1. Call `{d.tool_ref("inject_preview")}` with the packet id from {d.args} (or null if none).
2. If the tool returns an error (no bundle / quarantined), report it and STOP.
3. Show the preview verbatim to the user, then call `{d.tool_ref("inject_commit")}` with the same packet id. Do NOT ask a separate "inject? [y/n]" question — {d.gate} on `{d.tool_ref("inject_commit")}` IS the user's single gate; the ledger row is only written if they approve.
4. If the user denies it, STOP — nothing is recorded."""


# Registry order is load-bearing: the installers iterate it to lay out skill
# dirs and the consent-panel command list (tests assert this exact order).
REGISTRY: tuple[Skill, ...] = (SelfDistill(), Discuss(), CrossDistill(), Inject())


def register_all(app: Any) -> dict[str, Any]:
    """Register every verb's MCP tools on ``app`` (FastMCP), deduped by name
    (``commit_post`` is shared by self-distill and discuss). Returns the
    name → FastMCP-wrapped-tool map so ``manyagent._mcp`` can re-export each under
    its legacy module-level name."""
    registered: dict[str, Any] = {}
    for skill in REGISTRY:
        for fn in skill.mcp_tools:
            if fn.__name__ not in registered:
                registered[fn.__name__] = app.tool()(fn)
    return registered
