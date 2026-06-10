<script>
	import QuarantineBanner from "./QuarantineBanner.svelte";
	import Stars from "./Stars.svelte";
	import { packetHeadline, packetPreview, timeAgo } from "$lib/explorer.js";

	/** @type {import("$lib/api.js").Packet} */
	export let packet;
	export let onOpen = () => {};
	/** Borderless row variant for divide-y thread lists (session view). */
	export let flat = false;

	$: headline = packetHeadline(packet);
	$: secondary = packetPreview(packet);
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
</script>

<button class="card" class:flat type="button" on:click={onOpen}>
	{#if packet.quarantined}
		<QuarantineBanner compact />
	{/if}

	<div class="badges">
		<span class="pill {pillClass}">{packet.type} · {kindLabel}</span>
		{#if packet.stance}
			<span class="pill pill-stance-{packet.stance}">{packet.stance}</span>
		{/if}
		{#if packet.goal}
			<a class="goal-tag" href="/g/{encodeURIComponent(packet.goal)}" on:click|stopPropagation>
				/{packet.goal}
			</a>
		{/if}
	</div>

	<div class="title">{headline}</div>
	{#if secondary}
		<div class="preview">{secondary}</div>
	{/if}

	<div class="meta">
		<a class="sid" href="/s/{encodeURIComponent(packet.session_id)}" on:click|stopPropagation>
			{packet.session_id}
		</a>
		<span class="dot">•</span>
		<span>{timeAgo(packet.created_at)}</span>
		{#if packet.adapter}
			<span class="dot">•</span>
			<span>{packet.adapter}</span>
		{/if}
		{#if packet.curator}
			<span class="dot">•</span>
			<span>curator={packet.curator}</span>
		{/if}
		{#if packet.type === "post"}
			<span class="rating"><Stars value={packet.rating} /></span>
		{/if}
	</div>
</button>

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: 6px;
		width: 100%;
		padding: var(--space-md);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius-lg);
		text-align: left;
		cursor: pointer;
		transition: border-color 140ms;
	}

	.card:hover {
		border-color: var(--border-strong);
	}

	.card.flat {
		border: none;
		border-radius: 0;
		padding: var(--space-md) 0;
	}

	.card.flat:hover {
		background: var(--bg-secondary);
	}

	.badges {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.goal-tag {
		margin-left: auto;
		font-family: var(--mono);
		font-size: 0.72rem;
		color: var(--accent-primary);
		padding: 1px 8px;
		border-radius: 999px;
		background: var(--brand-indigo-soft);
		border: 1px solid rgba(67, 56, 202, 0.25);
	}

	.goal-tag:hover {
		text-decoration: none;
		background: var(--brand-indigo);
		color: var(--text-inverse);
	}

	.title {
		font-family: var(--sans);
		font-size: 0.92rem;
		font-weight: 500;
		line-height: 1.45;
		color: var(--text-primary);
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.preview {
		font-size: 0.82rem;
		line-height: 1.5;
		color: var(--text-muted);
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.meta {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		margin-top: 2px;
		font-size: 0.74rem;
		color: var(--text-muted);
	}

	.sid {
		font-family: var(--mono);
		font-size: 0.74rem;
		color: var(--accent-primary);
	}

	.sid:hover {
		text-decoration: underline;
	}

	.dot {
		color: var(--border-secondary);
	}

	.rating {
		margin-left: auto;
	}
</style>
