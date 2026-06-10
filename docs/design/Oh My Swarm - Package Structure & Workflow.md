---
tags:
  - documentation
  - oh-my-swarm
  - design-process
---

## Status

- **Lifecycle:** Blueprint — copy datasmith's scaffolding, do not reinvent.
- **Last reviewed:** 2026-05-19. Follows `Oh My Swarm - Design Principles.md` (adds §10).
- Derived from the *shipped* `datasmith` mechanics (`Makefile`, `pyproject.toml`, `tox.ini`, `.pre-commit-config.yaml`, `src/datasmith/__init__.py`, `update/cli.py`, `.github/workflows/main.yml`, `mkdocs.yml`). The build/CLI/quality/docs scaffolding is itself a reusable artifact — the single highest-confidence thing to inherit, because it already survived a real package.

## Repository layout

```
oh-my-swarm/
  pyproject.toml            # single source for deps, build, lint, type, test config
  uv.lock                   # committed; CI asserts it matches pyproject
  Makefile                  # the only human/CI entrypoint (see below)
  tox.ini                   # python-version matrix
  .pre-commit-config.yaml   # ruff + ruff-format + hygiene hooks
  mkdocs.yml                # docs site (material + mkdocstrings)
  oms.env                   # local config (gitignored); see oms.utils
  src/oms/                  # src-layout package (import name: oh_my_agent? -> see note)
    __init__.py             # PEP 562 lazy surface (pattern below)
    __init__.pyi            # explicit static API for type-checkers
    py.typed                # PEP 561 marker
    cli.py                  # the single console-script entrypoint
    core/ adapters/ capture/ distill/ bank/ web/ utils/
  supabase/
    migrations/             # the data-model SOURCE OF TRUTH (oms.bank)
    config.toml
  tests/                    # mirrors src/oms/ one-to-one
  docs/
    design/                 # THESE docs (Overview + components/ + principles)
    guide/                  # operational how-tos (written as ops materializes)
    getting-started/
  scripts/                  # backfill / recovery / migration (expected — Principles §8)
  infra/                    # proxy/tunnel configs (expected if hosted)
```

> **Import-name note.** The Overview uses `import oh_my_agent as oms`. Pick the *distributed* name (`oh-my-swarm` on PyPI), the *import* name (`oh_my_agent`), and the *alias* (`oms`) once, in `pyproject.toml` + `__init__.py`, and never derive them as strings elsewhere (datasmith's identity rule). datasmith ships `fc-data` (dist) / `datasmith` (import) — the mismatch is fine and intentional.

## The Makefile is the only interface

datasmith drives *everything* through `make`; humans and CI call the same targets. `make` with no args prints help (auto-generated from `## ` comments; `.DEFAULT_GOAL := help`). Mirror this exactly:

| Target | What it does (datasmith parity) |
|---|---|
| `make install` | `uv sync --all-extras` + `uv pip install -e .` + `pre-commit install` |
| `make check` | assert `uv.lock` matches pyproject; `pre-commit run -a`; `mypy`; `deptry src` |
| `make test` | `uv run python -m pytest --cov --cov-config=pyproject.toml` |
| `make build` | `pyproject-build` wheel via uv |
| `make bank-up/down/status` | local Supabase lifecycle (datasmith's `supabase-*`) |
| `make bank-migrate` | apply `supabase/migrations/` to the local instance |
| *(later, if hosted)* `make web-up`, `make *-tunnel`, `make model-proxy` | the grafana/cloudflare/litellm analogs — name them now, build when hosted |

Rule: if a contributor needs to remember a raw `uv`/`npx`/`docker` incantation, it belongs in the Makefile with a `## ` doc comment.

## Tooling stack (all via `uv`)

- **`uv` for everything**: `uv sync`, `uv lock --locked` (CI gate), `uv run`, `uv venv`. No bare `pip`/`virtualenv`.
- **Build**: `hatchling`, src-layout, `[tool.hatch.build.targets.wheel] packages = ["src/oms"]`.
- **Single console script**: `[project.scripts] oms = "oms.cli:main"` — the *one* entrypoint (datasmith has exactly one: `fc-data = "datasmith.update.cli:main"`).
- **Dependency groups**: runtime `dependencies`; `[dependency-groups] dev` (pytest, pytest-asyncio, mypy, ruff, deptry, pre-commit, tox-uv) and `docs` (mkdocs-material, mkdocstrings). `git+` direct refs allowed (`allow-direct-references = true`) for the agent-CLI/harbor-style deps.
- **Lint/format**: `ruff` + `ruff-format` (line-length 120; enable `S` bandit, `B` bugbear, `A`, `C4`, `SIM`, `I`, `C90`, `UP`, `RUF`, `TRY`, `PGH`; per-file-ignores for `tests/*` and any `templates/`). Pre-commit also runs hygiene hooks (trailing-whitespace, check-yaml/json/toml, merge-conflict).
- **Types**: `mypy` strict (`disallow_untyped_defs`, `no_implicit_optional`, `warn_return_any`), `files=["src"]`, ship `py.typed`.
- **Dead deps**: `deptry src`.
- **Tests**: `pytest` + `pytest-cov` + `pytest-asyncio` (`asyncio_mode = "auto"` — `oms` is async like datasmith's runners), `branch=true` coverage, `testpaths=["tests"]`.
- **Matrix**: `tox.ini` py-versions; `[gh-actions]` mapping. Pin `requires-python` to one minor in `pyproject` (datasmith ships `>=3.12`) but keep ruff/tox aware of the lower bound you actually test.

## Package import surface — PEP 562 lazy loading

datasmith's `__init__.py` exposes a flat API (`import datasmith as ds; ds.PR`) **without importing every submodule at startup**, via a package-level `__getattr__`:

- `_SUBMODULES: set[str]` and `_LAZY_IMPORTS: dict[str, (module_path, attr)]`.
- `def __getattr__(name)`: import on first access, cache in `globals()`.
- `def __dir__()`: returns `__all__` for REPL/autocomplete.
- A `if TYPE_CHECKING:` block re-imports everything so mypy/IDEs see the real types; `__init__.pyi` makes the static surface explicit.
- `setup_environment()` loads `oms.env` at import (datasmith's `dotenv.load_dotenv("tokens.env")`).

> **Reconciles Design Principles §4.** §4 ("clever dynamic dispatch dies") is about **per-instance** `__getattr__` for hook/method dispatch on models — that pattern was deleted in datasmith. **Package-level** `__getattr__` for lazy import of *known, static* submodules is the opposite: explicit (everything enumerated in two dicts), type-checker-visible, and is exactly what datasmith *kept*. §4 amended to say so; `oms` uses package-level lazy loading and forbids instance-level dispatch.

## The single CLI entrypoint pattern

`oms.cli:main` mirrors `datasmith.update.cli:main` structurally:

1. `argparse` with a rich `epilog` (datasmith lists pipeline stages; `oms` lists commands `start/register/<agent>/end` + slash commands). `RawDescriptionHelpFormatter`.
2. Validate inputs, construct the orchestrator object, hand off. datasmith: build `Pipeline(...)`, `asyncio.run(pipeline.run(...))`. `oms`: dispatch to `oms.core`/`oms.adapters`/`oms.distill` — the CLI stays dumb (`oms.cli` doc, Principles §4).
3. **Signal handling for child agent processes — directly relevant to `oms`.** datasmith installs a SIGINT handler that SIGTERMs (then SIGKILLs on second Ctrl-C) all tracked agent subprocesses, because agents run in `start_new_session=True` and otherwise hang worker threads. `oms` *wraps* a live agent under a PTY — it inherits this exact problem. Adopt datasmith's two-stage SIGINT handler in `oms.cli` from day one (added to that doc's Operations section).
4. A `preflight` module run as `python -m oms.preflight` validating env/bank/keys before real work (datasmith pattern; the Overview already shows the transcript).

## Quality gate / CI

`.github/workflows/main.yml` parity: one `quality` job (`make check`) + one `tests-and-type-check` matrix job (`pytest` + `mypy` across the python matrix) + codecov upload on one version. PRs gate on both. A separate `docs` workflow builds/publishes mkdocs to Pages. `pre-commit` cache keyed on the config hash.

## Docs site

`mkdocs.yml` + `mkdocs-material` + `mkdocstrings[python]`. `docs/design/` is *this* doc set (Overview, `components/`, principles, this file); `docs/guide/` is operational how-tos written **as ops materializes** (Principles §8 — datasmith accreted `remote-access.md`, `monitoring.md`, `model-proxy.md`); `docs/getting-started/` is install+quickstart. The design docs are the *why*; README + an agent-guide (CLAUDE.md/AGENTS.md) are the operational truth (Principles §3).

## The development workflow (the loop)

```
make install            # once
# edit code + write the test in the mirrored tests/ path (every feature MUST have a test)
make check              # ruff + ruff-format + mypy + deptry + lockfile  — must pass
make test               # pytest + coverage                              — must pass
python -m oms.preflight # before any test that touches a live bank/model
# update docs/ if behavior/CLI/schema/architecture changed (Principles §3)
# open PR  → CI runs `make check` + matrix tests → review → merge
```

Adapter contributions are PRs to `src/oms/adapters/` (or a `plugins/` dir) reviewed by maintainers (see `oms.adapters` — this is the plugin trust model). Schema changes are **new numbered migrations**, never edits to old ones (`oms.bank`, Principles §3).

## Decision log

- **2026-05-19 — Created from the shipped datasmith scaffolding.** Adds Design Principles §10 ("the build/CLI/quality/docs scaffolding is a reusable artifact — inherit it"). Reconciled §4 vs. package-level PEP-562 `__getattr__` (different thing; explicitly allowed). Flagged datasmith's child-process SIGINT handling as mandatory for `oms` since `oms` wraps live agents.
