---
tags:
  - documentation
  - manyagent
  - design-process
---

## Status

- **Lifecycle:** Living — the explicit "pick this up in a future conversation" list.
- **Last reviewed:** 2026-05-19. Follows `ManyAgent - Design Principles.md`.
- This is the deliberate residue after the 2026-05-19 resolution pass. Everything here is either (a) waiting on the user for input, (b) intentionally deferred with a seam in place, or (c) recorded as *closed-simple* so we do not relitigate it.

## A. Waiting on the user (TODO — needs your input)

1. **Distillation prompt design — RESOLVED 2026-05-19.** The `swarms` codebase *was* the reproducible codebase. The validated discipline (concrete + bounded + grounded + scarce; anti-meta block; mechanical validation; falsifiable post-mortem write schema; 6-bucket Insight curate schema; outcome weighting) is specified in `components/manyagent.forum.md` + `components/manyagent.distill.md` from `swarms/distillation/prompts.py`, `cross_task.py`, `concreteness.py`, `discussion/forum_prompt.py`, `seeding.py`. No longer waiting on the user. (Superseded — kept for the record.)

2. **Metadata population mechanism — folded into item 14 (goal-inference).** Superseded by the swarms-alignment pass: clustering is now goal+time-based, and the open piece is *goal* inference (item 14). `metadata`/embeddings remain an optional later refinement of goal clustering. (Kept for the record.)

## B. Deferred with a seam (safe to ship v1 without; revisit when it bites)

3. **Secret-scrub completeness.** Now *narrowed*, not catastrophic: raw trace bodies are **not** in the public-read role's grant (only summaries + metadata are public), so scrub is defense-in-depth, not the only wall. Residual open question: scrub will still miss novel secret formats, and a `trusted` reader is still a reader. Seam: `traces.scrub_version` enables a re-scrub backfill + retro-quarantine. Revisit if/when the corpus is opened wider than `trusted`.

4. **Old-trace disk growth / offload.** Self-hosted Postgres; raw traces are large. Seam: `KnowledgePacket.raw_ref` is a pointer, not a blob, so an object-storage offload of cold traces is a non-breaking change. No policy yet (backup cadence, retention, offload threshold). Operational, not architectural; lives in `docs/guide/` when hosting is real.

5. **Non-interactive default policy.** `manyagent` now has several interactive gates (resume/delete, hub-download, `/inject` confirm, self-distill accept/reject). Automation can't answer prompts. Deferred decision: what each gate's *safe non-interactive default* is. Current stance: in non-interactive mode `/inject` and self-distill **deny by default** (never silently inject unreviewed context or auto-accept a distillation); destructive prompts default to the documented safe choice. Flag/env (`--yes`, `MANYAGENT_NONINTERACTIVE`) not built in v1.

6. **Agents that genuinely cannot accept mid-session injection.** `inject()` prepends to the next turn (adapter responsibility, PR-reviewed). For a long-running agent that never re-reads context, injection mid-run is a no-op. Rare; declared the adapter author's problem to document per plugin. Revisit only if a popular agent hits it.

## C. Closed-simple — do NOT relitigate (recorded so we stop circling)

7. **Cross-distill concurrency.** Resolved simple: Supabase is ACID; two simultaneous `/cross-distill` calls just produce two `cross-distill` packets; consumers take the **latest**. No locking, no coordination. (`manyagent.distill`/`manyagent.bank`.)

8. **Plugin ecosystem / "Bank API versioning".** There is no arbitrary ecosystem. An adapter is a **pull request to the GitHub repo**, manually reviewed by a maintainer, open-source, merged only if non-nefarious. The hub serves reviewed/merged plugins. No untrusted-code-execution trust model, no multi-client schema-compat problem. (`manyagent.adapters`.)

9. **PTY / capture fidelity.** Not an `manyagent.capture` heuristic problem. The `CanonicalTrace` OO schema is the contract; the *adapter author* is responsible for emitting schema-conformant traces and is reviewed at PR time. `manyagent.capture` validates conformance, bounds size, scrubs, persists — it does not magically parse arbitrary terminals. (`manyagent.capture`/`manyagent.adapters`.)

10. **Identity / abuse / "no-auth".** Resolved via the 3-role model (datasmith-validated): `public` (read-only), `trusted` (writes with a manually-distributed key), `admin` (full oversight), enforced by Supabase-native RLS + role grants, exposed via PostgREST. Not "no auth"; a deliberate minimal-auth wall-back adopted from the start. (`manyagent.bank`/`manyagent.web`.)

11. **Injection trust boundary.** Resolved via human-in-the-loop: `/inject` shows a preview (first 100 + last 100 tokens of the packet summary) and requires explicit y/n; deny aborts. No reputation/trust-graph needed for v1. (`manyagent.cli`/`manyagent.distill`.)

## D. The one genuinely-hard thing still partly open

12. **"What is a good distillation?" — design RESOLVED; one empirical question now tracked.** The *definition* is no longer open: it is the swarms discipline, adopted in `manyagent.forum`/`manyagent.distill` (concrete+bounded+grounded+scarce, enforced write+curate-time, mechanically validated). What remains is **empirical, and it is the new headline experiment** (see §A1-NEW below), not a design blocker.

## A-NEW. Tracked experiments (design settled; needs running data to confirm)

13. **Validate downstream-reuse as the curation weight (the new headline question).** Per the 2026-05-19 decision, `reuse_score` (a packet was `/inject`ed into a later session that was then rated/accepted well) is the **default baseline** curation weight — a behavioral substitute for swarms' objective `native_score`/recurrence. It is *designed and implemented* (`manyagent.bank` `injections` ledger + recomputable view; `manyagent.distill` weighting). **Open empirically:** does reuse-weighting actually improve injected-bundle usefulness vs. ★-only or unweighted? This needs real corpus data. Tracked as the primary `manyagent` experiment once there is a running deployment. Owner: us, post-deployment.
14. **Goal-inference mechanism.** `Goal` is soft/optional/agent-inferrable. *When* and *how* the agent proposes a goal tag (at `/self-distill`? cheap heuristic vs. an LLM call? human-confirmable like ★?), and how ungoaled posts are clustered for cross-goal curation, is unspecified. Seam in place: `Session.goal`/`Packet.goal` columns + per-goal/cross-goal scopes. (Subsumes the old "metadata population" item — `metadata`/embeddings become an optional refinement of goal clustering, still TODO.)
15. **Server-curator budget / ops / abuse.** The hybrid `server` curator periodically re-distills the public corpus under the narrow `curator` identity. Unspecified: re-distill cadence, per-goal cost budget, who funds it, abuse handling if a `trusted` writer floods a goal with junk posts to skew a bundle (partial mitigation: reuse-weighting down-weights un-reused junk; quarantine; no-carry-forward). Operational, not architectural; lands in `docs/guide/` when hosted.

## Decision log

- **2026-05-19 — Created.** Captures the residue of the resolution pass so the next conversation starts from a known boundary, per the user's instruction to retain anything not fully specified.
- **2026-05-19 — Swarms-alignment update.** §A1 RESOLVED (swarms repo was the reproducible codebase; discipline specified in `manyagent.forum`/`manyagent.distill`). §D12 design-resolved; the residual is now empirical. §A2 folded into the new item 14. Added tracked items 13 (validate downstream-reuse-as-signal — the new headline experiment, design-complete, needs deployment data), 14 (goal-inference mechanism), 15 (server-curator budget/ops/abuse). The boundary for the next conversation: nothing is design-blocking; the open items are an experiment to run and two ops/refinement questions.
