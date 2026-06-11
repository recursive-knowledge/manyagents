# Oh My Swarm — Trace Renditions & Mining (M12–M14)

Status: **plan** (M12 groundwork shipped 2026-06-09: non-TTY capture fix,
`oms._hook` lifecycle-hook binding sink, list-aware installer merge). The
*why* below was researched against live docs (Claude Code hooks/sessions
references, asciinema cast v2 spec + player, Codex/Gemini local-storage
source) and against this repo; load-bearing claims cite their source.

## 1. The reframe: one run, three renditions

The 2026-06-10Z "trial" run proved two things at once:

1. The PTY byte stream is a **reliable but uninterpretable** modality — the
   855 KB blob stored as `trial/951450c1` replays faithfully but carries no
   timing (one `TraceEvent` at `ts=0.0`) and no structure.
2. The harness already writes the symbolic trace for us —
   `~/.claude/projects/<munged-cwd>/<session-id>.jsonl` held every turn,
   tool call, and timestamp of the same run.

So this is not "raw capture + an asciinema feature + a mining feature".
It is **one captured run with three renditions**, each canonical for a
different question:

| rendition | source                  | canonical for                                   |
|-----------|-------------------------|--------------------------------------------------|
| `raw`     | PTY/pipe tee (today)    | byte-fidelity backup; scrub target               |
| `cast`    | the same tee + timing   | *what it looked like* — terminal replay (web)    |
| `harness` | mined local harness files | *what it meant* — turns, tools, edits, timing  |

All three hang off the **existing `raw` packet** (no new packet types; the
`packets_type_chk` constraint stays untouched). New storage is a sibling
table (migration `00008`, the `00007` injections-table precedent):

```sql
create table trace_renditions (
  packet_id  text not null references packets(id),
  format     text not null check (format in ('cast', 'harness')),
  body       text not null,
  miner_version text,          -- scrub/miner provenance, e.g. 'cast-v1', 'claude-miner-v1'
  complete   boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (packet_id, format)
);
```

`traces` (the raw body) is unchanged. Renditions are **upserts** keyed on
`(packet_id, format)` so mid-session refreshes are idempotent — the same
discipline as `oms.distill`'s content-addressed re-runs.

## 2. Decision: write the cast ourselves, don't shell out to `asciinema rec`

The obvious alternative — install asciinema and wrap the agent in
`asciinema rec -c "claude …" out.cast` — was considered and rejected:

- **It inverts the architecture.** asciinema's recorder owns the PTY
  (`pty.fork` inside asciinema). oms would become a consumer of its output
  — losing our tee (the byte-exact `raw` backup), our `TIOCSWINSZ`/
  `SIGWINCH` size sync, our two-stage SIGINT handling, and the
  `OMS_SESSION` env threading, or stacking a second nested PTY under ours
  with two layers of signal/winsize forwarding to keep honest.
- **The cast is lossy; the raw stream must not be.** Cast v2 event data is
  UTF-8 JSON; invalid bytes become U+FFFD irreversibly (asciinema itself
  decodes with `codecs.getincrementaldecoder("UTF-8")("replace")` —
  v2.4.0 `asciicast/v2.py`). If asciinema owns the capture, the byte-exact
  raw rendition can no longer be reconstructed. Raw must come first; cast
  is a derived projection.
- **The dependency buys ~15 lines.** Cast v2 is NDJSON: one header line +
  one `[elapsed_seconds, "o", chunk]` line per read — written incrementally
  from exactly the loop oms already runs (`cli.py` `_pty_spawn` master-read
  site). asciinema 3.x is a Rust binary (not pip-installable as a library);
  2.x is a Python app, not an API. A system-binary dependency + subprocess
  + format coupling vs. a dozen lines of stdlib in a loop we already own.
- **What we'd give up matters; what we'd gain doesn't.** asciinema rec adds
  idle-limit/title/env capture (trivial header fields), `"i"` stdin events
  (we can add from our stdin branch if ever wanted), and upload to
  asciinema.org (out of scope — the Bank is our store).

The **player** is a different story: we do adopt `asciinema-player` (npm,
Apache-2.0, ~64 KB gz) in the viewer rather than rendering ANSI ourselves —
its WASM terminal (avt) is a byte-driven state machine that handles
arbitrary chunk boundaries, including escape sequences split across events.

## 3. M12 — timed capture + cast rendition

Goal: every wrapped run leaves a playable `.cast` rendition in the Bank,
with the raw blob unchanged as backup.

> **M12.1 shipped early (2026-06-10, oms.cli.md):** both spawn loops write a
> timing sidecar (`<tee>.timing`, one `"<offset_s> <n_bytes>"` line per
> read), and `_timed_events` builds timestamped `TraceEvent`s (incremental
> UTF-8 decode; collapse-to-single-event safety valve when the joined text
> scrub-hits). New ≤2 MiB captures already replay real cadence end-to-end
> via `/api/cast`. Remaining for M12 proper: the asciicast-at-source second
> tee, the `trace_renditions` table, and >2 MiB timing survival (today
> `_bound_pty` still flattens those).

1. **Timestamp at the source** (`cli.py::_pty_spawn`): timing cannot be
   reconstructed post-hoc — this is the one structural change.
   - Before the bridge loop: `t0 = time.monotonic()`; rows/cols from the
     `TIOCGWINSZ` ioctl already performed; write the cast header
     `{version: 2, width, height, timestamp, command, title: <session>}`.
   - At the single master-read site: append
     `[round(monotonic-t0, 6), "o", dec.decode(data)]` where `dec` is a
     **per-session `codecs.getincrementaldecoder("UTF-8")("replace")`** —
     per-chunk `.decode()` would shred multi-byte glyphs (box-drawing,
     emoji) split across 64 KiB reads into U+FFFD.
   - In the existing `SIGWINCH` handler: append `[elapsed, "r", "{cols}x{rows}"]`.
   - `_pipe_spawn` (the non-TTY path) does the same minus winsize (default
     80×24 header; no resize events).
   - The cast is a **second sidecar tee file**; the raw tee is untouched.
     NDJSON streams to disk, so a crashed session still leaves a playable
     prefix — strictly better crash behavior than today's build-at-exit.
   - Update the two monkeypatched doubles in lockstep:
     `tests/test_cli.py` spawn stub and
     `oms.testing.Simulation._play_transcript_through_pty`.
2. **Persist as rendition** (`oms.capture`): after the existing
   `persist()` stores the raw packet, scrub the cast (on the **joined**
   text, then re-split — per-event regexes can miss a secret straddling a
   chunk boundary) and upsert `trace_renditions(packet_id, 'cast', body)`.
   Record `cast_lossless: bool` in the header env (true iff the decoder
   never emitted U+FFFD) so downstream knows when o-concatenation equals
   the raw text. Do **not** route the cast through `CanonicalTrace.events`:
   `_bound_pty` flattens to 3 events past `OMS_TRACE_MAX_BYTES` (2 MiB),
   destroying timing exactly for the long sessions a player matters for.
   Cap the cast with its own tunable (`OMS_CAST_MAX_BYTES`, default 8 MiB;
   head+tail with a marker event, mirroring `bound.py`).
3. **Migration `00008`** (above) + `FakeBank` parity (`put_rendition` /
   `get_rendition`) — the curator-rejection incident showed FakeBank/live
   divergence is where offline-green/online-red bugs live; ship the gated
   integration test alongside.
4. **Fidelity note:** the non-TTY path keeps `source_fidelity="pty"` for
   now; if a separate `stream` fidelity is wanted it is a one-line
   `SOURCE_FIDELITIES` addition + conformance test, decided in M12 review.

## 4a. M13 concrete plan — the "Conversation" tab (added 2026-06-10; **SHIPPED same day** — M13.0–M13.3 all landed, see the four component-doc Decision-log entries dated 2026-06-10)

The user-facing goal, stated plainly: a raw-trace page shows **three tabs**
— Replay (asciinema, shipped) · Terminal text (pyte projection, shipped) ·
**Conversation** (the harness's own transcript from
`~/.claude/projects/<munged-cwd>/<session-id>.jsonl`, mined and stored).
Sequenced so each step lands green on its own:

1. **M13.0 — storage (migration `00009`).** `trace_renditions(packet_id →
   packets, format text check in ('harness'), body text, miner_version,
   complete, created_at, primary key (packet_id, format))` + anon SELECT
   gated the same way as 00008 (quarantine-joined policy). Bank methods
   `put_rendition` (upsert on the PK — idempotent re-mining) /
   `get_rendition`, with **FakeBank parity in the same commit** (the
   curator-23502 and quarantine lessons both came from fake/live drift).
2. **M13.1 — the miner.** `Adapter.mine(ctx) -> MinedConversation | None`
   as the third optional ABC hook (the `install_skills` pattern;
   `oms.adapters.skills.claude` delegation precedent). `MineContext =
   {cwd, window: (run_started, run_ended), bindings}` — `_do_run_agent`
   already has all three (bindings via `_harness_bindings`, shipped). The
   Claude miner reads every bound `transcript_path` (one PTY run spans
   several harness sessions across `/clear` — observed live), falls back
   to the munged-cwd + mtime-window scan when no bindings exist, and
   parses defensively (`_text`-fallback precedent in
   `adapters/builtin/__init__.py`) into the normalized shape:

   ```json
   {"miner_version": "claude-v1", "binding": "hook|scan",
    "completeness": "full|partial|none",
    "segments": [{"harness_session_id": "...", "turns": [
      {"role": "user|assistant|tool", "ts": "...", "text": "...",
       "tool": {"name": "...", "input_preview": "..."} }]}]}
   ```

   Mining runs in `_do_run_agent` right after `persist()` (it has the new
   packet id), wrapped in the same never-fail try/except as capture, and
   the body is **scrubbed** (transcripts carry full tool outputs) before
   `put_rendition(pid, 'harness', …)`.
3. **M13.2 — the API + tab.** `GET /api/rendition/{session}/{p}/harness`
   behind the existing `_gated_trace_body`-style gate (public switch +
   quarantine + 404-not-oracle + projection cache header). `TraceView`
   gains the Conversation tab: turns rendered like the forum's
   `StructuredView` pattern (role badge, timestamp, collapsed tool
   calls), `completeness` surfaced honestly ("transcript partially
   recovered") rather than silently truncated.
4. **M13.3 — the payoff loop.** Turn timestamps + the cast header's start
   time → `markers` option on the player: click a turn in Conversation →
   seek the Replay to that moment (±1 s). Mid-session refresh (the MCP /
   `PostToolUse` trigger) stays deferred until exit-time mining proves
   insufficient.

Decisions deliberately NOT made yet (decide at M13.0 review): whether the
rendition row also serves `oms.distill` as a structured-fidelity input for
PTY sessions (it is exactly the `structured` trace the curator wishes it
had), and whether `mine()` failures should quarantine-flag the raw packet
or merely log.

## 4b. M13 — `Adapter.mine()` + the Claude miner (original sketch)

Goal: every wrapped run also leaves a `harness` rendition — the structured
conversation (turns, tool calls, files touched, timestamps) mined from the
harness's own local files.

1. **Binding (shipped as groundwork, 2026-06-09).** Hooks push the answer
   to us: Claude Code's `SessionStart`/`SessionEnd` hooks deliver
   `{session_id, transcript_path, cwd, reason}` on stdin; the installed
   `oms._hook` sink appends them to `$OMS_HOME/bindings/<session>.jsonl`
   iff `OMS_SESSION` is set. This survives `/clear` (observed in the trial
   forensics: one PTY run, two transcript files — `SessionEnd reason:
   "clear"` then a fresh `SessionStart`). Fallback tiers when no binding
   records exist (hooks declined at consent, or harness predates hooks):
   (a) `--session-id <uuid>` pinning injected by the wrapper at spawn
   (Claude-only; initial session only), (b) munged-cwd directory scan
   filtered by the run's wall-clock window. Record which tier produced the
   binding in the artifact (`binding: hook | pinned | scan`).
2. **ABC seam:** a sixth optional method, the `install_skills` pattern:

   ```python
   def mine(self, ctx: MineContext) -> MinedConversation | None:  # default None
   ```

   `MineContext = {cwd, window: (start, end), bindings: list[BindingRecord],
   raw_bytes: bytes}` — built by `_do_run_agent` post-spawn (it already has
   everything; `run_started` landed with the groundwork). Layering is clean:
   adapters already import `oms.capture`.
3. **Claude miner** (`oms.adapters.builtin.claude`): parse each bound
   transcript jsonl **defensively** (the format is undocumented and
   drifts; the `_text` fallback in `builtin/__init__.py` is the in-repo
   precedent) into a normalized shape: messages (role, ts, text), tool
   calls (name, key inputs), models, token usage if present, files edited.
   Multiple session ids → one artifact with ordered segments. Include
   `completeness` + `miner_version` fields — transcripts can be missing or
   truncated relative to the harness's own state.
4. **Persist** as `trace_renditions(packet_id, 'harness', json)` — scrubbed
   (the transcript contains full tool outputs), upserted (idempotent).
5. **Mid-session refresh (the "/self-distill should update the trace"
   ask): yes, two triggers, ship the cheap one first.** (a) The MCP server
   already executes oms code mid-session with `OMS_SESSION` in env — after
   `commit_post`/`cross_distill` it can re-mine the bound transcripts and
   upsert the rendition (no raw packet exists yet mid-run, so the refresh
   targets a session-scoped provisional packet id, or simply defers
   persist-to-Bank to exit and refreshes a local staging file). (b) A
   `PostToolUse` hook with matcher `mcp__oms__.*` — verified syntax — if
   harness-side triggering is preferred. Decision point at M13 review;
   exit-time mining alone already covers the primary need.
6. **Codex/Gemini miners** are follow-ups, not blockers — both harnesses
   now ship hooks delivering the same `{session_id, transcript_path, cwd}`
   stdin payload, and both store local transcripts (Codex:
   `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, cwd in the line-1
   SessionMeta; Gemini: `~/.gemini/tmp/<shortId>/chats/`). The ABC seam is
   designed against all three; only the parsing is per-adapter.

## 5. M14 — viewer: replay + structured conversation

1. ~~Security gate decision~~ **Decided + shipped early (2026-06-10,
   pre-alpha):** scrubbed raw trace bodies are PUBLIC — `OMS_WEB_PUBLIC_RAW`
   tunable (default on) + migration `00008` anon grant on `traces`; the M9
   anon-exclusion is the switch's off position (oms.web.md Decision log).
   `GET /api/cast/{session}/{p}` already serves an asciicast v2 rendition
   synthesized from the stored envelope (synthetic pacing pre-M12; the same
   endpoint replays real timing once M12 lands timestamped chunks). M14
   re-points it at the `trace_renditions` table instead of synthesizing.
2. ~~`web/viewer`: asciinema-player~~ **Shipped early (same date):**
   `TraceView.svelte` on the `/t/` trace pages — client-only player
   (`fit: 'width'`, `idleTimeLimit: 2`), plain-text inspection tab,
   envelope download.
3. A structured-conversation component for the `harness` rendition,
   following `StructuredView.svelte`'s ordered-fields-with-JSON-fallback
   pattern.
4. **The unification payoff:** the miner emits cast **markers** (`"m"`
   events, or the player's `markers` option) at turn/tool boundaries by
   aligning harness timestamps with the cast header's unix `timestamp`
   (±1 s skew). Click a turn in the structured view → seek the terminal
   replay to that moment.

## 6. Risks & open questions (carried into milestone reviews)

- **Cast size**: Claude Code is a high-frame-rate TUI; casts run larger
  than the logical transcript. The player fetches whole files; practical
  smooth-playback ceiling is tens of MB. `OMS_CAST_MAX_BYTES` + the
  bounded head/tail policy is the guardrail; the split worker bundle is
  the escalation if dense recordings stutter.
- **Cross-chunk scrub**: chunked events break per-event secret regexes;
  M12 scrubs the joined stream. Quasi-identifiers (home-dir username,
  resume UUID, wall-clock TZ) survive scrub v1 by design — revisit only if
  renditions go public (M14 gate).
- **Hook trust/consent**: hooks ride the existing `OMS_INSTALL_SKILLS`
  consent panel + manifest + uninstall. Declining hooks must degrade to
  scan-tier binding, never break capture.
- **Stale-process trap** (the trial's second curator failure): the MCP
  server caches imported modules; mining logic reachable from the MCP
  child must tolerate running pre-edit code mid-session.
- **`--session-id` semantics on `/clear`** are not documented; our local
  evidence says `/clear` rolls a new id + file. The hook tier makes this
  moot, but re-verify before relying on pinning alone anywhere.
- **Non-TTY runs** now capture (2026-06-09 fix) but have no winsize and no
  resize events; their casts render at 80×24. Acceptable; revisit if
  headless replay matters.
