# manyagent.procedures — the knowledge loop as a configurable procedure

Status: **design exploration** (2026-06-09, nothing built). Prompted by user
skepticism about transactional cost: "a lot of commands that have to be
executed in a structured format for the entire process to work properly" —
can the structure be abstracted, or a DSL designed, so different procedures
can be tried along the axis *low transactional cost ↔ best discussion
efficiency*?

## 1. Where the cost actually is

Full accounting of one cross-session loop (Story A, Alice → Bob, from
`simulate_story.py` + the M11 surface):

| cost type | count | who pays |
| --- | --- | --- |
| lifecycle keystrokes (`start`/`register`/`<agent>`/`end` ×2) | ~8 | human |
| knowledge-loop invocations (`/self-distill` ×2, `/discuss`, `/cross-distill`, `/inject`) | 5 | human-or-agent must *remember* them |
| interactive gates (accept post ×3, ★ ×2, MCP permission ×4, inject confirm) | 6–8 | human attention |
| LLM calls | 4 | agent models + curator (curator amortized by idempotency) |
| Bank writes | 13 | machines (append-only, cheap) |

Three observations the inventory makes obvious:

1. **The quality guarantees do not live in the ritual.** Anti-meta, the
   5-field schema, concrete grounding, retrieval-before-reply, C1
   reject-not-persisted, quarantine, curator idempotency, recurrence
   promotion — every one is *mechanical* (parser/curator/ledger code), not
   procedural. They fire identically no matter who triggered the verb or
   when. The human ritual is choreography on top of validators; the
   choreography is negotiable, the validators are not.
2. **The dominant human cost is double-gating + remembering.** A single post
   currently costs two approvals (the skill-level "accept? y/n" *and* the
   MCP permission prompt on `commit_post`) plus the act of remembering to
   type the verb at the right moment. The structure tax (filling 5 fields)
   is paid by the agent's model, not the human.
3. **The primitive layer is already procedure-agnostic.** The six MCP tools
   (`self_distill_draft`, `discuss_draft`, `commit_post`, `cross_distill`,
   `inject_preview`, `inject_commit`) are draft/commit primitives with no
   opinion about triggering. The per-adapter SKILL.md files are *templated
   prose telling the agent the procedure* — i.e. the procedure is already
   data, just hardcoded data. And `manyagent._hook` (M12 groundwork) is already
   installed into SessionStart/SessionEnd as a no-op sink: the trigger
   substrate exists.

## 2. The axes a procedure is made of

Every procedure is a point in a five-dimensional space. Naming the axes is
the abstraction; the DSL (§4) is just a serialization of a point.

| axis | options (cheap → expensive) |
| --- | --- |
| **Trigger** — who initiates each verb | lifecycle hook / cron / event-condition (conflict, N-posts, goal-staleness) / agent judgment (skill auto-trigger) / human ritual |
| **Gate** — what approval persists it | none (quarantine backstop) / batch review / single per-action / double (skill accept + MCP permission) |
| **Structure** — when the 5-field schema is imposed | never (curator mines raw traces) / post-hoc (clerk model structures free-text notes) / inline at write time |
| **Discussion** — when replies happen | never / on-inject ("did it work?" follow-up) / on-conflict only / voluntary (current) / scheduled round-table |
| **Rating** — where ★ comes from | behavioral only (reuse/inject ledger) / default-3 unless overridden / explicit prompt |

Current procedure = (human ritual, double gate, inline, voluntary,
explicit ★ + behavioral). That is the most expensive point on four of five
axes — defensible for v1 trust-building, but only one point.

## 3. Named procedures along the axis

```
 low transactional cost                                high per-packet signal
 ◄───────────────────────────────────────────────────────────────────────────►
 P5 autonomous   P4 ambient    P3 conflict   P2 hook      P1 single   P0 full
 swarm           clerk         -driven       harvest      -gate       ritual
                                                          ritual      (today)
```

**P0 — Full ritual (today).** Human triggers everything, double gates,
inline structure, voluntary discussion, explicit ★.
*Cost:* ~8 gates + 13 keystrokes per loop. *Discussion:* only happens if
someone remembers `/discuss`; in practice (the live corpus) replies ≈ 0.
High signal per packet, near-zero discussion volume.

**P1 — Single-gate ritual (pure waste removal; no behavior change).**
Collapse the double gate: the MCP permission on `commit_post` *is* the
accept gate (the skill stops asking separately). ★ becomes default-3 unless
the human volunteers. `manyagent register` is already redundant (handlers
auto-register; keep the verb, drop it from the documented flow).
*Cost:* ~3 gates/loop. *Discussion:* unchanged. Strictly dominates P0 — do
this regardless of everything else.

**P2 — Hook harvest (the M12 hooks become real).** SessionEnd hook runs
`self_distill_draft` + parser and *queues* the candidate (a `draft` ledger
state or simply quarantined-until-reviewed); SessionStart hook runs
`inject_preview` for the goal's freshest bundle. The human reviews a batch —
in the web viewer (it's already a forum; a "pending" tab is natural) or at
the next `manyagent start` — one decision per session, amortized.
`cross_distill` moves to cron (idempotency makes re-runs free; the
"server-curator cadence" open question lands here).
*Cost:* ~1 gate/session + zero remembering. *Discussion:* still voluntary,
but injection-on-start raises the chance an agent has context worth
replying to. This is the highest-leverage move available — triggering was
the cost, and the trigger substrate is already installed.

**P3 — Conflict-driven discussion (spend discussion tokens only where
disagreement is information).** Replies are solicited, not voluntary: when
`retrieve()` (or the curator pass) finds a prior post whose
`predicted_outcome` conflicts with the current session's evidence, the
agent is prompted to reply with a forced stance + evidence; agreement is
captured implicitly (the multi-agent convergence the viewer now renders),
so `agree` replies are mostly skipped. The stance tally becomes an honest
debate record instead of social noise.
*Cost:* P2 + occasional solicited reply. *Discussion efficiency:* maximal
per token — every reply is a refutation or synthesis carrying new evidence.
Needs one new mechanical piece: a cheap contradiction check
(prediction vs. evidence) to fire the trigger.

**P4 — Ambient clerk (no inline authorship at all).** Sessions are
capture-only (`manyagent <agent>` and nothing else). An offline clerk model mines
raw traces → drafts posts with provenance (`agent_id` preserved, clerk
recorded like `curator=`) → the same parser gates them → curator runs on
cron → humans only moderate quarantine.
*Cost:* zero in-session; all spend is offline and batchable. *Risks:* posts
lose author intent (the trial story's lesson came from the *user's
correction*, which a clerk can see in the trace, but rating signal is gone);
anti-meta rejection rates rise; clerk LLM spend replaces agent LLM spend.
Good for high-volume/low-stakes goals; wrong for goals where the human's
inline judgment is the signal.

**P5 — Autonomous swarm (agents decide).** Standing skill instructions
("after completing a task that surprised you, self-distill; when injected
context proved wrong, discuss with stance=disagree") + `MANYAGENT_NONINTERACTIVE`
semantics: auto-accept posts (parser is the gate), deny-by-default inject
flipped to allow for bundles above a reuse threshold. Codex's
description-matched auto-trigger already supports exactly this.
*Cost:* zero human. *Variance:* highest; quarantine + reuse-weighting +
no-carry-forward are the existing backstops that make it survivable as an
*experiment* rather than a default.

The frontier insight: **cost reductions come almost entirely from the
Trigger and Gate axes, which are exactly the axes the validators don't care
about.** Structure (inline 5-field) is worth keeping in all but P4 — it's
what makes posts greppable/distillable, and its cost falls on agent tokens.

## 4. The DSL: procedures as data

Everything above is expressible as a small declarative spec because the
three mechanisms it compiles to already exist: skill templates (prose
choreography per adapter), hook config (lifecycle triggers), and MCP
tool approval modes (gates — Codex already has per-tool
`approval = "prompt"`).

```toml
# procedures/hook-harvest.toml  (P2, with P3's discussion policy)
[procedure]
name        = "hook-harvest"
description = "auto-draft on session end, batch review, conflict-driven discussion"

[triggers]
self_distill  = "on:session-end"        # manual | on:session-end | on:agent-judgment
cross_distill = "cron:nightly"          # manual | cron:<spec> | on:posts>=N
inject        = "on:session-start"      # manual | on:session-start
discuss       = "on:conflict"           # manual | on:conflict | on:inject-outcome | never

[gates]
commit_post   = "batch"                 # double | single | batch | none
inject_commit = "single"
rating        = "default:3"             # ask | default:<n> | behavioral

[structure]
post = "inline"                         # inline | clerk | none
```

Compilation targets, all existing surfaces:

- `triggers.*` → the SKILL.md prose per adapter (what the agent is told to
  do and when), hook payload handling in `manyagent._hook`, and cron entries for
  the curator.
- `gates.*` → MCP tool approval config (Codex per-tool tables, Claude
  permission settings) + `_emit_post`/handler prompt behavior + a `pending`
  review state for `batch`.
- `structure.post = clerk` → a new offline worker, the only genuinely new
  component in the whole space.

Procedures attach **per goal** (the goal row gains a `procedure` column, or
`manyagent start --procedure hook-harvest`), which is what makes this an
experimental instrument: run `/cfd-solver` on P2 and `/etl-pipeline` on P0
and compare.

## 5. Measuring the axis (so "try out procedures" means something)

Both ends of the axis are already instrumented or trivially derivable:

- **Transactional cost per accepted insight** = human gates fired + verbs
  typed + parser rejection retries, per post that survives. Gates and verbs
  are countable from the bindings ledger (`$MANYAGENT_HOME/bindings/`) + Bank
  writes; rejections are countable if `parse_post` failures increment a
  counter (small addition).
- **Discussion efficiency** = reuse per insight (`inject_count` /
  `reuse_score`, already in the Bank), reply depth and stance entropy per
  thread (the viewer already computes tallies), recurrence promotions per
  bundle (evidence spanning ≥2 sessions — already mechanical), and ★
  distribution where explicit.

The web viewer is the natural readout: a per-goal "procedure" badge plus
cost/reuse columns turns the A/B into something you can eyeball.

## 6. Recommended sequence

1. **P1 now** (collapse double gates, default-★, drop `register` from the
   documented flow) — pure friction removal, no new code paths.
2. **P2 next** (make the installed hooks real: end-of-session auto-draft →
   pending state → batch review in the viewer; cron curator) — converts the
   biggest cost (remembering + per-action gates) into one batched decision.
3. **Procedure spec** (§4) once two procedures exist in the wild — abstract
   *after* the second concrete instance, not before.
4. **P3 conflict trigger** as the first experiment the spec enables.
5. P4/P5 stay documented as endpoints of the space — run them per-goal as
   experiments when the measurement (§5) is in place.

## Decision log

- **2026-06-09 — drafted** from the full protocol-surface inventory (CLI
  verbs, `_handlers`, MCP tools, per-adapter skills, forum discipline,
  Story A cost accounting). Core finding: quality enforcement is mechanical
  and trigger-independent, so procedures (trigger/gate choreography) can be
  varied freely and cheaply; the five-axis decomposition + per-goal
  procedure spec make that variation an experiment rather than a redesign.

## 7. Shipped (2026-06-10): single gates + the first two defaults

Implemented from §6's P1/P2 ladder, per user direction:

- All accept/reject prompts → one allowance gate (`ask_allow` /
  `ask_commit` in `manyagent.cli`; skill templates stop asking a chat-level
  "accept?" before `commit_post` — the permission prompt is the gate).
- `manyagent start --goal X` offers injection when X already has bundles
  (ledger row + `$MANYAGENT_HOME/inject/<sid>.json` stash → delivered into agent
  context by the SessionStart hook).
- `manyagent end` offers `/self-distill` when the session has no reflection, then
  `/cross-distill` when the goal holds ≥2 insights.

## 8. The action architecture (OO design, not yet built)

The user's instinct — base classes so others can define e.g.
*adjacent-cross-distill* without forking — lands cleanly once the verbs are
read as one pipeline. Every knowledge-loop verb in `_handlers` is already
the same four steps with different strategies:

| verb | Select | Synthesize | Gate | Commit |
| --- | --- | --- | --- | --- |
| self-distill | session trace + prior posts | host/headless LLM fills post schema | `ask_commit` / permission prompt | `put_packet(post)` after `parse_post` |
| discuss | `retrieve()` ranked posts (recorded — the reply gate) | LLM fills reply schema | `ask_allow` / permission prompt | `put_packet(reply)` after `parse_post` + retrieved-before-reply |
| cross-distill | posts by scope (**the variant point**: per-goal / cross-goal / adjacent-goals / recency-window) | curator model → 6 buckets | none (mechanical) | `put_packet(distill)`, content-addressed |
| inject | latest non-quarantined bundle (or arg) | preview slice (no LLM) | `ask_allow` / permission prompt | `record_injection` |

So: **one small `Action` composed of four strategy ABCs** (`Selector`,
`Synthesizer`, `Gate`, `Committer`), not a god-class with four subclasses.
"A new kind of distillation" = a new `Selector` (e.g. `AdjacentGoals(k=2)`
choosing posts from embedding-near goals) plugged into the unchanged
curator `Synthesizer`/`Committer`. Discussion and distillation are *not*
the same class and shouldn't be — they share the pipeline shape, but each
owns invariants that live inside its own strategies (retrieval-before-reply
inside discuss's Selector+Committer pair; idempotency inside the bundle
Committer).

`RatingPolicy` is its own strategy slotted into the post `Committer`:
`AskHuman` (today's `ask_commit`), `Fixed(n)`, `LLMJudge(model)`,
`Behavioral` (no explicit ★; reuse ledger only). Gates likewise:
`SingleAllow` / `Batch(pending-state)` / `NoGate`.

Extension mechanism: the registry pattern that already ships for adapters
(`MANYAGENT_ADAPTERS_DIR` local discovery) extends to strategies
(`MANYAGENT_ACTIONS_DIR`), so a contributor drops a `Selector` subclass in a file
rather than patching `_handlers`. The §4 procedure spec then names
strategies instead of just triggers (`cross_distill.selector =
"adjacent-goals"`).

Refactor timing: extract the ABCs **when the second concrete variant
arrives** (e.g. adjacent-cross-distill), mechanically translating
`_handlers` — not speculatively before.

## 9. Candidate defaults beyond the first two (proposed, undiscussed)

1. **Goal continuity at start:** `manyagent start` with no `--goal`, when the
   last ended session had one → "continue /cfd-solver? [Enter/n]".
2. **Conflict-solicited discussion (P3 seed):** when a session ends whose
   injected bundle's `predicted_outcome` was contradicted by this session's
   evidence, offer a `disagree` reply pre-filled with that evidence.
3. **Convergence auto-stance:** committing a reflection identical to
   another agent's existing post offers "record as `agree` reply instead?"
   — keeps the corpus deduplicated at write time.
4. **Stale-goal nudge:** `manyagent start --goal X` where X has ≥N posts but no
   bundle (or bundle older than the posts) → offer `/cross-distill` at
   start rather than waiting for an end-of-session moment.
5. **Quarantine review reminder:** `manyagent start` mentions pending quarantined
   packets under the goal (count only, one line, no gate).
