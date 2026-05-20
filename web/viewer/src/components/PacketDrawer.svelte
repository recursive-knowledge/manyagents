<script>
	import { onMount, onDestroy } from "svelte";
	import QuarantineBanner from "./QuarantineBanner.svelte";
	import Stars from "./Stars.svelte";
	import StructuredView from "./StructuredView.svelte";
	import { timeAgo } from "$lib/explorer.js";

	/** @type {import("$lib/api.js").Packet} */
	export let packet;
	export let onClose = () => {};

	let copyState = "";

	function copyInject() {
		// Quarantine invariant (oma.web.md): the "use as context" affordance
		// is disabled when packet.quarantined. The button below is guarded.
		const cmd = `/inject @${packet.id}`;
		navigator.clipboard?.writeText(cmd).then(
			() => {
				copyState = "copied";
				setTimeout(() => (copyState = ""), 1500);
			},
			() => {
				copyState = "failed";
			}
		);
	}

	function handleKey(e) {
		if (e.key === "Escape") onClose();
	}

	onMount(() => {
		document.addEventListener("keydown", handleKey);
		document.body.style.overflow = "hidden";
	});

	onDestroy(() => {
		document.removeEventListener("keydown", handleKey);
		document.body.style.overflow = "";
	});

	$: shortId = packet.id.split("/").pop() ?? packet.id;
	$: parents = Array.isArray(packet.parents) ? packet.parents : [];
</script>

<div
	class="scrim"
	role="dialog"
	aria-modal="true"
	aria-label="packet detail"
	on:click|self={onClose}
>
	<aside class="drawer">
		<header class="head">
			<div class="head-meta">
				<span class="pill pill-{packet.type}">{packet.type}</span>
				{#if packet.kind}<span class="kind">{packet.kind}</span>{/if}
				{#if packet.scope}<span class="kind">{packet.scope}</span>{/if}
				{#if packet.stance}
					<span class="pill pill-stance-{packet.stance}">{packet.stance}</span>
				{/if}
				{#if packet.goal}
					<a class="goal-tag" href="/g/{encodeURIComponent(packet.goal)}">/{packet.goal}</a>
				{/if}
			</div>
			<button class="close" on:click={onClose} aria-label="close">×</button>
		</header>

		{#if packet.quarantined}
			<QuarantineBanner />
		{/if}

		<div class="ids">
			<div class="id-row">
				<span class="id-label">packet</span>
				<code>{packet.id}</code>
			</div>
			<div class="id-row">
				<span class="id-label">session</span>
				<a class="mono" href="/s/{encodeURIComponent(packet.session_id)}">
					{packet.session_id}
				</a>
			</div>
			<div class="id-row">
				<span class="id-label">created</span>
				<code>{packet.created_at ?? "—"}</code>
				<span class="muted"> ({timeAgo(packet.created_at)})</span>
			</div>
			{#if packet.adapter}
				<div class="id-row">
					<span class="id-label">adapter</span>
					<code>{packet.adapter}</code>
				</div>
			{/if}
			{#if packet.curator}
				<div class="id-row">
					<span class="id-label">curator</span>
					<code>{packet.curator}</code>
				</div>
			{/if}
			{#if packet.type === "post"}
				<div class="id-row">
					<span class="id-label">rating</span>
					<Stars value={packet.rating} />
				</div>
			{/if}
		</div>

		<section class="body">
			<StructuredView {packet} />
		</section>

		{#if parents.length > 0}
			<section class="parents">
				<h3>Parents</h3>
				<ul>
					{#each parents as pid}
						{@const [sid, uuid] = pid.split("/")}
						<li>
							<a class="mono" href="/s/{encodeURIComponent(sid)}?p={uuid ?? ""}">
								{pid}
							</a>
						</li>
					{/each}
				</ul>
			</section>
		{/if}

		{#if packet.type === "post" && packet.reply_to}
			<section class="parents">
				<h3>Reply to</h3>
				<a class="mono" href={`/s/${encodeURIComponent(packet.session_id)}?p=${packet.reply_to.split("/").pop()}`}>
					{packet.reply_to}
				</a>
			</section>
		{/if}

		<footer class="actions">
			{#if packet.quarantined}
				<span class="muted">
					reuse disabled — this packet is excluded from "use as context"
				</span>
			{:else}
				<button class="cta" on:click={copyInject}>
					{copyState === "copied" ? "✓ copied" : `copy /inject @${shortId.slice(0, 8)}…`}
				</button>
				<span class="hint">paste into a live <code>oma</code> session</span>
			{/if}
		</footer>
	</aside>
</div>

<style>
	.scrim {
		position: fixed;
		inset: 0;
		background: rgba(15, 23, 42, 0.35);
		display: flex;
		justify-content: flex-end;
		z-index: 1000;
		backdrop-filter: blur(2px);
	}

	.drawer {
		width: min(720px, 100vw);
		height: 100vh;
		overflow-y: auto;
		background: var(--bg-primary);
		border-left: 1px solid var(--border-primary);
		box-shadow: var(--shadow-lg);
		padding: var(--space-lg);
		display: flex;
		flex-direction: column;
		gap: var(--space-md);
	}

	.head {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: var(--space-md);
	}

	.head-meta {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.kind {
		font-family: var(--mono);
		font-size: 0.78rem;
		color: var(--text-muted);
	}

	.goal-tag {
		font-family: var(--mono);
		font-size: 0.82rem;
		color: var(--accent-primary);
		padding: 2px 8px;
		border-radius: 999px;
		background: var(--brand-indigo-soft);
	}

	.close {
		font-size: 1.6rem;
		line-height: 1;
		color: var(--text-muted);
		padding: 2px 8px;
		border-radius: var(--radius);
	}

	.close:hover {
		background: var(--bg-tertiary);
		color: var(--text-primary);
	}

	.pill-post {
		background: var(--type-post-soft);
		color: var(--type-post);
	}
	.pill-distill {
		background: var(--type-distill-soft);
		color: var(--type-distill);
	}
	.pill-raw {
		background: var(--type-raw-soft);
		color: var(--type-raw);
	}

	.pill {
		display: inline-flex;
		align-items: center;
		font-family: var(--mono);
		font-size: 0.72rem;
		font-weight: 600;
		padding: 3px 9px;
		border-radius: 999px;
		text-transform: lowercase;
	}

	.ids {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 12px;
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		background: var(--bg-secondary);
		font-size: 0.85rem;
	}

	.id-row {
		display: flex;
		gap: 8px;
		align-items: baseline;
		flex-wrap: wrap;
	}

	.id-label {
		font-family: var(--mono);
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		min-width: 72px;
	}

	.id-row code {
		font-size: 0.82rem;
	}

	.parents h3 {
		font-family: var(--sans);
		font-size: 0.82rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-muted);
		margin: 0 0 6px;
	}

	.parents ul {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.actions {
		margin-top: auto;
		padding-top: var(--space-md);
		border-top: 1px solid var(--border-primary);
		display: flex;
		gap: var(--space-md);
		align-items: center;
		flex-wrap: wrap;
	}

	.cta {
		padding: 8px 14px;
		background: var(--accent-primary);
		color: #fff;
		border-radius: var(--radius);
		font-family: var(--sans);
		font-size: 0.85rem;
		font-weight: 600;
	}

	.cta:hover {
		background: var(--brand-indigo-dark);
	}

	.hint {
		font-size: 0.8rem;
		color: var(--text-muted);
	}

	.hint code {
		font-size: 0.78rem;
		background: var(--bg-tertiary);
		padding: 0 4px;
		border-radius: 3px;
	}

	.muted {
		color: var(--text-muted);
		font-size: 0.85rem;
	}

	@media (max-width: 720px) {
		.drawer {
			padding: var(--space-md);
		}
	}
</style>
