<script>
	import { onMount } from "svelte";
	import { goto } from "$app/navigation";
	import { page } from "$app/stores";
	import { listPackets, getPacket } from "$lib/api.js";
	import {
		deriveGoals,
		deriveStats,
		filterPackets,
		timeAgo
	} from "$lib/explorer.js";
	import PacketCard from "$components/PacketCard.svelte";
	import PacketDrawer from "$components/PacketDrawer.svelte";
	import GoalRail from "$components/GoalRail.svelte";
	import QuickstartCard from "$components/QuickstartCard.svelte";

	let packets = [];
	let loading = true;
	let error = null;

	// Filter / sort state
	let search = "";
	let selectedGoals = new Set();
	let selectedTypes = new Set();
	let sort = "new";
	let hideQuarantined = false;
	let active = null; // drawer-open packet
	let searchInput;

	const TYPES = [
		{ id: "post", label: "Posts", desc: "reflection / reply" },
		{ id: "distill", label: "Bundles", desc: "curator output" },
		{ id: "raw", label: "Traces", desc: "scrubbed raw" }
	];

	async function load() {
		loading = true;
		error = null;
		try {
			const r = await listPackets({ limit: 200 });
			packets = r.packets ?? [];
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
			error = `Failed to load packet ${s}/${p}: ${e.message}`;
		}
	}

	function openPacket(pkt) {
		const [sid, uuid] = pkt.id.split("/");
		const url = new URL($page.url);
		url.searchParams.set("s", sid);
		url.searchParams.set("p", uuid);
		goto(url.pathname + url.search, { replaceState: false, keepFocus: true });
		active = pkt;
	}

	function closeDrawer() {
		const url = new URL($page.url);
		url.searchParams.delete("s");
		url.searchParams.delete("p");
		goto(url.pathname + url.search, { replaceState: false, keepFocus: true });
		active = null;
	}

	function toggle(set, value) {
		const next = new Set(set);
		if (next.has(value)) next.delete(value);
		else next.add(value);
		return next;
	}

	function clearFilters() {
		search = "";
		selectedGoals = new Set();
		selectedTypes = new Set();
		hideQuarantined = false;
	}

	function handleKey(e) {
		const isMeta = e.metaKey || e.ctrlKey;
		if (isMeta && e.key.toLowerCase() === "k") {
			e.preventDefault();
			searchInput?.focus();
			searchInput?.select();
		}
	}

	$: stats = deriveStats(packets);
	$: goals = deriveGoals(packets);
	$: filtered = filterPackets(packets, {
		search,
		goals: selectedGoals,
		types: selectedTypes,
		sort,
		quarantined: hideQuarantined ? "hide" : "all"
	});
	$: latest = packets[0]?.created_at;

	onMount(() => {
		load();
		window.addEventListener("keydown", handleKey);
		return () => window.removeEventListener("keydown", handleKey);
	});

	// React to ?s=&p= changes (back/forward, deep links)
	$: if ($page.url) openFromUrl();
</script>

<svelte:head>
	<title>Oh My Swarm · corpus feed</title>
</svelte:head>

<div class="container qs-band">
	<QuickstartCard />
</div>

<section class="layout container">
	<section class="results">
		<div class="toolbar">
			<div class="search-wrap">
				<span class="search-icon" aria-hidden="true">⌕</span>
				<input
					bind:this={searchInput}
					type="search"
					placeholder="Search packet body, session, goal, adapter…"
					bind:value={search}
				/>
				<kbd>⌘K</kbd>
			</div>

			<div class="type-seg">
				{#each TYPES as t}
					<button
						class="seg-btn"
						class:active={selectedTypes.has(t.id)}
						title={t.desc}
						on:click={() => (selectedTypes = toggle(selectedTypes, t.id))}
					>
						{t.label}
					</button>
				{/each}
			</div>

			<div class="sort-wrap">
				<label for="sort">Sort</label>
				<select id="sort" bind:value={sort}>
					<option value="new">New first</option>
					<option value="old">Old first</option>
				</select>
			</div>

			<label class="check">
				<input type="checkbox" bind:checked={hideQuarantined} />
				<span>Hide withdrawn</span>
			</label>
		</div>

		<div class="meta-row">
			<span>
				<strong>{filtered.length.toLocaleString()}</strong>
				of {stats.total.toLocaleString()} packet{stats.total === 1 ? "" : "s"}
				{#if selectedGoals.size > 0 || selectedTypes.size > 0 || search}
					<button class="link-btn" on:click={clearFilters}>reset</button>
				{/if}
			</span>
			{#if latest}
				<span class="muted">latest: {timeAgo(latest)}</span>
			{/if}
		</div>

		{#if loading}
			<div class="state">Loading…</div>
		{:else if error}
			<div class="state err">
				<p><strong>Couldn't reach the read API.</strong></p>
				<p class="muted">{error}</p>
				<p class="muted">
					If you're running locally, start it with
					<code>make web-up</code> (port 8580).
				</p>
			</div>
		{:else if filtered.length === 0}
			<div class="state">
				{#if packets.length === 0}
					<p><strong>The corpus is empty.</strong></p>
					<p class="muted">
						No <code>raw</code>, <code>post</code>, or <code>distill</code>
						packets in the connected Bank yet. Run
						<code>oms start --goal &lt;goal&gt;</code>, contribute a
						<code>/self-distill</code>, and the post will show up here.
					</p>
				{:else}
					<p>No packets match these filters.</p>
					<button class="link-btn" on:click={clearFilters}>Reset filters</button>
				{/if}
			</div>
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

	<GoalRail
		{goals}
		selected={selectedGoals}
		onToggle={(id) => (selectedGoals = toggle(selectedGoals, id))}
		onClear={() => (selectedGoals = new Set())}
	/>
</section>

{#if active}
	<PacketDrawer packet={active} onClose={closeDrawer} />
{/if}

<style>
	.qs-band {
		padding-top: var(--space-lg);
	}

	.layout {
		display: grid;
		grid-template-columns: 1fr 280px;
		gap: var(--space-xl);
		padding-top: var(--space-lg);
		padding-bottom: var(--space-2xl);
		align-items: start;
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
		flex-wrap: wrap;
		padding: var(--space-sm);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
	}

	.search-wrap {
		position: relative;
		flex: 1 1 280px;
		min-width: 220px;
	}

	.search-wrap input {
		width: 100%;
		padding: 8px 56px 8px 30px;
		font-family: var(--sans);
		font-size: 0.9rem;
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		outline: none;
		transition:
			border-color 120ms,
			box-shadow 120ms;
	}

	.search-wrap input:focus {
		border-color: var(--accent-primary);
		box-shadow: 0 0 0 3px rgba(67, 56, 202, 0.18);
	}

	.search-icon {
		position: absolute;
		left: 10px;
		top: 50%;
		transform: translateY(-50%);
		color: var(--text-muted);
		font-size: 0.95rem;
	}

	.search-wrap kbd {
		position: absolute;
		right: 10px;
		top: 50%;
		transform: translateY(-50%);
		font-family: var(--mono);
		font-size: 0.7rem;
		padding: 2px 6px;
		color: var(--text-muted);
		background: var(--bg-tertiary);
		border: 1px solid var(--border-primary);
		border-radius: 4px;
		pointer-events: none;
	}

	.type-seg {
		display: flex;
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.seg-btn {
		padding: 6px 12px;
		font-family: var(--sans);
		font-size: 0.82rem;
		color: var(--text-secondary);
	}

	.seg-btn + .seg-btn {
		border-left: 1px solid var(--border-primary);
	}

	.seg-btn.active {
		background: var(--accent-primary);
		color: #fff;
	}

	.sort-wrap {
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.sort-wrap label {
		font-family: var(--mono);
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-muted);
	}

	.sort-wrap select {
		font-family: var(--sans);
		font-size: 0.85rem;
		padding: 6px 8px;
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
	}

	.check {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 0.82rem;
		color: var(--text-secondary);
		cursor: pointer;
	}

	.meta-row {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		font-size: 0.85rem;
		color: var(--text-secondary);
	}

	.meta-row strong {
		color: var(--text-primary);
	}

	.link-btn {
		font-size: 0.82rem;
		color: var(--accent-primary);
		text-decoration: underline;
		margin-left: 8px;
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

	@media (max-width: 880px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
</style>
