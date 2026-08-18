# KSI protocol test suite — design

**Date:** 2026-08-17
**Status:** approved, ready for an implementation plan

## Problem

Four defects in the KSI knowledge loop are measured but untested. Nothing in the
suite would notice if they got worse, and nothing documents them:

| Defect | Measured | Nature |
| --- | --- | --- |
| `/discuss` stores no reply — fields land top-level instead of nested in `structured` | 0/6 live runs | Deterministic |
| `evidence` is paraphrased, so the verbatim check drops the post | 17/17 rejections; post acceptance 14/36 (39%) | Rate |
| The existing-goals picker turns a bare `n` into a goal literally named `n` | Reproducible | Deterministic |
| Domain sensitivity — full cycle closes 2/3 reasoning, 1/3 terminal, 0/3 coding | 3/9 overall | Rate |

Source: `scripts/ksi_rig.py`, 9 runs across 3 domains, 65 model calls, 0
infrastructure failures.

Three of these arose the same way: a prompt asked for one thing while a parser
enforced another, and nothing connected the two. `evidence_post_ids` named a
field that does not exist; the prompt said "concrete" while `CONCRETE_RE`
demanded code-shaped text; the prompt says "verbatim" while models write fluent
summaries. **A prompt edit currently has no observable downstream signal.** That
is the gap this suite closes.

## Goals

1. Freeze the four defects so they cannot silently worsen.
2. Make a prompt edit produce a visible, per-behaviour delta.
3. Probe harder than we have: adversarial content, degenerate shapes, and
   cross-model portability.
4. Keep the CI gate fast, offline, and deterministic.

## Non-goals

- Fixing the defects. This suite documents and pins them; fixes are separate work.
- Measuring the model's true acceptance distribution at production temperature.
  The corpus is deterministic by design (see *Determinism*); sampled measurement
  is a possible follow-up, not part of this spec.
- Benchmark-scale evaluation. The corpus is hand-written and small.

## Key decision: determinism

At `temperature=0` with a fixed seed, the served model returns **byte-identical**
output across calls (verified on `qwen3.5-9b`, three consecutive requests).

Variation therefore comes from the **scenario corpus**, not from sampling. A rate
is a fraction over scenarios, so re-running yields the identical number and the
ratchet compares by exact equality. Any change is caused by our prompts, our
parser, or the model — never noise. This is what makes a stochastic-looking
system testable without confidence intervals.

## Architecture — record / replay

One corpus is the single source of truth. The live runner records; the offline
suite replays.

```
scenario corpus ──▶ live runner (temp=0) ──▶ cassettes ──▶ offline tests (CI)
       │                    │                                    │
       │                    └──▶ baseline.json ◀── ratchet ──────┘
       └──▶ prompt hash ──────────────────────────▶ staleness guard
```

### Layout

```
tests/ksi_corpus.py            # scenarios: the single source of truth
tests/ksi_cassette.py          # record/replay store, prompt-hash keyed
tests/test_ksi_offline.py      # CI gate: replays cassettes through the real parser
tests/test_ksi_live.py         # marked `online`: re-runs the corpus, ratchets
tests/fixtures/ksi_cassettes/  # <scenario>/<step>-<sha12>.json
tests/fixtures/ksi_baseline.json
scripts/ksi_record.py          # re-record cassettes; --update-baseline
```

These live flat in `tests/` rather than mirroring `src/manyagent/` 1:1 (CLAUDE.md
"Build conventions"). The deviation is deliberate: these are cross-cutting
protocol tests spanning `forum`, `distill`, `cli`, and `_handlers`, with no
single owning module. Note it in the `manyagent.procedures` decision log when
implemented.

### Components

**`ksi_corpus.py`** — a `Scenario` is `(key, goal, traces, guidance,
expectations)`. Pure data plus small builders; no I/O, no model. Importable by
both suites and by `scripts/`.

**`ksi_cassette.py`** — `record(scenario, step, prompt, response, meta)` and
`load(scenario, step)`. One JSON file per `(scenario, step)`:

```json
{
  "scenario": "terminal-lead",
  "step": "self_distill",
  "prompt_sha256": "ab12…",
  "model": "qwen3.5-9b",
  "temperature": 0,
  "seed": 42,
  "max_tokens": 2048,
  "enable_thinking": false,
  "recorded_at": "2026-08-17",
  "prompt": "…full rendered prompt, for diffing…",
  "response": "…raw model output…"
}
```

The full prompt is stored, not just its hash, so a stale cassette can be diffed
against the current rendering to see exactly what changed.

**`test_ksi_offline.py`** — feeds each cassette's `response` into the real
`forum.parse_post` / `distill.curator._extract_json` + `distill.parse.validate_bundle`
and asserts the verdict. No model, no network.

**`test_ksi_live.py`** — marked `online`; re-executes the corpus against the
endpoint and ratchets against the baseline.

**`scripts/ksi_record.py`** — the recorder. `--scenario` to re-record a subset,
`--update-baseline` to commit new numbers.

## The staleness guard

The load-bearing test. For every cassette, re-render the scenario's prompt from
current source and compare hashes:

```python
def test_cassettes_match_current_prompts():
    for cassette in load_all():
        current = render_prompt_for(cassette.scenario, cassette.step)
        assert sha256(current) == cassette.prompt_sha256, (
            f"{cassette.scenario}/{cassette.step}: the prompt changed since "
            f"recording. Run `uv run python scripts/ksi_record.py "
            f"--scenario {cassette.scenario}` and review the delta."
        )
```

Editing `ANTI_META_BLOCK`, `POST_ANTI_META_BLOCK`, `_SCHEMA`, `_OUTPUT_SCHEMA`,
or a scope directive invalidates exactly the cassettes it reaches, forcing a
re-record and surfacing the behavioural delta. This is the signal whose absence
let three prompt/enforcement mismatches ship.

## Scenario corpus

Four probe classes, **17 scenarios** — 6 regression, 5 adversarial, 6 degenerate.
Portability adds no new scenarios; it re-runs all 17 against a second endpoint.
Each scenario may exercise several protocol steps, so the cassette count is
higher than the scenario count.

### Regression — pins what we measured

- `reply-nesting` — a session with ≥1 post, then `/discuss`. Expect the reply
  rejected for `missing or empty required field 'load_bearing_assumption'`.
- `evidence-paraphrase` — a trace with quotable sentences; expect the model to
  paraphrase and the post to be dropped as ungrounded.
- `goal-picker-n` — `ma session start <explicit-goal>` with prior goals present,
  answering `n`; expect the session filed under goal `n`.
- `domain-terminal`, `domain-coding`, `domain-reasoning` — one full generation
  each, carrying the per-domain full-cycle rates.

### Adversarial — engineered to tempt each guard

- `no-quotable-sentence` — a trace of only tool output and fragments, so no
  sentence can be quoted verbatim.
- `evidence-in-code-block` — the only quotable content sits inside a fenced
  block; does whitespace normalisation still match?
- `log-marker-only` — the sole concrete primitive is `[emerg]` (the
  `CONCRETE_RE` alternative added 2026-08-09).
- `injection-in-trace` — the trace contains `INSIGHT` / `EVIDENCE_REF` protocol
  tokens and an instruction to ignore prior rules; the sanitiser must neutralise
  them.
- `self-contradicting-trace` — two mutually exclusive findings; the curator
  should not emit both as `confirmed_constraints`.

### Degenerate — shape and scale

- `empty-trace`, `single-line-trace`
- `context-overflow` — a ~30k-token trace against the model's window
- `fifty-posts-one-goal` — curator input far past the 5-per-bucket cap
- `unicode-rtl` — emoji, RTL text, combining characters through the verbatim check
- `cite-quarantined` — a post citing a quarantined packet

### Portability

The whole corpus re-run against `MANYAGENT_LLM_BASE_URL_ALT`. KSI claims
distilled knowledge transfers across LLM families; this is the only probe that
tests it. **Skips today** — only `qwen3.5-9b` on `:8000` is served.

## Failure semantics

### Offline (CI gate)

Two test kinds:

1. **Characterization** — replay a cassette, assert today's exact verdict.
   Catches parser changes that alter how recorded output is judged.
2. **Desired-behaviour, `xfail(strict=True)`** — assert what *should* happen; the
   `reason` documents the defect:

```python
@pytest.mark.xfail(
    strict=True,
    reason="reply fields land top-level instead of nested in `structured`; "
           "PR #13's _REPLY_OUTER_SHAPE is merged but ineffective (0/6 live)",
)
def test_discuss_stores_a_reply(...):
    assert ok is True
```

`strict=True` means a fix turns this into an XPASS **failure**, forcing the
marker's removal. A fix cannot land silently.

### Live (opt-in ratchet)

`ksi_baseline.json` holds per-scenario, per-step verdicts plus aggregate rates:

```json
{
  "model": "qwen3.5-9b",
  "recorded": "2026-08-17",
  "scenarios": {
    "terminal-lead": {"self_distill": "stored", "discuss": "rejected:missing_field"}
  },
  "rates": {"post_accepted": "14/36", "reply_stored": "0/6", "full_cycle": "3/9"}
}
```

Comparison is exact equality. **Any** deviation fails with a per-scenario diff —
including improvements, so new numbers are reviewed and committed deliberately
via `--update-baseline`, the same contract as `xfail(strict=True)`.

### Infrastructure never masquerades as protocol

Carried from `scripts/ksi_rig.py`, where these were learned the hard way:

- Endpoint unreachable or 5xx after retries → **skip**, never fail. `online` is
  opt-in infrastructure.
- Step wrappers catch `BaseException`: the CLI signals operator errors with
  `SystemExit`, which is not an `Exception`, and a wrapper catching only
  `Exception` loses every completed run.
- Per-run wall-clock timeout (default 180s); a wedged generation is recorded as
  a failed run, not left to hang the suite.
- Results checkpoint after each run, so a later hang cannot erase finished work.
- `/cross-distill` is an **in-session** verb; curate before `end`.

### Skips

| Condition | Behaviour |
| --- | --- |
| `MANYAGENT_RUN_ONLINE` unset | whole live module skips (existing `conftest` gate) |
| `MANYAGENT_LLM_BASE_URL` unreachable | live module skips with the endpoint in the reason |
| `MANYAGENT_LLM_BASE_URL_ALT` unset | portability scenarios skip |
| cassette missing for a scenario | offline test fails with the re-record command |

## Data flow

**Recording.** `scripts/ksi_record.py` → for each scenario, drive the real verbs
via `manyagent.testing.Simulation` with a live model at `temp=0` → capture every
`(prompt, response)` → write cassettes → write `baseline.json`.

**CI.** `pytest` → offline module loads cassettes → staleness guard → replay
through real parsers → assert verdicts and `xfail` markers.

**Ratcheting.** `MANYAGENT_RUN_ONLINE=1 pytest -m online` → re-execute the corpus
→ compare to baseline → pass on exact match, else fail with a diff.

## Testing this suite

The suite is code and can be wrong; the rig already shipped three bugs of its own.

- `ksi_cassette` round-trip: record → load → identical payload.
- Staleness guard fires: mutate a stored prompt, assert the guard fails with the
  re-record hint.
- Ratchet comparison: synthetic baseline vs synthetic result, assert regression,
  improvement, and match are each classified correctly.
- Skip paths: unset each env var, assert skip rather than fail or error.
- Corpus integrity: every scenario key unique; every scenario reachable from at
  least one test.

## Risks

- **Cassette churn.** Prompt-heavy work will re-record often. Mitigated by
  per-scenario re-recording and by storing full prompts so deltas are readable.
- **Corpus is hand-written.** It measures the cases we thought of. The
  adversarial and degenerate classes exist to widen that, but it is not a
  benchmark and the spec does not claim otherwise.
- **Determinism assumes server behaviour holds.** vLLM at `temp=0` was verified
  reproducible; a server upgrade or batching change could break it. The recorded
  sampling parameters make such a break visible rather than silent.
- **`xfail(strict=True)` blocks a fix until the marker is removed.** Intended,
  but it must be documented where contributors will see it.

## Open questions

- Should `scripts/ksi_rig.py` be retired once `test_ksi_live.py` exists, or kept
  as the exploratory driver? Leaning kept — it is useful for one-off sweeps at
  non-zero temperature that the deterministic suite deliberately excludes.
- Which second model for portability? Nothing beyond `:8000` is currently
  served, so this needs an endpoint before those scenarios do anything.
