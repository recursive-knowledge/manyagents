# Installation

`manyagent` is built and managed entirely through `uv` and `make`.

## Requirements

- `uv` (provisions Python 3.12 automatically; `requires-python >=3.12,<4.0`)
- `node` / `npx` (for the local Bank via `npx supabase`)
- `docker` (the local Bank runs in containers)
- An installed coding-agent CLI to wrap (`claude`, `codex`, or `gemini`)

## Install

```bash
make install   # uv venv + all deps + editable install + pre-commit hooks
make help      # list every target
```

## Verify

```bash
make check     # ruff + ruff-format + mypy strict + deptry + lockfile
make test      # pytest + coverage (offline)
```

The `integration` (local Bank) and `online` (installed CLIs / live LLM) test
suites are opt-in via `MANYAGENT_RUN_INTEGRATION=1` / `MANYAGENT_RUN_ONLINE=1`.

## Configuration

Copy `manyagent.env.example` to `manyagent.env` (gitignored) and uncomment the tunables you
need. Precedence is CLI flag > process env > `manyagent.env` > built-in default.
