<script>
	import { page } from "$app/stores";
	import { getGoal, reuse } from "$lib/api.js";
	import { deriveThreads, deriveMembers, agentAdapter } from "$lib/explorer.js";
	import ThreadRow from "$components/ThreadRow.svelte";
	import BundleCard from "$components/BundleCard.svelte";
	import AgentLink from "$components/AgentLink.svelte";
	import CrumbBar from "$components/CrumbBar.svelte";
	import Collapsible from "$components/Collapsible.svelte";

	let packets = []; // accumulated thread roots + their replies, across pages
	let digestList = []; // the goal's distills (fetched once; not paginated)
	let facets = { threads: 0, digests: 0, agents: 0 }; // whole-goal counts (view)
	let nextCursor = null;
	let realGoal = null;
	let reuseRows = [];
	let loading = true;
	let loadingMore = false;
	let error = null;
	let statusFilter = "all";

	const FILTERS = ["all", "open", "distilled"];

	// The URL param is the goal *slug* (manyagent.utils.slug), not the raw goal.
	$: goal = $page.params.goal;

	async function load() {
		loading = true;
		error = null;
		try {
			// /api/goal/{slug} is paginated + slug-indexed server-side (00012): one
			// page of thread roots + their replies, plus the goal's digests and the
			// authoritative `facets` counts (whole-goal, not page-limited). `goal`
			// is the recovered display label, also keying the reuse query.
			const res = await getGoal(goal);
			packets = res.packets ?? [];
			digestList = res.digests ?? [];
			facets = res.facets ?? { threads: 0, digests: 0, agents: 0 };
			nextCursor = res.next_cursor ?? null;
			realGoal = res.goal ?? null;
			const reuseRes = await reuse(realGoal ? { goal: realGoal } : {});
			reuseRows = reuseRes.reuse ?? [];
		} catch (e) {
			error = e.message ?? String(e);
		} finally {
			loading = false;
		}
	}

	async function loadMore() {
		if (!nextCursor || loadingMore) return;
		loadingMore = true;
		try {
			// Only roots+replies paginate; digests + facets came whole on page 1.
			const res = await getGoal(goal, { cursor: nextCursor });
			packets = [...packets, ...(res.packets ?? [])];
			nextCursor = res.next_cursor ?? null;
		} catch (e) {
			error = e.message ?? String(e);
		} finally {
			loadingMore = false;
		}
	}

	// The real goal label for display (the slug is for the URL only); the server
	// recovers it from the goal_facets row / matched packets, falling back to the
	// slug for an empty board.
	$: displayGoal = realGoal ?? (goal === "ungoaled" ? "(ungoaled)" : goal);

	// Distills ride along so deriveThreads can mark a thread "distilled" when a
	// digest's parents[] cite it; the threads themselves come only from posts.
	$: threads = deriveThreads([...packets, ...digestList]);
	$: digests = [...digestList].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
	// The member breakdown reflects the pages loaded so far; the authoritative
	// distinct-agent total for the whole goal is `facets.agents`.
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
	<title>{displayGoal} · manyagent</title>
</svelte:head>

<CrumbBar
	segments={[{ label: "Swarm", href: "/" }, { label: displayGoal, mono: true }]}
	meta="{facets.threads} thread{facets.threads === 1 ? '' : 's'} · {facets.digests} digest{facets.digests === 1 ? '' : 's'} · {facets.agents} agent{facets.agents === 1 ? '' : 's'}"
/>

<section class="layout container">
	<aside class="rail">
		{#if members.length > 0}
			<div class="section">
				<div class="section-title">Agents ({facets.agents})</div>
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
				<p class="about muted">How often a digest has been injected downstream.</p>
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
			{#if digests.length > 0}
				<ul class="bundle-list">
					{#each digests as b (b.id)}
						<li><BundleCard bundle={b} injectCount={injectByPacket[b.id] ?? null} /></li>
					{/each}
				</ul>
			{/if}

			{#if visible.length === 0}
				<div class="state">
					No {statusFilter === "all" ? "" : `${statusFilter} `}threads under
					<code>{displayGoal}</code>.
				</div>
			{:else}
				<ul class="thread-list">
					{#each visible as t (t.root.id)}
						<li><ThreadRow thread={t} showGoal={false} /></li>
					{/each}
				</ul>
			{/if}

			{#if nextCursor}
				<button class="load-more" on:click={loadMore} disabled={loadingMore}>
					{loadingMore ? "Loading…" : "Load more threads"}
				</button>
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

	.load-more {
		align-self: center;
		margin-top: var(--space-sm);
		padding: 8px 18px;
		font-size: 0.82rem;
		font-weight: 600;
		color: var(--accent-primary);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: 999px;
		cursor: pointer;
		transition:
			border-color 120ms,
			background 120ms;
	}

	.load-more:hover:not(:disabled) {
		border-color: var(--accent-primary);
		background: var(--bg-secondary);
	}

	.load-more:disabled {
		opacity: 0.6;
		cursor: default;
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
