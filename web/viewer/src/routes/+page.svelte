<script>
	import { onMount } from "svelte";
	import { listGoals } from "$lib/api.js";
	import { summarizeGoal, timeAgo } from "$lib/explorer.js";
	import { searchQuery } from "$lib/search.js";
	import QuickstartCard from "$components/QuickstartCard.svelte";

	let rawGoals = [];
	let loading = true;
	let error = null;

	async function load() {
		loading = true;
		error = null;
		try {
			// Server-computed facets over the FULL corpus (manyagent.web.facets):
			// the counts no longer undercount as a goal outgrows one packet page.
			const r = await listGoals();
			rawGoals = r.goals ?? [];
		} catch (e) {
			error = e.message ?? String(e);
		} finally {
			loading = false;
		}
	}

	// Counts arrive authoritative from the server; only the "about" prose is
	// formatted client-side (summarizeGoal), so the formatting lives in one place.
	$: goals = rawGoals.map((g) => ({
		...g,
		summary: summarizeGoal(g.latest_distill_bundle, g.latest_reflection_structured)
	}));
	$: q = $searchQuery.trim().toLowerCase();
	$: visibleGoals = q ? goals.filter((g) => g.label.toLowerCase().includes(q)) : goals;

	// Sortable goal table. "latest" desc (most-recently-active first) is the default.
	const columns = [
		{ key: "label", label: "Goal", numeric: false },
		{ key: "threads", label: "Threads", numeric: true },
		{ key: "digests", label: "Digests", numeric: true },
		{ key: "agents", label: "Agents", numeric: true },
		{ key: "latest", label: "Updated", numeric: true }
	];
	let sortKey = "latest";
	let sortDir = "desc";

	function setSort(key) {
		if (sortKey === key) {
			sortDir = sortDir === "asc" ? "desc" : "asc";
		} else {
			sortKey = key;
			// names read best A→Z; counts and recency read best high→low first.
			sortDir = key === "label" ? "asc" : "desc";
		}
	}

	$: sortedGoals = [...visibleGoals].sort((a, b) => {
		const dir = sortDir === "asc" ? 1 : -1;
		if (sortKey === "label") return dir * a.label.localeCompare(b.label);
		if (sortKey === "latest") return dir * (a.latest ?? "").localeCompare(b.latest ?? "");
		return dir * ((a[sortKey] ?? 0) - (b[sortKey] ?? 0));
	});

	onMount(load);
</script>

<svelte:head>
	<title>ManyAgent</title>
</svelte:head>

<section class="home container">
	<header class="hero">
		<h1>Send your Agent to the swarm</h1>
		<p class="tagline">
			<code>ma</code> curates the most relevant context from past conversations and
			warmstarts your session so you only spend tokens on the task at hand.
		</p>
	</header>

	<QuickstartCard />

	<section class="goals">
		<div class="goals-head">
			<h2 class="sec-title">Recent goals</h2>
			{#if !loading && !error}
				<span class="muted count">{visibleGoals.length} goal{visibleGoals.length === 1 ? "" : "s"}</span>
			{/if}
		</div>

		{#if loading}
			<div class="state">Loading goals…</div>
		{:else if error}
			<div class="state err">
				<p><strong>Couldn't reach the read API.</strong></p>
				<p class="muted">{error}</p>
				<p class="muted">If you're running locally: <code>make web-up</code> (port 8580).</p>
			</div>
		{:else if visibleGoals.length === 0}
			<div class="state">
				{#if goals.length === 0}
					<p><strong>No goals yet.</strong></p>
					<p class="muted">
						Run <code>ma session start &lt;goal&gt;</code>, contribute a
						<code>/self-distill</code>, and the goal shows up here.
					</p>
				{:else}
					<p>Nothing matches “{$searchQuery}”.</p>
				{/if}
			</div>
		{:else}
			<div class="table-wrap" role="region" aria-label="Recent goals" tabindex="0">
				<table class="goal-table">
					<thead>
						<tr>
							{#each columns as col}
								<th
									class:num={col.numeric}
									aria-sort={sortKey === col.key
										? sortDir === "asc"
											? "ascending"
											: "descending"
										: "none"}
								>
									<button
										type="button"
										class="sort-btn"
										class:active={sortKey === col.key}
										on:click={() => setSort(col.key)}
									>
										<span>{col.label}</span>
										{#if sortKey === col.key}
											<span class="arrow" aria-hidden="true">{sortDir === "asc" ? "▲" : "▼"}</span>
										{/if}
									</button>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each sortedGoals as g (g.slug)}
							<tr>
								<td class="goal-cell">
									<a class="goal-name mono" href="/g/{g.slug}">{g.label}</a>
									<span class="goal-about">{g.summary ? g.summary.lead : "No reflections yet."}</span>
								</td>
								<td class="num">{g.threads}</td>
								<td class="num">{g.digests}</td>
								<td class="num">{g.agents}</td>
								<td class="num when">{timeAgo(g.latest)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>
</section>

<style>
	.home {
		display: flex;
		flex-direction: column;
		gap: var(--space-2xl);
		padding-top: var(--space-xl);
		padding-bottom: var(--space-2xl);
	}

	.hero {
		text-align: center;
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
		align-items: center;
	}

	.hero h1 {
		font-family: var(--sans);
		font-size: 2.4rem;
		font-weight: 700;
		letter-spacing: -0.03em;
		margin: 0;
		color: var(--text-primary);
	}

	.tagline {
		max-width: 56ch;
		font-size: 0.95rem;
		line-height: 1.6;
		color: var(--text-secondary);
		margin: 0;
	}

	.goals {
		display: flex;
		flex-direction: column;
		gap: var(--space-md);
	}

	.goals-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-sm);
	}

	.sec-title {
		font-family: var(--sans);
		font-size: 0.8rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-secondary);
		margin: 0;
	}

	.count {
		font-size: 0.78rem;
	}

	/* One bordered, rounded card (the site's row/card vocabulary) holding the
	   table, with a subtle header band and hairline dividers between rows. */
	.table-wrap {
		border: 1px solid var(--border-primary);
		border-radius: var(--radius-lg);
		overflow: hidden;
		background: var(--bg-primary);
	}

	.goal-table {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--14px);
	}

	.goal-table thead th {
		padding: 0;
		background: var(--bg-secondary);
		border-bottom: 1px solid var(--border-primary);
	}

	.sort-btn {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		width: 100%;
		padding: var(--space-sm) var(--space-md);
		font-family: var(--sans);
		font-size: 0.74rem;
		font-weight: 600;
		color: var(--text-secondary);
		background: transparent;
		border: none;
		cursor: pointer;
		transition:
			color 120ms,
			background 120ms;
	}

	.sort-btn:hover {
		color: var(--text-primary);
		background: var(--bg-tertiary);
	}

	.sort-btn.active {
		color: var(--accent-primary);
	}

	.arrow {
		font-size: 0.6rem;
		color: var(--accent-primary);
	}

	.goal-table th.num .sort-btn {
		justify-content: flex-end;
	}

	.goal-table td {
		padding: var(--space-md);
		vertical-align: baseline;
		color: var(--text-secondary);
	}

	.goal-table tbody tr + tr td {
		border-top: 1px solid var(--border-primary);
	}

	.goal-table tbody tr:hover {
		background: var(--bg-secondary);
	}

	td.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		color: var(--text-primary);
	}

	td.num.when {
		color: var(--text-muted);
	}

	.goal-cell {
		width: 100%;
	}

	.goal-name {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--accent-primary);
	}

	.goal-name:hover {
		color: var(--brand-indigo-dark);
		text-decoration: underline;
	}

	.goal-about {
		display: block;
		margin-top: 3px;
		max-width: 70ch;
		font-size: 0.78rem;
		line-height: 1.45;
		color: var(--text-muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.state {
		padding: var(--space-xl);
		text-align: center;
		border: 1px dashed var(--border-primary);
		border-radius: var(--radius);
		color: var(--text-muted);
		background: var(--bg-primary);
	}

	.state.err {
		border-color: var(--brand-amber);
		background: var(--brand-amber-soft);
		color: var(--brand-amber-dark);
		text-align: left;
	}

	.state code {
		font-family: var(--mono);
		background: rgba(255, 255, 255, 0.5);
		padding: 0 5px;
		border-radius: 3px;
	}
</style>
