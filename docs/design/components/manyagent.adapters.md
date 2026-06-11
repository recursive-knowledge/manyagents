---
tags:
  - documentation
  - manyagent
  - knowledge-curation
---

## Status

- **Lifecycle:** Planned — the primary extension point.
- **Last reviewed:** 2026-05-19. Follows `ManyAgent - Design Principles.md`.
- **Validated by datasmith, not just asserted:** datasmith went Codex-only → multi-agent auto-detect (Claude/Codex/Gemini/Qwen) via an explicit `get_agent()`/`InstalledAgent` abstraction. An explicit adapter ABC + registry is the design that *survived* contact (Design Principles §4, §5). Keep it; this doc records *why* it's right.

## Abstract

`manyagent.adapters` is the agent adapter/plugin system. An *adapter* is the small object-oriented integration that lets `manyagent` source a session's trace and inject context for a specific terminal agent. It produces **raw** material only; normalization/bounding/scrubbing is `manyagent.capture`'s job (Design Principles §2 — keep adapters dumb so the public corpus isn't only as safe as the worst third-party plugin).

## High level overview

```mermaid
graph LR
    A --> B
    B --> C
    B --> D
    B --> E

    A[Plugin Hub / GitHub plugins dir]
    B["`manyagent.adapters
    (This Feature)`"]
    C[manyagent.cli]
    D[manyagent.capture
    raw → CanonicalTrace]
    E[Terminal LLM agent CLIs]
```

## Modules

* `manyagent.adapters.base`: The `Adapter` ABC — the whole contract: `invoke()`, `capture()`, `inject()`, `retrieve()`, optional `distill_model()`, plus identity metadata.
* `manyagent.adapters.registry`: Local discovery + plugin-hub fallback (`manyagent.org/plugins/<name>`).
* `manyagent.adapters.builtin`: First-party `claude`, `codex`, `gemini`, `qwen` — reference impls and the smallest examples for contributors.

## Design Decisions

### Adapter (code) vs. Agent (record) — **Settled**

`Adapter` = stateless pluggable code for one CLI; `Agent` (`manyagent.core`) = a registered instance in one session. datasmith's surviving split (models vs. behavior) directly.

### The four-method contract — **Settled**

```python
class Adapter(ABC):
    name: str; binary: str; version: str
    @abstractmethod
    def invoke(self, args: list[str]) -> "session-attached subprocess": ...
    @abstractmethod
    def capture(self) -> "RawTrace":
        """Return RAW session material (native log or PTY bytes). Normalization,
        size-bounding, and SECRET SCRUBBING are NOT done here — manyagent.capture owns
        that, centrally and uniformly."""
    @abstractmethod
    def inject(self, context: str) -> None: ...
    @abstractmethod
    def retrieve(self) -> str | None: ...
    def distill_model(self): ...   # optional: expose the agent's own model to manyagent.distill
```

`capture()` returning *raw* (not a distilled or scrubbed result) is the load-bearing decision: contributors never touch scrub/prompt policy, so a malicious or careless community adapter cannot weaken the public corpus's safety guarantees. The previous draft folded normalization into a `PtyAdapter` mixin; that responsibility moved to `manyagent.capture` (Decision log).

### Registry resolution order — **Settled**

Local install (`~/.manyagent/adapters/<name>/`) → built-in → plugin hub (with the Overview's `[y/n]` download prompts). The hub serves **only adapters that were merged into the `manyagent` GitHub repo via a maintainer-reviewed PR** — it is not an arbitrary code-distribution channel. A community adapter is a pull request to `src/manyagent/adapters/` (or `plugins/`), open-source, manually reviewed, merged only if non-nefarious (see *Plugin trust*).

### `distill_model()` couples to the *abstract* hook only — **Settled**

`manyagent.utils.provider` depends on the ABC hook, not concrete adapters → no cycle with `manyagent.distill`. datasmith multi-agent validates designing this seam up front.

## Key Design Questions

### Plugin trust / sandboxing — **Settled (maintainer-review gate)**

The trust model is **human code review at PR time**, not runtime sandboxing of unknown code. An adapter enters the ecosystem only as a pull request to the `manyagent` repo: open-source, diffable, manually reviewed by a maintainer, merged only if it does nothing nefarious (no exfiltration of pre-scrub traces, no rogue injection, no network beyond the wrapped agent). The hub then serves only these merged, reviewed plugins. This is a deliberate scoping decision: there is **no arbitrary plugin ecosystem** (Open-Questions §C8), so the datasmith `tamper_audit`-style "assume adversarial third-party code" threat does not apply to adapters — it applies to *packet content* (handled in `manyagent.distill`/`manyagent.bank`), not adapter code. The first-download confirmation prompt remains as defense-in-depth. (Reversed from the prior **Fragile** rating per the 2026-05-19 user decision.)

### Mid-session injection — **Open**

`inject()` is adapter-specific (prepend block / leading prompt / stdin). A `PromptPrefixInjector` mixin is the lowest-common default. Open: injecting *between* turns for long-running agents that don't re-read context. Named, not silently assumed.

### Capture fidelity per agent — **Settled (adapter-author responsibility)**

The `CanonicalTrace` OO schema (`manyagent.capture`) is the *contract*. The **adapter author is responsible for emitting schema-conformant trace material** for their agent and is reviewed on it at PR time. `manyagent.capture` *validates conformance*, bounds, scrubs, and persists — it does **not** heuristically parse arbitrary terminal output. `claude`/`codex` authors map native structured logs and declare `source_fidelity="structured"`; a `gemini`/`qwen` author who only has a PTY tee declares `"pty"` and is responsible for making even that conform to the schema. This is the entire point of the OO setup: heterogeneity is absorbed by the plugin author against a fixed schema, not by central heuristics (Open-Questions §C9).

## Operations & recovery

- **Plugin pinning/rollback:** registry records installed plugin `version`; a bad community adapter can be pinned back. (datasmith pinned models/scripts for exactly this reason.)
- **Observability:** which adapter, plugin version, and `source_fidelity` per session — needed to attribute corpus quality/abuse to an adapter.

## Verification

* **Unit:** registry order (local > built-in > hub); ABC rejects a subclass missing any required method; `capture()` returns raw bytes/log faithfully (no scrub here — that's tested in `manyagent.capture`).
* **Integration (mock Bank, fake agent):** register fake adapter, `invoke()` scripted CLI, assert `capture()` raw passes to `manyagent.capture` and a (scrubbed) `raw` packet lands; `inject()`→`retrieve()` round-trips; plugin-hub not-found flow installs to `~/.manyagent/adapters/<name>/` and is found next call.
* Each built-in adapter passes a shared conformance suite.

## Decision log

- **2026-05-19 — `capture()` redefined to return RAW; normalization/scrub moved to `manyagent.capture`.** Rationale: a public corpus cannot delegate safety to third-party plugins (Design Principles §2). Supersedes the `PtyAdapter`-does-normalization framing.
- **2026-05-19 — Design validated against datasmith.** Explicit ABC+registry is what datasmith converged to (multi-agent `get_agent()`); recorded as Settled with evidence rather than left as an assumption.
- **2026-05-19 — Plugin trust RESOLVED to Settled (maintainer-review gate), reversing the earlier Fragile rating.** Per user decision: adapters are reviewed PRs, not arbitrary downloaded code; there is no open plugin ecosystem (Open-Questions §C8). The adversarial-input threat moves entirely to packet content (`manyagent.distill`/`manyagent.bank`).
- **2026-05-19 — Capture fidelity reframed as adapter-author responsibility against the fixed `CanonicalTrace` schema** (Open-Questions §C9). Removes any central PTY-parsing heuristic burden.
- **2026-05-19 (M5 build) — `RawTrace` resolved as an alias for `manyagent.capture.CanonicalTrace`, not a separate shape.** The ABC's frozen `capture() -> "RawTrace"` wording and `manyagent.capture.md`'s "the adapter author emits `CanonicalTrace`-shaped material" describe the *same* dataclass at different lifecycle stages: an adapter returns a `CanonicalTrace` that is still **raw** (pre-scrub, pre-bound — `scrub_report` empty, `bytes_out` 0); `manyagent.capture` then validates → scrubs → bounds → persists. Shipped as `RawTrace = CanonicalTrace` in `manyagent.adapters.base`. This is a seam-naming reconciliation between two frozen docs (Design Principles §3 doc-sync), not a behavioural divergence; no Settled decision changed.
- **2026-05-20 (M11 build) — `Adapter.install_skills` added (optional fifth method); transparency contract.** The M11 in-agent surface needs each adapter to know how to install its own slash commands + MCP server registration into the agent's native config (`~/.claude/skills/` + `claude mcp add` for Claude; `gemini extensions link` against a staged bundle for Gemini; `~/.codex/skills/` + `tomlkit`-preserving `~/.codex/config.toml` merge for Codex). Added as an **optional** method on the `Adapter` ABC with a `None`-returning default — adapters that don't expose an in-agent surface (e.g. the `qwen` stub) silently no-op so `_do_run_agent` keeps working. Implementations live in `manyagent.adapters.skills.{claude,codex,gemini}` (one module per adapter; each ships a `build_plan` returning an `manyagent._installer.InstallPlan` of `FileOp` + `CLIAction` entries). **Transparency contract** (manyagent._installer): every install is preceded by a printed plan + `[y/n/d]` consent prompt (override `MANYAGENT_INSTALL_SKILLS=auto|prompt|deny`); a per-adapter manifest at `$MANYAGENT_HOME/installed/<adapter>.json` records every absolute path with create-vs-merge, the merge_keys we own, and sha256-at-write; `manyagent status` lists it; `manyagent uninstall <adapter>` reverses cleanly by running the agent's unregister CLI first (so the agent unregisters while the bundle is still on disk — the M11.3 ordering lesson) then deleting created files (kept if sha256 changed since install — user-edited files are not touched); third-party content survives install→uninstall round-trip byte-identically (tested). This is a new ABC seam, not a behavioural change to the existing four methods; full design in `manyagent.cli.md` 2026-05-20 (M11 build) entry.
- **2026-06-09 (M12 groundwork) — Claude installer gains lifecycle hooks (`settings.json` shared-array merge); the `manyagent._hook` binding sink; stale docstring corrected.** The trace-mining redesign (see *Trace Renditions & Mining (M12–M14)* design doc) needs a deterministic binding from an manyagent-wrapped run to the harness's own local artifacts (Claude Code: `~/.claude/projects/<munged-cwd>/<session-id>.jsonl`). Verified mechanism: Claude Code hooks receive `{session_id, transcript_path, cwd, hook_event_name, reason}` as JSON on stdin. Settled: `manyagent.adapters.skills.claude.build_plan` now adds **two merge FileOps** into `~/.claude/settings.json` (the documented home for hooks — MCP registration stays on the `claude mcp` CLI; the module docstring's claim that we "MERGE the `mcpServers.manyagent` entry into `~/.claude/settings.json`" was stale M11.2-first-pass text and is corrected) installing `SessionStart` + `SessionEnd` hooks that run `<sys.executable> -m manyagent._hook`. **Hook arrays are shared territory** (the user's own hooks may live under the same event), so this shipped with a new installer merge primitive: `merge_json_list_item` / `unmerge_json_list_items` (`manyagent._installer`) append/remove exactly one item by structural equality — `merge_keys` tagged `list:<top>.<key>`, the appended item recorded in a new `ManifestEntry.merge_items` field so uninstall removes *our* entry and leaves neighbors untouched (round-trip byte-identical, tested; old manifests load via the dataclass default). `manyagent._hook` itself is a **stdlib-only, always-exit-0** sink: with `MANYAGENT_SESSION` set (only true under the manyagent wrapper — exported at PTY-spawn, inherited by the harness and its hook subprocesses) it appends the payload to `$MANYAGENT_HOME/bindings/<session>.jsonl`; without it (the user's everyday Claude sessions — the hooks are installed user-scope and fire for every session) it writes nothing; it never raises, prints, or exits nonzero (a misbehaving hook can disturb the host session); a `/` in `MANYAGENT_SESSION` is refused (path escape + Bank constraint). SessionStart/SessionEnd are deliberately the only events — per-tool hooks would put a subprocess spawn on the host's hot path for no binding gain. Consumption: `manyagent.cli._harness_bindings` surfaces this run's records post-capture (same-date `manyagent.cli.md` entry); `Adapter.mine()` (M13) is the real consumer. Adversarial-review hardening folded into the same change: (1) the hook item bakes `sys.executable` into the command, so a reinstall from a moved/recreated venv would accumulate a stale entry that fires (and errors) in every user session — list merges therefore carry a **staleness marker** (`__list_purge__` = `-m manyagent._hook`, recorded as `ManifestEntry.purge_contains`): install purges different-but-marked items before appending, uninstall removes marked items even after the user/host tool edited them (and reports `KEPT … remove manually` instead of claiming success when nothing matched); (2) the hook command is wrapped as `<python> -m manyagent._hook 2>/dev/null || true` so a dead interpreter path degrades to a silent no-op rather than a visible "hook error" notice at the start and end of every Claude Code session; (3) `apply_plan` saves a **partial manifest** before re-raising when a mid-apply failure (e.g. a user-shaped `settings.json` the merge can't parse — `hooks` as an array) would otherwise strand the already-written SKILL.md creates with no reversal path. Tests: `tests/test_hook.py`, list-merge + purge + partial-manifest suite in `tests/test_installer.py`, plan/install/uninstall/round-trip + stale-venv-purge updates in `tests/test_adapter_install.py`.
- **2026-06-09 — Consent prompt leads advisory; `InstallPlan.commands` added.** The `[y/n/d]` consent surface now leads with what the user gets (the slash commands + usage blurbs each installer declares via `InstallPlan.commands`, sourced from the shared `manyagent.adapters.skills.USAGE` table) and demotes the file-by-file plan + diff to the `[d]etails` keypress; first-run declines are recorded (`$MANYAGENT_HOME/installed/<adapter>.declined`) so the panel never re-walls. Full rationale + mechanics in `manyagent.cli.md` (same-date entry). Installer modules: per-verb descriptions replace the repeated "host-LLM procedure" boilerplate; `install()` passes `oma_home` through to `consent_prompt` for the decline marker.
- **2026-06-10 — `CLIAction.failure_ok` quiets expected pre-clear exits (user decision).** Every first install printed `manyagent: pre-clear any existing manyagent MCP server (--scope user) — exit 1: No user-scoped MCP server found with name: manyagent` — the pre-clear's nonzero exit IS the fresh-install case, so the note was pure noise. `CLIAction` gains `failure_ok: bool = False`; `_run_cli` suppresses the exit note when set. Set on exactly the two pre-clear actions (claude `mcp remove`, gemini `extensions uninstall`); everything else keeps printing — the M11.3 lesson (silent nonzero exits hide real bugs) still holds for actions whose success is load-bearing.
- **2026-06-10 — headless `distill_model()` hygiene: no `MANYAGENT_SESSION` inheritance; claude shells `--output-format json` with an envelope unwrap.** Two fixes from the FA04-ESNF trial. (1) `_HeadlessModel.complete` spawned the distiller CLI with the wrapper's full environment — including the `MANYAGENT_SESSION` that `_do_run_agent` exports for the PTY child. The user-scope `manyagent._hook` inside the spawned CLI therefore treated the *distiller* as a wrapped run: it appended the distiller's own harness session id + transcript path to `$MANYAGENT_HOME/bindings/<sid>.jsonl` (poisoning what `Adapter.mine()`/trace-grounding later reads — the wrong-session contamination) and delivered the session's inject stash as the distiller's SessionStart `additionalContext`. The shell-out now strips `MANYAGENT_SESSION` from the child env (everything else preserved). (2) `ClaudeAdapter._distill_cmd_prefix` is now `["claude", "-p", "--output-format", "json"]`: the headless answer arrives as one machine-readable `{"type": "result", "result": "<text>", …}` envelope instead of free prose at the mercy of markdown fences. `_HeadlessModel` gained an optional `extract` hook (default identity, supplied per adapter via the new `_StructuredBuiltin._distill_extract`); claude's override returns the envelope's `result` text (empty string on an error envelope — the caller's "no parseable post" path, never envelope-as-post garbage) and passes non-envelope output through untouched, so codex/gemini and the `MANYAGENT_LLM_*` fallback are unaffected. Tests: `tests/test_adapters.py` (env strip, prefix, unwrap matrix, extract-applied-through-`distill_model()`).
- **2026-06-10 (M13.1) — `Adapter.mine(ctx)` (optional sixth hook) + `manyagent.adapters.miners.claude`.** The harness-rendition seam from the Trace Renditions & Mining design doc §4a. ``MineContext = {cwd, window, bindings}`` (frozen dataclass in ``adapters.base``); the default returns None (no miner). The Claude miner reads every transcript named by this run's ``manyagent._hook`` binding records (``binding: "hook"`` — survives ``/clear`` rolling new session files; verified on the live corpus: a single run yielded 2–3 segments), falls back to scanning ``~/.claude/projects/<munged-cwd>/`` by run-window mtime (``binding: "scan"`` — recovered the pre-hook trial trace), and parses per-line defensively (``user`` string/block content; ``assistant`` ``text`` blocks → assistant turns, ``tool_use`` blocks → tool turns with a 400-char input preview; metadata entry types skipped; torn lines never kill a segment). Every text field is scrubbed **per-string** before the artifact leaves the miner — scrubbing the serialized JSON instead could let an env-kv redaction span structure and corrupt the document. Caps: 4000 chars/turn, 2000 turns/segment; ``completeness: full|partial`` is honest about unreadable bound files. ``DummyAdapter`` (manyagent.testing) gained ABC parity (``mine`` → None).
- **2026-06-10 (M13.1 hardening, adversarial review) — scrub-before-cap, basename-only path, tight scan window, size bound.** Review of the miner confirmed four issues, all fixed in `manyagent.adapters.miners.claude`: (1) **the on-disk `transcript_path` leaked the OS username + home tree into the public rendition** (it was never scrubbed and `manyagent.capture.scrub` has no path rule) — the segment now carries only the file basename (`transcript`), and the viewer never displayed the full path anyway. (2) **Cap-before-scrub** stranded sub-floor credential fragments: `_parse_transcript` now stores UNCAPPED text and `_scrub_then_cap` scrubs the full strings *then* truncates, so a key spanning the cap is redacted before truncation can hide its tail (the capture pipeline's documented scrub-before-bound order, which the miner had inverted). (3) **Scan-tier cross-contamination**: `_WINDOW_SLACK_S` cut 120s→15s — mining runs at the child's exit, so a transcript written well after belongs to a *different* session; a wide window swept concurrent same-cwd sessions into one run's public rendition. The hook tier (exact bound paths) is immune and remains the default. (4) **No size bound** vs the raw path's `MANYAGENT_TRACE_MAX_BYTES`: `_bound_artifact` caps the serialized body at 1 MB, dropping trailing turns/segments and downgrading `completeness`. In-conversation home paths in turn TEXT remain (same content the raw trace already exposes; scrub v1 quasi-identifier scope, unchanged). Tests added for each: basename-only, boundary-straddling key redaction, size bound.
- **2026-06-11 — headless `distill_model()` runs from an empty temp cwd (hermetic distiller).** Completes the 2026-06-10 hygiene entry's third leg. A live distillation (session `3BK6-0AFF`) showed the spawned `claude -p` — running in the repo cwd — loading its OWN project context (CLAUDE.md, git status) and blending it into the reflection as if it were session evidence (`proposed_next` cited "the primary design doc listed in git status"; the session trace contained no such resolution). `_HeadlessModel.complete` now runs the shell-out inside a `tempfile.TemporaryDirectory` (`cwd=` the empty dir, removed after): the spawned CLI finds no CLAUDE.md, no `.git`, no project dir, so the trace rendered into the prompt (manyagent.forum.md same date — which also gained the prompt-side fence sentence) is the distiller's only knowledge of the session. Applies to all three builtins via `_StructuredBuiltin.distill_model()`. Test: `tests/test_adapters.py::test_headless_complete_runs_in_hermetic_cwd`.
