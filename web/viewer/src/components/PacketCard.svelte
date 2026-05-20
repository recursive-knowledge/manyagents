<script>
	import QuarantineBanner from "./QuarantineBanner.svelte";
	import Stars from "./Stars.svelte";
	import { packetHeadline, timeAgo } from "$lib/explorer.js";

	/** @type {import("$lib/api.js").Packet} */
	export let packet;
	export let onOpen = () => {};

	$: headline = packetHeadline(packet);
	$: pillClass =
		packet.type === "post"
			? "pill-post"
			: packet.type === "distill"
				? "pill-distill"
				: "pill-raw";
	$: kindLabel =
		packet.type === "post"
			? packet.kind ?? "post"
			: packet.type === "distill"
				? packet.scope ?? "bundle"
				: "trace";
	$: shortId = packet.id.split("/").pop() ?? packet.id;
</script>

<button class="card" type="button" on:click={onOpen}>
	{#if packet.quarantined}
		<QuarantineBanner compact />
	{/if}

	<div class="meta">
		<span class="pill {pillClass}">{packet.type}</span>
		<span class="kind">{kindLabel}</span>
		{#if packet.stance}
			<span class="pill pill-stance-{packet.stance}">{packet.stance}</span>
		{/if}
		{#if packet.goal}
			<a class="goal-tag" href="/g/{encodeURIComponent(packet.goal)}" on:click|stopPropagation>
				/{packet.goal}
			</a>
		{:else}
			<span class="goal-tag muted">/(ungoaled)</span>
		{/if}
	</div>

	<div class="headline">{headline}</div>

	<div class="footer">
		<span class="left">
			<a class="sid" href="/s/{encodeURIComponent(packet.session_id)}" on:click|stopPropagation>
				{packet.session_id}
			</a>
			<span class="dot">·</span>
			<span class="when">{timeAgo(packet.created_at)}</span>
			{#if packet.adapter}
				<span class="dot">·</span>
				<span class="adapter">{packet.adapter}</span>
			{/if}
			{#if packet.curator}
				<span class="dot">·</span>
				<span class="adapter">curator={packet.curator}</span>
			{/if}
		</span>
		<span class="right">
			{#if packet.type === "post"}
				<Stars value={packet.rating} />
			{/if}
			<span class="pid mono">{shortId.slice(0, 8)}</span>
		</span>
	</div>
</button>

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: 8px;
		width: 100%;
		padding: var(--space-md);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius-lg);
		text-align: left;
		cursor: pointer;
		transition:
			border-color 140ms,
			box-shadow 140ms,
			transform 140ms;
	}

	.card:hover {
		border-color: var(--accent-primary);
		box-shadow: var(--shadow-lg);
		transform: translateY(-1px);
	}

	.meta {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.kind {
		font-family: var(--mono);
		font-size: 0.72rem;
		color: var(--text-muted);
		text-transform: lowercase;
	}

	.goal-tag {
		margin-left: auto;
		font-family: var(--mono);
		font-size: 0.78rem;
		color: var(--accent-primary);
		padding: 2px 8px;
		border-radius: 999px;
		background: var(--brand-indigo-soft);
	}

	.goal-tag.muted {
		background: var(--bg-tertiary);
		color: var(--text-muted);
	}

	.goal-tag:hover {
		text-decoration: none;
		background: var(--brand-indigo);
		color: var(--text-inverse);
	}

	.headline {
		font-family: var(--sans);
		font-size: 0.98rem;
		line-height: 1.5;
		color: var(--text-primary);
		display: -webkit-box;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.footer {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--space-sm);
		padding-top: 8px;
		border-top: 1px dashed var(--border-primary);
		font-size: 0.78rem;
		color: var(--text-muted);
		flex-wrap: wrap;
	}

	.left {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		flex-wrap: wrap;
	}

	.right {
		display: inline-flex;
		align-items: center;
		gap: 10px;
	}

	.sid {
		font-family: var(--mono);
		font-size: 0.78rem;
		color: var(--text-secondary);
	}

	.sid:hover {
		text-decoration: underline;
		color: var(--accent-primary);
	}

	.dot {
		color: var(--text-muted);
	}

	.pid {
		font-size: 0.72rem;
		color: var(--text-muted);
		background: var(--bg-tertiary);
		padding: 1px 6px;
		border-radius: 4px;
	}
</style>
