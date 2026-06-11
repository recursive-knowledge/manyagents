<script>
	// The raw-trace body view (pre-alpha public — oms.web.md 2026-06-10):
	// three projections of the same captured run.
	//   Replay        — asciinema player over the synthesized cast
	//   Terminal text — the stream replayed through a server-side VT emulator
	//   Conversation  — the harness's own transcript, mined at run end (M13)
	// plus a download of the exact stored envelope. The player is DOM + WASM
	// (SSR-unsafe), so it is imported dynamically inside onMount; the SPA
	// shell never touches it. Conversation turn timestamps become player
	// markers (run_started anchors wall-clock → cast offsets), and each turn
	// can seek the replay to its moment.
	import { onMount, onDestroy } from "svelte";
	import { castUrl, getPacketRaw, traceText, traceConversation } from "$lib/api.js";
	import "asciinema-player/dist/bundle/asciinema-player.css";

	export let sessionId;
	export let uuid;

	let mode = "replay"; // "replay" | "text" | "conv"
	let playerEl;
	let player = null;
	let playerErr = null;

	let rawText = null; // the server-rendered terminal-text projection
	let rawErr = null;
	let loadingRaw = false;
	let envelopeBody = null; // the stored envelope JSON string (download only)

	let conv = null; // the mined conversation artifact
	let convMissing = false; // 404 — run predates mining (or no transcript found)
	let convErr = null;

	function offsetOf(turn) {
		// Wall-clock turn timestamp → seconds into the replay. Only meaningful
		// for real-timed casts: synthetic-pacing traces (legacy, or any run a
		// scrub-collapse flattened) carry `timed: false` and get no markers/
		// seek at all. The anchor is `cast_t0` — the wall-clock instant the
		// cast's zero maps to (first event ≈ spawn + a few hundred ms) — which
		// the server stamps; `run_started` is a coarse fallback.
		if (!conv?.timed || !turn?.ts) return null;
		const anchor = conv.cast_t0 ?? conv.run_started;
		if (!anchor) return null;
		const off = Date.parse(turn.ts) / 1000 - anchor;
		return Number.isFinite(off) && off >= 0 ? off : null;
	}

	function markersOf(artifact) {
		const out = [];
		for (const seg of artifact?.segments ?? []) {
			for (const turn of seg.turns ?? []) {
				const off = offsetOf(turn);
				if (off == null || turn.role === "tool") continue;
				const snippet = (turn.text ?? "").split("\n")[0].slice(0, 40);
				out.push([off, `${turn.role === "user" ? "❯" : "●"} ${snippet}`]);
			}
		}
		return out.sort((a, b) => a[0] - b[0]);
	}

	onMount(async () => {
		// Markers must be known at create() time (asciinema-player 3.15 has no
		// post-create marker API), so the conversation IS needed before the
		// player — but the player's heavy WASM import is independent, so run
		// them concurrently and bound the fetch so a slow/hung /api/rendition
		// can't leave the default Replay tab blank forever (it just loses its
		// markers). The full conversation still loads for the tab regardless.
		const convFetch = traceConversation(sessionId, uuid)
			.then((c) => {
				conv = c;
				return c;
			})
			.catch((e) => {
				if (String(e?.message ?? "").startsWith("HTTP 404")) convMissing = true;
				else convErr = e?.message ?? String(e);
				return null;
			});
		const apImport = import("asciinema-player");
		const timeout = new Promise((resolve) => setTimeout(() => resolve(null), 4000));
		const [AP, convForMarkers] = await Promise.all([apImport, Promise.race([convFetch, timeout])]);
		const markers = convForMarkers ? markersOf(convForMarkers) : [];
		try {
			player = AP.create(castUrl(sessionId, uuid), playerEl, {
				fit: "width",
				idleTimeLimit: 2,
				terminalFontSize: "12px",
				theme: "asciinema",
				...(markers.length ? { markers } : {})
			});
		} catch (e) {
			playerErr = e?.message ?? String(e);
		}
	});

	onDestroy(() => player?.dispose?.());

	async function showText() {
		mode = "text";
		if (rawText != null || loadingRaw) return;
		loadingRaw = true;
		rawErr = null;
		try {
			rawText = await traceText(sessionId, uuid);
		} catch (e) {
			rawErr = e?.message ?? String(e);
		} finally {
			loadingRaw = false;
		}
	}

	function seekTo(turn) {
		const off = offsetOf(turn);
		if (off == null || !player) return;
		mode = "replay";
		player.seek?.(off);
		player.play?.();
	}

	async function download() {
		if (envelopeBody == null) {
			const p = await getPacketRaw(sessionId, uuid).catch(() => null);
			envelopeBody = p?.trace ?? null;
			if (envelopeBody == null) return;
		}
		const blob = new Blob([envelopeBody], { type: "application/json" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `${sessionId}-${uuid}.trace.json`;
		a.click();
		URL.revokeObjectURL(a.href);
	}

	const ROLE_LABELS = { user: "❯ user", assistant: "● assistant", tool: "⚙ tool" };
</script>

<div class="trace">
	<div class="tabs">
		<button class:active={mode === "replay"} on:click={() => (mode = "replay")}>
			▶ Replay
		</button>
		<button class:active={mode === "text"} on:click={showText}>
			Terminal text
		</button>
		<button class:active={mode === "conv"} on:click={() => (mode = "conv")}>
			Conversation
		</button>
		<button class="dl" on:click={download} title="Download the stored trace envelope (JSON)">
			↓ envelope
		</button>
	</div>

	<!-- The player stays mounted across tab switches (display gated by CSS)
	     so returning to Replay never refetches or resets playback. -->
	<div class="pane" class:hidden={mode !== "replay"}>
		{#if playerErr}
			<p class="err">Replay unavailable: {playerErr}</p>
		{/if}
		<div class="player" bind:this={playerEl}></div>
	</div>

	{#if mode === "text"}
		<div class="pane">
			{#if loadingRaw}
				<p class="muted note">Loading trace…</p>
			{:else if rawErr}
				<p class="err">Could not load the trace body: {rawErr}</p>
			{:else if rawText != null}
				<pre class="rawtext">{rawText}</pre>
			{/if}
		</div>
	{/if}

	{#if mode === "conv"}
		<div class="pane">
			{#if convMissing}
				<p class="muted note">
					No conversation rendition for this trace — it is mined from the
					harness's own transcript at run end, and this run predates mining
					(or no transcript was found).
				</p>
			{:else if convErr}
				<p class="err">Could not load the conversation: {convErr}</p>
			{:else if conv}
				{#if conv.completeness && conv.completeness !== "full"}
					<p class="warn">
						Transcript {conv.completeness} — some of this run's harness
						sessions could not be recovered.
					</p>
				{/if}
				{#each conv.segments ?? [] as seg (seg.harness_session_id)}
					{#if (conv.segments ?? []).length > 1}
						<div class="seg-head mono">harness session {seg.harness_session_id}</div>
					{/if}
					{#each seg.turns ?? [] as turn}
						<div class="turn">
							<div class="turn-meta">
								<span class="role role-{turn.role}">{ROLE_LABELS[turn.role] ?? turn.role}</span>
								{#if turn.ts}
									<span class="muted">{new Date(turn.ts).toLocaleTimeString()}</span>
								{/if}
								{#if player && offsetOf(turn) != null}
									<button class="seek" on:click={() => seekTo(turn)} title="Seek the replay to this moment">
										▶
									</button>
								{/if}
							</div>
							{#if turn.tool}
								<details class="tool">
									<summary class="mono">{turn.tool.name}</summary>
									<pre>{turn.tool.input_preview}</pre>
								</details>
							{:else}
								<pre class="turn-text">{turn.text}</pre>
							{/if}
						</div>
					{/each}
				{/each}
			{:else}
				<p class="muted note">Loading conversation…</p>
			{/if}
		</div>
	{/if}

	<p class="muted note">
		Complete PTY capture of the wrapped session, scrubbed (v1) before it
		reached the Bank. Traces captured since timed capture landed
		(2026-06-10) replay the session's real cadence; older ones predate the
		timing sidecar and play with synthetic pacing.
	</p>
</div>

<style>
	.trace {
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
	}

	.tabs {
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.tabs button {
		font: inherit;
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--text-secondary);
		background: var(--bg-tertiary);
		border: 1px solid var(--border-primary);
		border-radius: 999px;
		padding: 2px 12px;
		cursor: pointer;
	}

	.tabs button.active {
		background: var(--brand-indigo-soft);
		color: var(--accent-primary);
		border-color: rgba(67, 56, 202, 0.25);
	}

	.tabs .dl {
		margin-left: auto;
	}

	.pane.hidden {
		display: none;
	}

	.player {
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		overflow: hidden;
		/* the terminal is naturally dark; keep it inside light card chrome */
		background: #121314;
	}

	.rawtext {
		margin: 0;
		padding: var(--space-md);
		max-height: 32rem;
		overflow: auto;
		font-size: 0.72rem;
		line-height: 1.5;
		white-space: pre-wrap;
		word-break: break-word;
		background: var(--bg-tertiary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		color: var(--text-primary);
	}

	/* Conversation */
	.seg-head {
		font-size: 0.72rem;
		color: var(--text-muted);
		padding: 6px 0 2px;
		border-bottom: 1px dashed var(--border-primary);
	}

	.turn {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: var(--space-sm) 0;
	}

	.turn + .turn {
		border-top: 1px solid var(--border-primary);
	}

	.turn-meta {
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 0.76rem;
	}

	.role {
		font-weight: 700;
	}

	.role-user {
		color: var(--accent-primary);
	}

	.role-assistant {
		color: var(--type-distill, #047857);
	}

	.role-tool {
		color: var(--text-muted);
	}

	.seek {
		font: inherit;
		font-size: 0.7rem;
		color: var(--text-secondary);
		background: var(--bg-tertiary);
		border: 1px solid var(--border-primary);
		border-radius: 999px;
		padding: 0 8px;
		cursor: pointer;
	}

	.seek:hover {
		color: var(--accent-primary);
		border-color: var(--accent-primary);
	}

	.turn-text {
		margin: 0;
		font-size: 0.8rem;
		line-height: 1.55;
		white-space: pre-wrap;
		word-break: break-word;
		font-family: var(--sans);
		color: var(--text-primary);
	}

	.tool {
		font-size: 0.76rem;
	}

	.tool summary {
		cursor: pointer;
		color: var(--text-secondary);
	}

	.tool pre {
		margin: 4px 0 0;
		padding: var(--space-sm);
		font-size: 0.7rem;
		white-space: pre-wrap;
		word-break: break-word;
		background: var(--bg-tertiary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
	}

	.warn {
		font-size: 0.78rem;
		margin: 0;
		padding: var(--space-sm) var(--space-md);
		color: var(--brand-amber-dark, #92400e);
		background: var(--brand-amber-soft, #fef3c7);
		border: 1px solid var(--brand-amber, #f59e0b);
		border-radius: var(--radius);
	}

	.note {
		font-size: 0.74rem;
		margin: 0;
	}

	.err {
		font-size: 0.8rem;
		color: var(--brand-amber-dark, #92400e);
		background: var(--brand-amber-soft, #fef3c7);
		border: 1px solid var(--brand-amber, #f59e0b);
		border-radius: var(--radius);
		padding: var(--space-sm) var(--space-md);
		margin: 0;
	}
</style>
