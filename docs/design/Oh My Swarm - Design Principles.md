---
tags:
  - documentation
  - oh-my-swarm
  - design-process
---

## Why this document exists

We designed `datasmith` on paper, then built it (github.com/formula-code/datasmith). The shipped package diverged from the design in consistent, predictable ways. These principles encode what only became visible *after* concretizing the package, so the `oh-my-swarm` docs are realistic from the start rather than after the same painful corrections. Every `oms` design doc is expected to obey these; the **Decision log** section in each doc is where deviations and reversals are recorded.

## The principles

### 1. Plan for ~3× structural growth

Datasmith went **7 → ~16 modules, 3 → 9 pipeline stages, 6 → 16+ tables** — a uniform ~2.5–3× expansion, not random noise. **Applied here:** the Overview frames the module list as *provisional and expected to grow*, not "the seven modules." Doc structure must absorb new components without a rewrite.

### 2. The biggest subsystem is the one nobody designed — the invisible prerequisite

Datasmith's largest, hardest module — `resolution/` (12 files, the longest design doc) — *did not exist in the initial design at all*. It was the unglamorous prerequisite: you cannot synthesize a working image until the dependencies are pinned. **Applied here:** for `oms` the analogous invisible prerequisite is **faithful trace capture & normalization** (and, second, **distillation reliability** over huge, heterogeneous traces). `adapter.capture()` was a one-line hand-wave in the first draft; it is now its own component (`oms.capture`) and `oms.distill` is hardened for scale.

### 3. Monolithic overview docs rot; living per-component docs and append-only migrations stay true

Datasmith's *final* Overview still claims "seven modules" and a 3-stage pipeline — it was never updated. The artifacts that stayed accurate were the per-feature component docs, the README/agent-guide, and the append-only SQL migrations (which structurally cannot drift). **Applied here:** the Overview holds only the stable vision; volatile specifics live in component docs that each carry a **Status** and **Decision log**; the data model's source of truth is the migration list in `oms.bank`, not prose; once `oms` is a real package, README + an agent-guide are the operational truth and the design docs are the *why*.

### 4. Clever dynamic dispatch dies; prefer explicit registries and plain functions

The designed `PR.register_hook` + **per-instance** `__getattr__` magic and "`render()` is just a hook" cleverness were deleted. Reality: an explicit `HookRegistry.register(name, fn)` / `.call(name, pr)` and a plain `render_problem_statement(pr)` function. **Applied here:** `oms.adapters` is an explicit ABC + registry (correct by this principle — keep it); `oms.core` uses plain memoized `.fetch()` + properties, **no per-instance `__getattr__` dispatch**; no "X is just a hook" indirection survives into the API. **Clarification:** datasmith *kept* a **package-level** `__getattr__` for PEP 562 lazy submodule loading (everything enumerated in two explicit dicts, type-checker-visible). That is the opposite of dynamic dispatch and is endorsed — see `Oh My Swarm - Package Structure & Workflow.md`. §4 forbids *instance* dispatch magic, not *module* lazy-import.

### 5. The single-tool / single-actor assumption hides whole subsystems

"Codex-only synthesizer" became multi-agent auto-detect **plus** a sandbox, a tamper-auditor, and a rate-limit detector — three subsystems the one-tool assumption concealed. **Applied here:** `oms`'s "use the user's local LLM" will hit budget/rate-limit exhaustion mid-distill and span wildly different model capabilities; `oms.distill`, `oms.adapters`, and `oms.utils` design for heterogeneity and rate limits now, not later.

### 6. "Open / no-auth / anyone writes" does not survive contact

Datasmith's design explicitly said "RLS not needed, single-team." The implementation added four migrations of RLS, anon-grant revocation, and Cloudflare Access. **Applied here:** `oms`'s original premise was exactly that reversed assumption — so rather than ship it and reverse it, `oms` **adopts datasmith's post-reversal endpoint up front**: a 3-role model (public read / trusted-key write / admin), Supabase-native RLS, PostgREST, with a packet **quarantine** state. This converted the question from **Fragile** to **Settled** on 2026-05-19 (`oms.bank`/`oms.web`). The general move — *when datasmith reversed a decision, start `oms` at the destination, not the origin* — is the strongest form of this principle.

### 7. Adversarial input is not an edge case

Agents forged validator logs and test files, so a `tamper_audit` subsystem was built. `oms` is *worse*: a stranger's packet is cross-distilled and **injected into your agent's context** — knowledge poisoning and prompt injection as a first-class threat, not a footnote. **Applied here:** provenance, scrubbing, audit, and quarantine are core to `oms.distill`/`oms.bank`/`oms.web`, not optional hardening.

### 8. Operations is ~⅓ of the real system and ~0% of most designs

Datasmith shipped `grafana/`, eight recovery/backfill/audit scripts, a LiteLLM proxy, and remote-access plumbing — none designed. **Applied here:** every data-bearing component doc carries an **Operations & recovery** section (backfill, corrupted-packet recovery, observability, preflight), and every tunable knob is an `OMS_`-prefixed environment override (datasmith's `DATASMITH_` convention).

### 9. Tier decisions honestly: Settled / Fragile / Open

Datasmith's design docs marked things "**Resolved.**" that were later reversed (no-auth, single-tool). Binary "resolved" was overconfident. **Applied here:** Key Design Questions are tiered **Settled** (cheap to change or genuinely closed), **Fragile** (closed for v1 but datasmith precedent predicts reversal — cite it), **Open** (acknowledged unknown). Never mark "Resolved" something that rhymes with a decision datasmith had to undo. **Corollary (per §6):** the best way to retire a **Fragile** is not to wait for the reversal but to adopt datasmith's known post-reversal endpoint immediately — this is how no-auth, plugin-trust, and the admin surface all moved Fragile→Settled on 2026-05-19. After that pass exactly one **Fragile** remains by design: the automated `poison_check` heuristic (`oms.distill`), which sits behind two Settled layers.

### 10. The build/CLI/quality/docs scaffolding is a reusable artifact — inherit it, don't reinvent

datasmith's `Makefile` (single human/CI interface, `make help` auto-generated), `uv`-only tooling, single `pyproject.toml` for build+lint+type+test, one console-script entrypoint, PEP-562 lazy `__init__`, `tox` matrix, `pre-commit`+`ruff`+`mypy`+`deptry`, mkdocs site, `preflight` module, and child-process SIGINT handling are *already-validated* structure. **Applied here:** `Oh My Swarm - Package Structure & Workflow.md` specifies `oms` to mirror it target-for-target. The scaffolding is the highest-confidence thing to copy, because reinventing it is pure undifferentiated risk and datasmith already paid that cost.

### 11. Knowledge quality is concreteness + boundary + grounding + scarcity — enforced at write *and* curate time, as an agent tax never a human tax

The swarms codebase measured an unguided knowledge bundle at **~74–95% generic process meta-advice** ("validate first", "decompose", "check edge cases"), and because the transferable layer is the payload, *the noise was what transferred* (`swarms/distillation/prompts.py`, `concreteness.py`). The validated fix: an insight is kept only if it (1) names a concrete primitive, (2) is bounded (`does_not_apply_when` ≠ "always"/"n/a"), (3) is grounded in a verbatim quote tied to a real post id, (4) is scarce (caps; empty > filler) — and this is enforced **both** when the contribution is written *and* when it is curated, via one byte-identical rule block, **mechanically** (the parser drops violators; the model is not trusted). **Applied here:** `oms.forum` (write) and `oms.distill` (curate) share `ANTI_META_BLOCK`; validation is parser-side; and — the OMA-specific constraint — *all of this structure runs in the agent-side skill prompt, never as a task on the practitioner*. The human taps accept + an optional ★. This is the single principle that lets OMA gain swarms' knowledge quality while keeping the open-ended loop. **Curator corollary to §6:** running an LLM to curate OMA's *own public corpus* is not being the user's *task* inference provider — OMA already hosts the Bank/API — so a hosted curator is consistent with "OMA ships no inference for the user's task," and the curator is therefore offered hybrid (local *or* server).

## Doc conventions that follow from these

- Every component doc starts with a **`## Status`** block (lifecycle + last-reviewed date + "follows Design Principles") and ends with a **`## Decision log`** (dated entries; record reversals explicitly, à la datasmith's synthesizer doc "The following was replaced by …").
- Superseded docs move to `components/archived/` with an `ARCHIVED ` filename prefix. They are never deleted — the graveyard is part of the record.
- The Overview never enumerates a fixed final architecture; it points to the living docs.
- Diagrams are Mermaid, never ASCII art (renders on GitHub and the docs site).

## Decision log

- **2026-05-19 — Added §10** (scaffolding is a reusable artifact) and reconciled §4 with package-level PEP-562 lazy loading.
- **2026-05-19 — Finalization pass.** Rewrote §6/§9 "Applied here" to record that the resolution pass adopted datasmith's *post-reversal endpoints up front*, converting most **Fragile** items to **Settled** and leaving exactly one intentional Fragile (`poison_check`). The doc set is design-frozen as of this date; the only design-blocking unknown is the distillation prompt/algorithm (Open-Q §A1).
- **2026-05-19 — Swarms-alignment pass.** Added §11 (knowledge quality = concreteness+boundary+grounding+scarcity, enforced write+curate-time, agent tax not human tax — swarms-validated) and the §6 curator corollary (curating the public corpus ≠ being the user's task inference provider → hybrid curator allowed). Open-Q §A1 resolved (the swarms repo was the reproducible codebase).
