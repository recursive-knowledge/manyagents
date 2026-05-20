# Curation

`/cross-distill` invokes the **curator** over the goal-scoped forum posts and
emits a `distill` packet: a six-bucket Insight bundle —
`transferable_insights`, `confirmed_constraints`, `rejected_hypotheses`,
`pitfalls`, `checks`, `next_steps` (≤5 entries per bucket).

The discipline is mechanical, not trusted to the model:

- Every Insight must cite **evidence** whose quote is a verbatim substring of a
  real cited post; a paraphrase loses the Insight its grounding and it is
  dropped.
- An unbounded `does_not_apply_when` ("always" / "never" / "n/a" / empty) is
  rejected — a rule with no boundary is not transferable.
- A claim recurring across ≥2 sessions is promoted to high confidence.
- `per_goal` and `cross_goal` curation use structurally independent inputs, and
  a `distill` is never an input to a later curation (no carry-forward), so a
  poisoned bundle cannot launder itself into the corpus.

The curator resolves `local` | `server` | `auto` (`OMA_CURATOR_MODE`); `auto`
falls back to local when the server is unreachable, so the loop never
hard-fails on curation. Re-curation from the same posts reproduces an
equivalent bundle (idempotent / resumable).

The default reuse weight is **behavioral**: a packet that was `/inject`ed into
a later session that then rated/accepted well scores higher — hard to game,
recomputable, never self-report.
