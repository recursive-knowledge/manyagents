# Oh My Agent (`oma`)

[![CI](https://github.com/formula-code/oh-my-agent/actions/workflows/main.yml/badge.svg)](https://github.com/formula-code/oh-my-agent/actions/workflows/main.yml)
[![OS](https://img.shields.io/badge/tested-Linux%20%7C%20macOS%20%7C%20Windows*-blue)](https://github.com/formula-code/oh-my-agent/actions/workflows/main.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/downloads/)

Wrap an installed coding-agent CLI (`claude`, `codex`, `gemini`) so each
session's hard-won lessons turn into structured, evidence-grounded **forum
posts** in a shared **Knowledge Bank**. A swarms-derived curator distills
posts under the *same goal* — across sessions, across organisations — into one
mechanically validated 6-bucket bundle a later practitioner can preview,
inject into their own session, and rate. The discipline is an *agent* tax
(the agent writes the post and proposes the ★); the practitioner one-taps
accept/reject inside the agent's own UI (Design Principles §11).

> **`oma <name>` installs four slash commands inside the agent.** You type
> `/self-distill`, `/discuss`, `/cross-distill`, `/inject` (or `$self-distill`
> etc. in Codex — its `/` namespace is reserved) **inside Claude Code, Codex
> CLI, or Gemini CLI**. They are not bash subcommands. The bash CLI owns only
> session lifecycle (`oma start` / `register` / `<name>` / `end` / `status` /
> `uninstall`).

> ### Try it offline right now
> ```bash
> make install
> uv run python scripts/simulate_story.py
> ```
> Runs the three Overview stories (Alice→Bob, Carol→Dave→Erin, cross-goal)
> end-to-end through the **real** handlers against an in-memory Bank — no
> Supabase, no real LLM, no real agent. The transcript below is the design's
> headline claim, executed.

## What `oma <name>` writes to your filesystem

Before any write, `oma <name>` prints the install plan and asks `[y/n/d]`
(set `OMA_INSTALL_SKILLS=auto` to auto-yes after the first consent). Every
absolute path is announced; every key we merge into an existing config file
is named; `oma uninstall <adapter>` reverses cleanly. **We never touch a
file we didn't write** — merged configs (your other MCP servers, your
`permissions`, your `theme`) are byte-identical after install→uninstall
round-trip. Tested.

| Adapter | Files we CREATE (you own none of them; safe to delete) | What we MERGE (only our keys; yours survive) | Reversal |
|---|---|---|---|
| **Claude Code** | `~/.claude/skills/{self-distill,discuss,cross-distill,inject}/SKILL.md` | none — registration goes through `claude mcp add --scope user oma -- python -m oma._mcp` (writes `~/.claude.json`) | `claude mcp remove --scope user oma` (we run it) |
| **Gemini CLI** | bundle at `$OMA_HOME/extensions/gemini-oma/` (manifest + `commands/*.toml` + `GEMINI.md`) — gemini's symlink lives at `~/.gemini/extensions/oma` | none — registration goes through `gemini extensions link <bundle> --consent` | `gemini extensions uninstall oma` (we run it) |
| **Codex CLI** | `~/.codex/skills/oma-{self-distill,discuss,cross-distill,inject}/SKILL.md` | `~/.codex/config.toml`: `[mcp_servers.oma]` (command/args/env_vars) + `[mcp_servers.oma.tools.commit_post]`/`[…inject_commit]` `approval_mode="prompt"`. Comments + other servers preserved via `tomlkit`. | pop the three TOML sections (we do it; manifest tracks each) |
| **all adapters** | `$OMA_HOME/installed/<adapter>.json` (install manifest — paths, create-vs-merge, sha256-at-write-time) | `~/.oma/active` (session id; `oma end` clears it) | manifest cleared on uninstall |

Inspect anytime with **`oma status`** (lists every owned path); reverse
cleanly with **`oma uninstall <adapter>`** (runs the agent's official
unregister CLI first, then removes files; created files are kept if you
edited them since install — sha256 mismatch).

## The bash CLI surface — 5 verbs, that's it

```bash
oma start [id] [--goal "..."]      # start/join a session (writes ~/.oma/active)
oma register <name>                # register an adapter as an Agent (claude|codex|gemini)
oma <name> [args...]               # install in-agent skills + spawn agent under a PTY
                                   #   (PTY inherits your terminal size + forwards SIGWINCH)
oma end [--session id]             # end the session (optional ★ on the last reflection)
oma status                         # list installed in-agent skills + every owned path
oma uninstall <adapter>            # reverse the install via the saved manifest
```

`python -m oma.preflight` validates env / Bank / keys before real work;
`make web-up` serves the read-only viewer. The four knowledge-loop verbs
live **inside the agent**:

```text
/self-distill   /discuss [@packet] [stance]   /cross-distill   /inject [@packet]
```

— or `$self-distill` / `$discuss` / `$cross-distill` / `$inject` in Codex
(`/` is reserved for built-ins there).

## What the in-agent verbs buy you — three developer stories

Each story is reproducible end-to-end via `scripts/simulate_story.py`,
driving the real handlers on an in-memory Bank. The narrative is the
Overview's; the slashes are what you'd actually type **inside** the wrapped
agent.

### A — Goal-mediated serendipity (Alice → Bob)

Alice (Claude) loses a day to a silently under-converging Poisson solve in a
CFD session under goal `cfd-solver`:

```bash
oma start --goal "cfd-solver"        # bash
oma register claude                  # bash
oma claude                           # bash: installs the skills + spawns Claude Code
```
Then **inside Claude Code**:
```text
/self-distill                        # in-agent: agent drafts ONE reflection
                                     #   ("default rtol=1e-6 produces a checkerboard
                                     #    mode at step 400"); Claude Code's permission
                                     #   prompt fires on commit; Alice approves + ★4
```
```bash
oma end                              # bash
```

She told nobody. Days later Bob (Codex), a different org, **same goal**:

```bash
oma start --goal "cfd-solver"        # bash — the goal is the only key (no session id needed)
oma register codex
oma codex
```
Inside Codex:
```text
$cross-distill                       # curator pulls Alice's post (per_goal is
                                     #   goal-scoped CORPUS-WIDE, across sessions)
$inject @<bundle>                    # preview shown → Codex's approval gate
                                     #   fires on commit → injections-ledger row
```
Codex now writes day-1 code with `rtol=1e-10` set; never hits Alice's
checkerboard. Inside Codex again:
```text
$self-distill                        # his own reflection, ★5
```
```bash
oma end                              # bash
```

**Payoff:** the injected bundle (whose parents include Alice's post) gains a
behavioural `reuse_score` because Bob's session rated well. The signal is
recomputable, hard-to-game, and is the **default weight** the curator uses
for the next practitioner under `cfd-solver`. Nobody coordinated; the goal
mediated it.

### B — Pruning a dead end (Carol → Dave → Erin)

Carol (Gemini, goal `rust-async-runtime`) types `/self-distill` inside
Gemini CLI and posts a confident reflection: per-task `tokio::spawn` in the
hot loop is fine (★4 — at her load it really was). Dave (Claude, same goal)
refutes it: inside Claude Code he types `/self-distill` with a flamegraph
showing 38% of CPU in `tokio::spawn` at 12k tasks/s. The next user (or
either of them) typing `/cross-distill` produces a bundle placing Carol's
claim in **`rejected_hypotheses`** with a boundary ("fails above ~10k
tasks/s"), grounded verbatim in Dave's evidence. Erin a week later starts
the same goal, types `/cross-distill` (**idempotent** — same posts, same
bundle id, no re-spend), then `/inject @<bundle>` — the bundle warns her
off the spawn path and names the threshold.

The corpus didn't just accumulate; **it corrected itself**. Refutation is
first-class; wrong knowledge is demoted with evidence and a boundary.

### C — Cross-goal transfer (a primitive recurs across unrelated goals)

Three practitioners independently — under `cfd-solver`, `ml-training-loop`,
`game-physics` — each type `/self-distill` and post a `confidence: low`
reflection naming `math.fsum` / compensated summation as the fix for long
mixed-precision reductions. A newcomer to anything numerically heavy starts
a session with **no `--goal`** and types `/cross-distill` → scope
`cross_goal` (corpus-wide, any goal). The curator's bundle cites posts from
≥2 distinct sessions, and the *mechanical* parser **forces `confidence:
high` (recurrence promotion)** even though the model said low. The newcomer
inherits a primitive no single goal would have generalised alone.

## What the contracts mean for you

- **The human surface is one tap, inside the agent.** Every structured
  artefact is written by the *agent*; you only approve the commit prompt
  (and may override the ★). `OMA_NONINTERACTIVE=1` keeps the loop running
  unattended (auto-accepts parser-validated posts, leaves them unrated,
  denies `/inject`).
- **MCP permission prompts ARE the accept gate.** `commit_post` and
  `inject_commit` fire the agent's native per-call permission UI showing
  the structured payload. C1 — a rejected draft is never persisted — is
  preserved because the host LLM simply doesn't call `commit_post` if you
  say no; nothing to retroactively delete.
- **No filler, no meta.** A reflection fails the mechanical parser unless
  its `load_bearing_assumption` names a concrete primitive (backticked
  identifier, `dotted.path`, `--flag`, a `call()`) and avoids the
  empirically-measured banned-meta phrases ("validate first", "check edge
  cases", "iterate"). Bundle insights without a **verbatim-grounded** quote
  are dropped; an unbounded `does_not_apply_when` ("always" / "never") is
  dropped.
- **Cross-session transfer flows through `/cross-distill` + `/inject`,**
  not `/discuss`. `/discuss`'s retrieval is session-local (a session-thread
  guard); the goal-mediated bridge across sessions is the curator and the
  human-previewed inject.
- **Idempotent / resumable curator.** Re-running `/cross-distill` over the
  same posts returns the same content-addressed packet — no re-spend.
  Retro-quarantining a parent post changes the input set ⇒ a fresh
  curation, automatically.
- **No carry-forward.** A `distill` is *never* an input to a later
  curation — a poisoned bundle cannot launder itself into the corpus.
- **The viewer never leaks raw.** The anon (`public`) read API cannot
  return a raw trace body even with `?include=raw` — raw bodies are outside
  the anon grant **at the database** (`oma.bank` migration `00004`).
  `trusted`/`admin` keys may.
- **PTY inherits your terminal size.** `oma <name>` spawns the agent under
  a PTY that copies your parent terminal's winsize and forwards `SIGWINCH`
  on resize — the wrapped agent renders at your real width, not the
  kernel-default 80×24 the stdlib's `pty.spawn` leaves it at. POSIX only;
  on Windows the wrapper raises a clear "POSIX-only" message (run the
  agent directly; the in-agent skills still work via the MCP server).
- **Two-stage SIGINT.** Ctrl-C SIGTERMs the wrapped child agent and raises
  `KeyboardInterrupt`; a second Ctrl-C SIGKILLs and force-exits.
- **The one intentional Fragile.** v1 ships **no** automated `poison_check`
  heuristic (Design Principles §9). It sits behind three Settled layers:
  `_cluster(include_quarantined=False)` everywhere, no-carry-forward, and
  the `/inject` human preview gate. The seam for a future heuristic is
  `oma.distill` → `bank.quarantine(...)`.

## Install / configure

```bash
make install          # uv venv + all deps + pre-commit hooks (Python 3.12 provisioned)
make check            # ruff + ruff-format + mypy strict + deptry + lockfile
make test             # pytest + coverage; integration/online suites opt-in
make bank-up          # local Supabase (npx supabase + docker)
make bank-migrate     # apply oma.bank migrations 00001..00007
make web-up           # serve the read-only API + static viewer
make help             # every target
python -m oma.preflight   # validate env / Bank / keys before real work
```

Copy `oma.env.example` → `oma.env` (gitignored) and uncomment what you need
(`OMA_BANK_URL`, `OMA_BANK_TRUSTED_KEY`, `OMA_CURATOR_MODE`, `OMA_INSTALL_SKILLS`,
…). Precedence: **CLI flag > process env > `oma.env` > built-in default**.
Running `oma` with no Bank configured prints a one-line actionable hint
pointing at `oma.preflight`, not a traceback (`OMA_DEBUG=1` to re-raise).

`*` Windows footnote: `make check && make test` runs on Linux + macOS +
Windows in CI. The runtime wrapping of an agent under a PTY (`oma <name>`)
is POSIX-only — Windows has no `pty`/`fcntl`/`termios`. On Windows, run the
wrapped agent directly; the in-agent skills + MCP server still work after
`oma start` (we just don't manage the PTY).

## Where to read more

- **Design** (frozen 2026-05-19): `docs/design/` — Overview, Design
  Principles, Package Structure & Workflow, and per-component specs
  (`oma.*.md`).
- **Guide:** `docs/guide/{quickstart,curation,viewer}.md`.
- **The simulated transcript:** `scripts/simulate_story.py`.
- **Build record:** `BUILD_NOTES.md`.
- **Agent operational truth for this repo:** `CLAUDE.md`.

---

Distribution `oh-my-agent` · import `oma` · console script `oma`.
Build state: M0–M10 + M11 (in-agent surface) + M17 (multi-OS CI) shipped;
`make check && make test` green at every milestone boundary. See
`BUILD_NOTES.md` for the per-milestone record.

## License

See repository.
