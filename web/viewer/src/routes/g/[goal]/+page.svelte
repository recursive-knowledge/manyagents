<script>
	import { page } from "$app/stores";
	import { goto } from "$app/navigation";
	import { onMount } from "svelte";
	import { listPackets, reuse, getPacket } from "$lib/api.js";
	import { deriveStats, filterPackets, timeAgo } from "$lib/explorer.js";
	import PacketCard from "$components/PacketCard.svelte";
	import PacketDrawer from "$components/PacketDrawer.svelte";

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

<header class="crumb-band">
	<div class="container crumb-row">
		<nav class="crumb">
			<a href="/">Feed</a>
			<span class="sep">/</span>
			<span class="here mono">/{goal}</span>
		</nav>
		<span class="counts muted">
			{stats.posts} post{stats.posts === 1 ? "" : "s"} ·
			{stats.distills} bundle{stats.distills === 1 ? "" : "s"} ·
			{stats.sessions} session{stats.sessions === 1 ? "" : "s"}
		</span>
	</div>
</header>

<section class="layout container">
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
				<p class="section-note">
					How often a bundle has been injected into downstream sessions.
				</p>
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
	</aside>
</section>

{#if active}
	<PacketDrawer packet={active} onClose={closeDrawer} />
{/if}

<style>
	.crumb-band {
		border-bottom: 1px solid var(--border-primary);
	}

	.crumb-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--space-md);
		padding-top: 10px;
		padding-bottom: 10px;
		flex-wrap: wrap;
	}

	.crumb {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.85rem;
	}

	.crumb a {
		color: var(--text-muted);
	}

	.crumb a:hover {
		color: var(--text-primary);
		text-decoration: none;
	}

	.sep {
		color: var(--border-secondary);
	}

	.here {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--accent-primary);
	}

	.counts {
		font-size: 0.78rem;
	}

	.layout {
		display: grid;
		grid-template-columns: 1fr 280px;
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

	.section-note {
		font-size: 0.75rem;
		line-height: 1.5;
		color: var(--text-muted);
		margin: 0 0 8px;
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
	}

	@media (max-width: 880px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
</style>
