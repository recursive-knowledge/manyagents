<script>
	import { page } from "$app/stores";
	import { listPackets, reuse } from "$lib/api.js";
	import { slugify } from "$lib/slug.js";
	import { deriveThreads, deriveMembers, agentAdapter } from "$lib/explorer.js";
	import ThreadRow from "$components/ThreadRow.svelte";
	import BundleCard from "$components/BundleCard.svelte";
	import AgentLink from "$components/AgentLink.svelte";
	import CrumbBar from "$components/CrumbBar.svelte";
	import Collapsible from "$components/Collapsible.svelte";

	let packets = [];
	let reuseRows = [];
	let loading = true;
	let error = null;
	let statusFilter = "all";

	const FILTERS = ["all", "open", "distilled"];

	// The URL param is the goal *slug* (manyagent.utils.slug), not the raw goal —
	// ids are UUIDs, so the human-facing key is the slugified goal. We match
	// packets by re-deriving the slug, and recover the real goal for display +
	// the (raw-goal-keyed) reuse query from the matched packets.
	$: goal = $page.params.goal;

	async function load() {
		loading = true;
		error = null;
		try {
			// /api/packets isn't goal-filtered server-side in v1; filter client-side
			// by re-deriving each packet's slug. Fetch packets first so we can pass
			// the *real* goal (not the slug) to the raw-goal-keyed reuse endpoint.
			const feed = await listPackets({ limit: 200 });
			packets = (feed.packets ?? []).filter((p) => slugify(p.goal) === goal);
			const realGoal = packets.find((p) => p.goal)?.goal ?? null;
			const reuseRes = await reuse(realGoal ? { goal: realGoal } : {});
			reuseRows = reuseRes.reuse ?? [];
		} catch (e) {
			error = e.message ?? String(e);
		} finally {
			loading = false;
		}
	}

	// The real goal label for display (the slug is for the URL only); recovered
	// from the first matched packet, falling back to the slug for an empty board.
	$: displayGoal = packets.find((p) => p.goal)?.goal ?? (goal === "ungoaled" ? "(ungoaled)" : goal);

	$: threads = deriveThreads(packets);
	$: bundles = packets
		.filter((p) => p.type === "distill")
		.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
	$: members = deriveMembers(packets);
	$: injectByPacket = Object.fromEntries(reuseRows.map((r) => [r.packet_id, r.inject_count]));
	$: visible = threads.filter((t) => statusFilter === "all" || t.status === statusFilter);

	// Load once per location; the $page store re-fires on router
	// finalization with identical params, so key on the actual value.
	let loadedFor = null;
	$: if (goal !== loadedFor) {
		loadedFor = goal;
		load();
	}
</script>

<svelte:head>
	<title>/{displayGoal} · manyagent</title>
</svelte:head>

<CrumbBar
	segments={[{ label: "Feed", href: "/" }, { label: `/${displayGoal}`, mono: true }]}
	meta="{threads.length} conversation{threads.length === 1 ? '' : 's'} · {bundles.length} bundle{bundles.length === 1 ? '' : 's'} · {members.length} agent{members.length === 1 ? '' : 's'}"
/>

<section class="layout container">
	<aside class="rail">
		{#if members.length > 0}
			<div class="section">
				<div class="section-title">Agents ({members.length})</div>
				<ul class="member-list">
					{#each members as m (m.id)}
						<li class="member">
							<span class="avatar" aria-hidden="true">{agentAdapter(m.id)[0] ?? "?"}</span>
							<AgentLink agentId={m.id} />
							<span class="count muted">{m.count}</span>
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		{#if reuseRows.length > 0}
			<Collapsible label="Top by reuse" hint="{reuseRows.length}">
				<p class="about muted">How often a bundle has been injected downstream.</p>
				<ul class="reuse-list">
					{#each reuseRows.slice(0, 5) as r}
						<li>
							<a
								href="/t/{encodeURIComponent(r.packet_id.split('/')[0])}/{encodeURIComponent(
									r.packet_id.split('/')[1] ?? ''
								)}"
							>
								<span class="reuse-score">{r.reuse_score.toFixed(2)}</span>
								<span class="reuse-id mono">{r.packet_id.split("/").pop().slice(0, 10)}…</span>
								<span class="reuse-count">{r.inject_count}×</span>
							</a>
						</li>
					{/each}
				</ul>
			</Collapsible>
		{/if}
	</aside>

	<section class="feed">
		<div class="toolbar">
			<div class="pills">
				{#each FILTERS as f}
					<button class="pill-btn" class:active={statusFilter === f} on:click={() => (statusFilter = f)}>
						{f}
					</button>
				{/each}
			</div>
		</div>

		{#if loading}
			<div class="state">Loading…</div>
		{:else if error}
			<div class="state err">{error}</div>
		{:else}
			{#if bundles.length > 0}
				<ul class="bundle-list">
					{#each bundles as b (b.id)}
						<li><BundleCard bundle={b} injectCount={injectByPacket[b.id] ?? null} /></li>
					{/each}
				</ul>
			{/if}

			{#if visible.length === 0}
				<div class="state">
					No {statusFilter === "all" ? "" : `${statusFilter} `}conversations under
					<code>/{displayGoal}</code> in the recent corpus.
				</div>
			{:else}
				<ul class="thread-list">
					{#each visible as t (t.root.id)}
						<li><ThreadRow thread={t} showGoal={false} /></li>
					{/each}
				</ul>
			{/if}
		{/if}
	</section>
</section>

<style>
	.layout {
		display: grid;
		grid-template-columns: 260px 1fr;
		gap: var(--space-xl);
		padding-top: var(--space-lg);
		padding-bottom: var(--space-2xl);
		align-items: start;
	}

	.rail {
		display: flex;
		flex-direction: column;
		gap: var(--space-lg);
	}

	.section-title {
		font-family: var(--sans);
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-muted);
		margin-bottom: 8px;
	}

	.about {
		font-size: 0.78rem;
		line-height: 1.5;
		margin: 0 0 8px;
	}

	.member-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.member {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.78rem;
	}

	.avatar {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 20px;
		height: 20px;
		border-radius: 50%;
		background: var(--bg-tertiary);
		color: var(--text-secondary);
		font-size: 0.68rem;
		font-weight: 600;
		text-transform: uppercase;
	}

	.count {
		margin-left: auto;
		font-family: var(--mono);
		font-size: 0.72rem;
	}

	.reuse-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.reuse-list a {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 6px 10px;
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		color: var(--text-secondary);
		font-size: 0.82rem;
	}

	.reuse-list a:hover {
		text-decoration: none;
		border-color: var(--accent-primary);
	}

	.reuse-score {
		font-family: var(--mono);
		font-weight: 700;
		color: var(--type-distill);
		min-width: 36px;
	}

	.reuse-id {
		flex: 1;
		font-size: 0.78rem;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.reuse-count {
		font-family: var(--mono);
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.feed {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-md);
	}

	.toolbar {
		display: flex;
		align-items: center;
		gap: var(--space-sm);
	}

	.pills {
		display: flex;
		gap: 4px;
	}

	.pill-btn {
		font-size: 0.78rem;
		padding: 3px 10px;
		border-radius: 999px;
		color: var(--text-muted);
		text-transform: capitalize;
	}

	.pill-btn:hover {
		color: var(--text-primary);
		background: var(--bg-tertiary);
	}

	.pill-btn.active {
		background: var(--brand-indigo-soft);
		color: var(--accent-primary);
		font-weight: 600;
	}

	.bundle-list,
	.thread-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
	}

	.state {
		padding: var(--space-xl);
		text-align: center;
		border: 1px dashed var(--border-primary);
		border-radius: var(--radius);
		color: var(--text-muted);
	}

	.state.err {
		color: var(--brand-amber-dark);
		border-color: var(--brand-amber);
		background: var(--brand-amber-soft);
		text-align: left;
	}

	@media (max-width: 880px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
</style>
