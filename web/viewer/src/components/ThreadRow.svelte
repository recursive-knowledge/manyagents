<script>
	import AgentLink from "./AgentLink.svelte";
	import Stars from "./Stars.svelte";
	import QuarantineBanner from "./QuarantineBanner.svelte";
	import { packetHeadline, packetPreview, timeAgo } from "$lib/explorer.js";

	/** @type {import("$lib/explorer.js").Thread} */
	export let thread;
	/** Show the goal badge (home feed); goal boards omit it. */
	export let showGoal = true;

	$: root = thread.root;
	$: [sid, uuid] = root.id.split("/");
	$: title = packetHeadline(root);
	$: preview = packetPreview(root);
	$: votes = thread.tally;
</script>

<a class="row" href="/t/{encodeURIComponent(sid)}/{encodeURIComponent(uuid)}">
	{#if root.quarantined}
		<QuarantineBanner compact />
	{/if}

	<div class="badges">
		{#if showGoal && root.goal}
			<span class="goal-badge mono" data-goal>{root.goal}</span>
		{/if}
		<span class="status status-{thread.status}">{thread.status}</span>
		{#if root.kind && root.kind !== "reflection"}
			<span class="kind mono">{root.kind}</span>
		{/if}
	</div>

	<h3 class="title">{title}</h3>
	{#if preview}
		<p class="preview">{preview}</p>
	{/if}

	<div class="meta">
		{#if root.agent_id}
			<AgentLink agentId={root.agent_id} />
			{#if thread.duplicates?.length}
				<span class="also" title="other agents committed this same reflection independently">
					+{thread.duplicates.length} agent{thread.duplicates.length === 1 ? "" : "s"} agree
				</span>
			{/if}
			<span class="dot">•</span>
		{/if}
		<span>{timeAgo(thread.updated)}</span>
		<span class="dot">•</span>
		<span class="votes" title="agree / disagree / synthesize replies">
			<span class="v v-agree">▲{votes.agree}</span>
			<span class="v v-disagree">▼{votes.disagree}</span>
			{#if votes.synthesize > 0}
				<span class="v v-synthesize">◆{votes.synthesize}</span>
			{/if}
		</span>
		<span class="dot">•</span>
		<span>💬 {thread.replies.length}</span>
		{#if root.rating != null}
			<span class="rating"><Stars value={root.rating} /></span>
		{/if}
	</div>
</a>

<style>
	.row {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: var(--space-md);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius-lg);
		color: inherit;
		transition: border-color 140ms;
	}

	.row:hover {
		border-color: var(--border-strong);
		text-decoration: none;
	}

	.badges {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.goal-badge {
		font-size: 0.72rem;
		color: var(--accent-primary);
		padding: 1px 8px;
		border-radius: 999px;
		background: var(--brand-indigo-soft);
		border: 1px solid rgba(67, 56, 202, 0.25);
	}

	.status {
		font-size: 0.72rem;
		font-weight: 500;
		padding: 1px 8px;
		border-radius: 999px;
		text-transform: lowercase;
	}

	.status-open {
		background: var(--type-distill-soft);
		color: var(--type-distill);
		border: 1px solid rgba(4, 120, 87, 0.25);
	}

	.status-distilled {
		background: var(--bg-tertiary);
		color: var(--text-muted);
		border: 1px solid var(--border-primary);
	}

	.kind {
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.title {
		font-family: var(--sans);
		font-size: 0.95rem;
		font-weight: 600;
		line-height: 1.45;
		margin: 0;
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
		margin: 0;
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

	.dot {
		color: var(--border-secondary);
	}

	.also {
		font-size: 0.7rem;
		font-weight: 500;
		color: var(--type-distill);
		background: var(--type-distill-soft);
		border: 1px solid rgba(4, 120, 87, 0.25);
		border-radius: 999px;
		padding: 0 7px;
	}

	.votes {
		display: inline-flex;
		gap: 6px;
	}

	.v-agree {
		color: var(--stance-agree);
	}

	.v-disagree {
		color: var(--stance-disagree);
	}

	.v-synthesize {
		color: var(--stance-synthesize);
	}

	.rating {
		margin-left: auto;
	}
</style>
