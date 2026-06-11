# CLAUDE.md

Operational truth for agents working in this repo (Design Principles §3). The
design docs in `docs/design/` are the *why*; this file is the *how*.

## What this is

`oms` wraps installed coding-agent CLIs, captures traces into a Supabase Bank,
curates cross-session knowledge (swarms-derived discipline), and serves a
read-only viewer. Distribution name `oh-my-swarm`, import name `oms`, single
console script `oms = "oms.cli:main"`. **Identity is fixed in `pyproject.toml`
+ `src/oms/__init__.py` and never re-derived as a string elsewhere.**

## Commands

```bash
make install   # uv sync --all-extras + editable install + pre-commit install
make check     # uv lock --locked; pre-commit run -a; mypy; deptry src
make test      # pytest + coverage (offline; OMS_RUN_INTEGRATION/OMS_RUN_ONLINE opt-in)
make bank-up / bank-migrate / bank-down / bank-status / bank-reset   # local Bank (npx supabase)
make web-up / web-build / web-dev   # read-only viewer (oms.web + web/viewer SvelteKit)
make                                # default goal is `help` (auto-generated target list)
```

`make check` and `make test` MUST be green at every milestone boundary.

Single-test / subset runs use `pytest` directly (the Makefile target has no
selector flag):

```bash
uv run pytest tests/test_bank.py                       # one file
uv run pytest tests/test_bank.py::test_quarantine      # one test
uv run pytest -k "distill and not integration"         # by keyword
OMS_RUN_INTEGRATION=1 uv run pytest -m integration     # opt in to Bank-backed tests
OMS_RUN_ONLINE=1      uv run pytest -m online          # opt in to live agent/LLM tests
```

Offline end-to-end smoke (drives the real handlers against an in-memory Bank;
no Supabase, no real LLM): `uv run python scripts/simulate_story.py`.

Pre-flight before real work against a live Bank: `python -m oms.preflight`
validates env / Bank reachability / keys and prints a one-line actionable hint
on failure (set `OMS_DEBUG=1` to re-raise).

## Build conventions

- **Milestone-ordered build (M0–M10).** Each milestone leaves the tree green.
  See `crystalline-crafting-quill.md` (repo parent) for the per-milestone spec
  and the C1–C4 corrections folded into it.
- **Dependencies are declared per milestone**, as the `src/` code that imports
  them lands, so `deptry src` (DEP002) stays green at each boundary. M0 runtime
  dep is just `python-dotenv`.
- **Schema changes are new numbered migrations** under `supabase/migrations/`,
  never edits to existing ones (oms.bank; Principles §3).
- **Tunables are `OMS_`-prefixed**, read from env at module scope, defaulted in
  code, documented in `oms.env.example` (Principles §8; oms.utils).
- **No per-instance `__getattr__`** dispatch (Principles §4). Package-level
  PEP-562 lazy loading in `src/oms/__init__.py` is the allowed, explicit form.
- **Tests mirror `src/oms/` 1:1.** Every feature ships with a test.
- **Doc-sync:** when the build forces a divergence from a frozen design doc,
  append a dated entry to that `docs/design/components/oms.*.md` Decision log.

## Architecture

Layered, bottom-up — each layer imports only from layers above it in the list:

`oms.utils` (config/sid/provider/log) → `oms.bank` (Supabase + RLS, retry
shim) → `oms.core` (frozen Pydantic Session/Goal/Agent/Packet, Collection) →
`oms.capture` (trace → scrubbed `raw` packet, `CanonicalTrace`) →
`oms.adapters` (`Adapter` ABC + builtins claude/codex/gemini; local-adapter
discovery via `OMS_ADAPTERS_DIR`) → `oms.forum` (post discipline,
`ANTI_META_BLOCK`) → `oms.distill` (curator, 6-bucket Insight schema,
content-addressed idempotent re-runs, no-carry-forward) → `oms.cli` (single
entrypoint `oms.cli:main`, two-stage SIGINT, writes `~/.oms/active`) →
`oms.web` (read-only FastAPI + SvelteKit viewer under `web/viewer/`) →
`oms._mcp` (M11 in-agent MCP server for Claude Code / Codex / Gemini).

`oms.testing` sits atop the stack (it imports cli/_handlers): the dummy
Bank / model / adapter / IO doubles plus the `Simulation` driver for
simulated-conversation tests, seeded with the real "trial story" fixture
(`trial_reflection()` / `trial_bundle()` / `seed_trial_story()`). Prefer the
`sim` / `trial_bank` fixtures from `tests/conftest.py` when a test should
replay a conversation through the real verbs; `tests/test_testing.py` is the
pattern reference.

Package-level lazy loading lives in `src/oms/__init__.py` (`_SUBMODULES` +
`_LAZY_IMPORTS`); add new public symbols there with a `TYPE_CHECKING` import
mirror so static analysis still sees them. Build M0–M10 is complete (per
`BUILD_NOTES.md`); M11 (MCP) is the in-progress surface in `_mcp.py`.

## Packet Model & Web API

Every piece of session data is a **Packet** (`oms.core.Packet`), one of three types:

- **`raw`** — The captured trace (PTY events or structured logs). Stored as a
  `CanonicalTrace` envelope in the `traces` table. When a harness rendition
  exists, `mined_conversation` (extracted user/assistant turns) is available at
  `/api/rendition/{session}/{p}/harness`. Raw traces are the only packet type
  that produce renditions.
- **`post`** — An agent's reflection or reply to another post (oms.forum). Has
  `kind` (reflection/reply), `stance` (agree/disagree/synthesize if a reply),
  `rating` (optional 1–5 star), and `structured` (the post content dict).
- **`distill`** — Curated insights by the distiller (oms.distill). Has `scope`
  (per_goal/cross_goal), `bundle` (the insight data), `parents` (post IDs it
  synthesizes), `curator` (local/server), and `preference` (accept/reject for
  server distills). Distills are immutable once created (no-carry-forward).

**Web API** (`oms.web.api`, M9) is read-only, identity-gated, DB-enforced. The
main entry point for end users is **`GET /api/session/{session}/summary`**,
which returns session metadata, agents, all packets chronologically, and a
summary. For raw packets, it includes `mined_conversation` if available (pulled
from harness rendition). Use `/s/{session}` for paginated packet browsing and
`/s/{session}/a/{agent}` for agent-specific work. Raw bodies (event streams)
require explicit `?include=raw` and are gated by `OMS_WEB_PUBLIC_RAW` env
(defaults on; pre-alpha). See `tests/test_web.py` for testing patterns — use
`FakeBank` + `httpx.AsyncClient` + `create_app()` to unit-test routes.

## Public Deployment

The read-only viewer and API are hosted at **`https://swarms.formulacode.org`**
(Cloudflare named tunnel). The Bank (Supabase HTTP API) is at
**`https://db-swarms.formulacode.org`** (separate tunnel; both tunnels are
managed by `make web-tunnel-*` and `make db-tunnel-*` respectively). The public
web tier is **safe to expose openly** — all writes are DB-enforced, reads are
identity-gated.
