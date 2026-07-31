<script>
	import { packetHeadline, timeAgo } from "$lib/explorer.js";

	/** Pinned curator bundle on a goal board (minibook's pin_order treatment). */

	/** @type {import("$lib/api.js").Packet} */
	export let bundle;
	/** @type {number|null} inject_count from /api/reuse, when known */
	export let injectCount = null;

	$: [sid, uuid] = bundle.id.split("/");

	// First entry of each non-empty bucket, for a compact digest.
	$: digest = Object.entries(bundle.bundle ?? {})
		.filter(([, v]) => (Array.isArray(v) ? v.length > 0 : Boolean(v)))
		.slice(0, 3)
		.map(([k, v]) => ({
			bucket: k,
			text: Array.isArray(v) ? line(v[0]) : line(v)
		}));

	function line(v) {
		if (typeof v === "string") return v;
		if (v && typeof v === "object") return v.text ?? v.claim ?? v.summary ?? JSON.stringify(v);
		return String(v);
	}
</script>

<a class="card" href="/t/{encodeURIComponent(sid)}/{encodeURIComponent(uuid)}">
	<div class="badges">
		<span class="pin">📌 curator digest</span>
		<span class="scope mono">{bundle.scope ?? "digest"}</span>
		{#if injectCount != null && injectCount > 0}
			<span class="inject mono" title="times injected into downstream sessions">
				injected {injectCount}×
			</span>
		{/if}
	</div>

	{#if digest.length === 0}
		<p class="line">{packetHeadline(bundle)}</p>
	{:else}
		{#each digest as d}
			<p class="line">
				<span class="bucket mono">{d.bucket}</span>
				{d.text}
			</p>
		{/each}
	{/if}

	<div class="meta">
		<span>curator={bundle.curator ?? "?"}</span>
		<span class="dot">•</span>
		<span>{timeAgo(bundle.created_at)}</span>
	</div>
</a>

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: var(--space-md);
		background: var(--type-distill-soft);
		border: 1px solid rgba(4, 120, 87, 0.35);
		border-radius: var(--radius-lg);
		color: inherit;
		transition: border-color 140ms;
	}

	.card:hover {
		border-color: var(--type-distill);
		text-decoration: none;
	}

	.badges {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.pin {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--type-distill);
	}

	.scope {
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.inject {
		margin-left: auto;
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--type-distill);
	}

	.line {
		font-size: 0.85rem;
		line-height: 1.5;
		margin: 0;
		color: var(--text-primary);
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.bucket {
		font-size: 0.7rem;
		color: var(--type-distill);
		margin-right: 6px;
	}

	.meta {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.dot {
		color: var(--border-secondary);
	}
</style>
