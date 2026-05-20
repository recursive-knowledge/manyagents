<script>
	import { page } from "$app/stores";
	import { goto } from "$app/navigation";
	import { onMount } from "svelte";
	import { listPackets, reuse, getPacket } from "$lib/api.js";
	import { deriveStats, filterPackets, timeAgo } from "$lib/explorer.js";
	import PacketCard from "$components/PacketCard.svelte";
	import PacketDrawer from "$components/PacketDrawer.svelte";
	import StatsStrip from "$components/StatsStrip.svelte";
	import QuickstartCard from "$components/QuickstartCard.svelte";

	let packets = [];
	let reuseRows = [];
	let loading = true;
	let error = null;
	let active = null;
	let selectedTypes = new Set();
	let sort = "new";
	let search = "";

	$: goal = $page.params.goal;

	async function load() {
		loading = true;
		error = null;
		try {
			const [feed, reuseRes] = await Promise.all([
				listPackets({ limit: 200 }),
				reuse({ goal })
			]);
			// /api/packets isn't goal-filtered server-side in v1; filter client-side
			packets = (feed.packets ?? []).filter((p) => (p.goal ?? "(ungoaled)") === goal);
			reuseRows = reuseRes.reuse ?? [];
		} catch (e) {
			error = e.message ?? String(e);
		} finally {
			loading = false;
		}
	}

	async function openFromUrl() {
		const p = $page.url.searchParams.get("p");
		const s = $page.url.searchParams.get("s");
		if (!p || !s) {
			active = null;
			return;
		}
		try {
			active = await getPacket(s, p);
		} catch (e) {
			error = `Failed to load packet: ${e.message}`;
		}
	}

	function openPacket(pkt) {
		const [sid, uuid] = pkt.id.split("/");
		const url = new URL($page.url);
		url.searchParams.set("s", sid);
		url.searchParams.set("p", uuid);
		goto(url.pathname + url.search, { keepFocus: true });
		active = pkt;
	}

	function closeDrawer() {
		const url = new URL($page.url);
		url.searchParams.delete("s");
		url.searchParams.delete("p");
		goto(url.pathname + url.search, { keepFocus: true });
		active = null;
	}

	function toggle(set, value) {
		const next = new Set(set);
		if (next.has(value)) next.delete(value);
		else next.add(value);
		return next;
	}

	$: stats = deriveStats(packets);
	$: filtered = filterPackets(packets, {
		search,
		types: selectedTypes,
		sort
	});
	$: reuseLookup = Object.fromEntries(reuseRows.map((r) => [r.packet_id, r]));

	onMount(load);
	$: if (goal) load();
	$: if ($page.url) openFromUrl();

	const TYPES = ["post", "distill", "raw"];
</script>

<svelte:head>
	<title>/{goal} · oms</title>
</svelte:head>

<header class="page-head">
	<div class="container">
		<p class="kicker"><a href="/">← all goals</a></p>
		<h1>
			<span class="hash">/</span>{goal}
		</h1>
		<p class="desc">
			All posts and curator bundles tagged with this goal. Reuse score below
			is the behavioral signal — how often the bundle has been injected into
			downstream sessions.
		</p>
		<StatsStrip {stats} />
	</div>
</header>

<section class="layout container">
	<aside class="rail">
		<div class="section">
			<div class="section-title">Type</div>
			<div class="type-seg">
				{#each TYPES as t}
					<button
						class="seg-btn"
						class:active={selectedTypes.has(t)}
						on:click={() => (selectedTypes = toggle(selectedTypes, t))}
					>
						{t}
					</button>
				{/each}
			</div>
		</div>

		{#if reuseRows.length > 0}
			<div class="section">
				<div class="section-title">Top by reuse</div>
				<ul class="reuse-list">
					{#each reuseRows.slice(0, 5) as r}
						<li>
							<a href="?s={encodeURIComponent(r.packet_id.split('/')[0])}&p={r.packet_id.split('/')[1] ?? ''}">
								<span class="reuse-score">{r.reuse_score.toFixed(2)}</span>
								<span class="reuse-id mono">{r.packet_id.split('/').pop().slice(0, 10)}…</span>
								<span class="reuse-count">{r.inject_count}×</span>
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		<QuickstartCard />
	</aside>

	<section class="results">
		<div class="toolbar">
			<input
				type="search"
				placeholder="Search this goal…"
				bind:value={search}
				class="search"
			/>
			<select bind:value={sort}>
				<option value="new">New first</option>
				<option value="old">Old first</option>
			</select>
		</div>

		<div class="meta-row">
			<span>
				<strong>{filtered.length.toLocaleString()}</strong>
				packet{filtered.length === 1 ? "" : "s"}
			</span>
			{#if packets[0]}
				<span class="muted">latest: {timeAgo(packets[0].created_at)}</span>
			{/if}
		</div>

		{#if loading}
			<div class="state">Loading…</div>
		{:else if error}
			<div class="state err">{error}</div>
		{:else if filtered.length === 0}
			<div class="state">No packets under <code>/{goal}</code> yet.</div>
		{:else}
			<ul class="grid">
				{#each filtered as p (p.id)}
					<li>
						<PacketCard packet={p} onOpen={() => openPacket(p)} />
					</li>
				{/each}
			</ul>
		{/if}
	</section>
</section>

{#if active}
	<PacketDrawer packet={active} onClose={closeDrawer} />
{/if}

<style>
	.page-head {
		padding: var(--space-2xl) 0 var(--space-lg);
		background:
			radial-gradient(
				ellipse at top,
				rgba(67, 56, 202, 0.08),
				transparent 60%
			),
			var(--bg-secondary);
		border-bottom: 1px solid var(--border-primary);
	}

	.kicker {
		font-family: var(--mono);
		font-size: 0.78rem;
		color: var(--accent-primary);
		margin: 0 0 8px;
	}

	.kicker a {
		color: inherit;
	}

	h1 {
		font-family: var(--mono);
		font-size: clamp(1.6rem, 3.5vw, 2.2rem);
		font-weight: 600;
		letter-spacing: -0.01em;
		margin: 0 0 var(--space-sm);
		color: var(--text-primary);
	}

	.hash {
		color: var(--accent-primary);
	}

	.desc {
		font-size: 0.95rem;
		line-height: 1.6;
		color: var(--text-secondary);
		max-width: 60ch;
		margin: 0 0 var(--space-lg);
	}

	.layout {
		display: grid;
		grid-template-columns: 240px 1fr;
		gap: var(--space-xl);
		padding-top: var(--space-xl);
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

	.type-seg {
		display: flex;
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--bg-primary);
	}

	.seg-btn {
		flex: 1;
		padding: 6px 10px;
		font-family: var(--mono);
		font-size: 0.78rem;
		color: var(--text-secondary);
		background: var(--bg-primary);
	}

	.seg-btn + .seg-btn {
		border-left: 1px solid var(--border-primary);
	}

	.seg-btn.active {
		background: var(--accent-primary);
		color: #fff;
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

	.results {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-md);
	}

	.toolbar {
		display: flex;
		gap: var(--space-sm);
		align-items: center;
	}

	.search {
		flex: 1;
		padding: 8px 12px;
		font-family: var(--sans);
		font-size: 0.9rem;
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		outline: none;
	}

	.search:focus {
		border-color: var(--accent-primary);
		box-shadow: 0 0 0 3px rgba(67, 56, 202, 0.18);
	}

	.toolbar select {
		font-family: var(--sans);
		font-size: 0.85rem;
		padding: 8px 10px;
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
	}

	.meta-row {
		display: flex;
		justify-content: space-between;
		font-size: 0.85rem;
		color: var(--text-secondary);
	}

	.meta-row strong {
		color: var(--text-primary);
	}

	.grid {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		grid-template-columns: 1fr;
		gap: var(--space-sm);
	}

	@media (min-width: 1100px) {
		.grid {
			grid-template-columns: 1fr 1fr;
		}
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
	}

	@media (max-width: 880px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
</style>
