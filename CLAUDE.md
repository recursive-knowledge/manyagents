# CLAUDE.md

Operational truth for agents working in this repo (Design Principles §3). The
design docs in `docs/design/` are the *why*; this file is the *how*.

## What this is

`oma` wraps installed coding-agent CLIs, captures traces into a Supabase Bank,
curates cross-session knowledge (swarms-derived discipline), and serves a
read-only viewer. Distribution name `oh-my-agent`, import name `oma`, single
console script `oma = "oma.cli:main"`. **Identity is fixed in `pyproject.toml`
+ `src/oma/__init__.py` and never re-derived as a string elsewhere.**

## Commands

```bash
make install   # uv sync --all-extras + editable install + pre-commit install
make check     # uv lock --locked; pre-commit run -a; mypy; deptry src
make test      # pytest + coverage (offline; OMA_RUN_INTEGRATION/OMA_RUN_ONLINE opt-in)
make bank-up / bank-migrate / bank-down / bank-status / bank-reset   # local Bank (npx supabase)
make web-up / web-build / web-dev   # read-only viewer (oma.web + web/viewer SvelteKit)
make                                # default goal is `help` (auto-generated target list)
```

`make check` and `make test` MUST be green at every milestone boundary.

Single-test / subset runs use `pytest` directly (the Makefile target has no
selector flag):

```bash
uv run pytest tests/test_bank.py                       # one file
uv run pytest tests/test_bank.py::test_quarantine      # one test
uv run pytest -k "distill and not integration"         # by keyword
OMA_RUN_INTEGRATION=1 uv run pytest -m integration     # opt in to Bank-backed tests
OMA_RUN_ONLINE=1      uv run pytest -m online          # opt in to live agent/LLM tests
```

Offline end-to-end smoke (drives the real handlers against an in-memory Bank;
no Supabase, no real LLM): `uv run python scripts/simulate_story.py`.

Pre-flight before real work against a live Bank: `python -m oma.preflight`
validates env / Bank reachability / keys and prints a one-line actionable hint
on failure (set `OMA_DEBUG=1` to re-raise).

## Build conventions

- **Milestone-ordered build (M0–M10).** Each milestone leaves the tree green.
  See `crystalline-crafting-quill.md` (repo parent) for the per-milestone spec
  and the C1–C4 corrections folded into it.
- **Dependencies are declared per milestone**, as the `src/` code that imports
  them lands, so `deptry src` (DEP002) stays green at each boundary. M0 runtime
  dep is just `python-dotenv`.
- **Schema changes are new numbered migrations** under `supabase/migrations/`,
  never edits to existing ones (oma.bank; Principles §3).
- **Tunables are `OMA_`-prefixed**, read from env at module scope, defaulted in
  code, documented in `oma.env.example` (Principles §8; oma.utils).
- **No per-instance `__getattr__`** dispatch (Principles §4). Package-level
  PEP-562 lazy loading in `src/oma/__init__.py` is the allowed, explicit form.
- **Tests mirror `src/oma/` 1:1.** Every feature ships with a test.
- **Doc-sync:** when the build forces a divergence from a frozen design doc,
  append a dated entry to that `docs/design/components/oma.*.md` Decision log.

## Architecture

Layered, bottom-up — each layer imports only from layers above it in the list:

`oma.utils` (config/sid/provider/log) → `oma.bank` (Supabase + RLS, retry
shim) → `oma.core` (frozen Pydantic Session/Goal/Agent/Packet, Collection) →
`oma.capture` (trace → scrubbed `raw` packet, `CanonicalTrace`) →
`oma.adapters` (`Adapter` ABC + builtins claude/codex/gemini; local-adapter
discovery via `OMA_ADAPTERS_DIR`) → `oma.forum` (post discipline,
`ANTI_META_BLOCK`) → `oma.distill` (curator, 6-bucket Insight schema,
content-addressed idempotent re-runs, no-carry-forward) → `oma.cli` (single
entrypoint `oma.cli:main`, two-stage SIGINT, writes `~/.oma/active`) →
`oma.web` (read-only FastAPI + SvelteKit viewer under `web/viewer/`) →
`oma._mcp` (M11 in-agent MCP server for Claude Code / Codex / Gemini).

Package-level lazy loading lives in `src/oma/__init__.py` (`_SUBMODULES` +
`_LAZY_IMPORTS`); add new public symbols there with a `TYPE_CHECKING` import
mirror so static analysis still sees them. Build M0–M10 is complete (per
`BUILD_NOTES.md`); M11 (MCP) is the in-progress surface in `_mcp.py`.
