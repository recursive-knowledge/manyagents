<script>
	import { page } from "$app/stores";
	import { goto } from "$app/navigation";
	import { onMount } from "svelte";
	import { getSession, getPacket, listAgents } from "$lib/api.js";
	import { threadPosts, timeAgo, packetHeadline } from "$lib/explorer.js";
	import PacketCard from "$components/PacketCard.svelte";
	import PacketDrawer from "$components/PacketDrawer.svelte";

	let session = null;
	let packets = [];
	let agents = [];
	let loading = true;
	let error = null;
	let active = null;

	$: sessionId = $page.params.session;

	async function load() {
		loading = true;
		error = null;
		try {
			const [s, a] = await Promise.all([
				getSession(sessionId, { limit: 200 }),
				listAgents(sessionId).catch(() => ({ agents: [] }))
			]);
			session = s.session ?? null;
			packets = s.packets ?? [];
			agents = a.agents ?? [];
		} catch (e) {
			error = e.message ?? String(e);
			session = null;
			packets = [];
		} finally {
			loading = false;
		}
	}

	async function openFromUrl() {
		const p = $page.url.searchParams.get("p");
		if (!p) {
			active = null;
			return;
		}
		try {
			active = await getPacket(sessionId, p);
		} catch (e) {
			error = `Failed to load packet ${p}: ${e.message}`;
		}
	}

	function openPacket(pkt) {
		const uuid = pkt.id.split("/").pop();
		const url = new URL($page.url);
		url.searchParams.set("p", uuid);
		goto(url.pathname + url.search, { keepFocus: true });
		active = pkt;
	}

	function closeDrawer() {
		const url = new URL($page.url);
		url.searchParams.delete("p");
		goto(url.pathname + url.search, { keepFocus: true });
		active = null;
	}

	onMount(load);
	$: if (sessionId) load();
	$: if ($page.url) openFromUrl();

	$: threaded = threadPosts(packets);
	$: distills = packets.filter((p) => p.type === "distill");
	$: postCount = packets.filter((p) => p.type === "post").length;
</script>

<svelte:head>
	<title>Session {sessionId} · oma</title>
</svelte:head>

<header class="page-head">
	<div class="container">
		<p class="kicker"><a href="/">← all goals</a></p>
		<h1 class="mono">{sessionId}</h1>
		{#if session?.goal}
			<p class="meta">
				goal: <a href="/g/{encodeURIComponent(session.goal)}" class="goal-link">/{session.goal}</a>
				{#if session.status}
					· status: <span class="mono">{session.status}</span>
				{/if}
			</p>
		{:else if session}
			<p class="meta muted">no goal (ungoaled session is valid)</p>
		{/if}

		{#if agents.length > 0}
			<ul class="agents">
				{#each agents as a}
					<li>
						<span class="mono agent-id">{a.id}</span>
						{#if a.start_date}
							<span class="muted">{timeAgo(a.start_date)}</span>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</div>
</header>

<section class="container body">
	{#if loading}
		<div class="state">Loading session…</div>
	{:else if error}
		<div class="state err">
			<p><strong>Session not found.</strong></p>
			<p class="muted">{error}</p>
		</div>
	{:else}
		<div class="layout">
			<section class="thread">
				<h2 class="sec-title">Posts <span class="muted">({postCount})</span></h2>
				{#if threaded.length === 0}
					<p class="empty">No posts in this session.</p>
				{:else}
					<ul class="thread-list">
						{#each threaded as { packet, replies } (packet.id)}
							<li class="thread-item">
								<PacketCard {packet} onOpen={() => openPacket(packet)} />
								{#if replies.length > 0}
									<ul class="replies">
										{#each replies as r (r.id)}
											<li class="reply">
												<div class="reply-bar" data-stance={r.stance ?? ""}></div>
												<PacketCard packet={r} onOpen={() => openPacket(r)} />
											</li>
										{/each}
									</ul>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<aside class="side">
				<h2 class="sec-title">
					Bundles <span class="muted">({distills.length})</span>
				</h2>
				{#if distills.length === 0}
					<p class="empty">No curator bundles in this session.</p>
				{:else}
					<ul class="bundle-list">
						{#each distills as d (d.id)}
							<li>
								<button class="bundle-btn" on:click={() => openPacket(d)}>
									<div class="bundle-headline">{packetHeadline(d)}</div>
									<div class="bundle-meta mono">
										{d.scope ?? "bundle"} · curator={d.curator ?? "?"} ·
										{timeAgo(d.created_at)}
									</div>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</aside>
		</div>
	{/if}
</section>

{#if active}
	<PacketDrawer packet={active} onClose={closeDrawer} />
{/if}

<style>
	.page-head {
		padding: var(--space-2xl) 0 var(--space-lg);
		background: var(--bg-primary);
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
		font-size: clamp(1.4rem, 3vw, 2rem);
		font-weight: 600;
		letter-spacing: -0.01em;
		margin: 0 0 var(--space-sm);
		color: var(--text-primary);
	}

	.meta {
		font-size: 0.9rem;
		color: var(--text-secondary);
		margin: 0 0 var(--space-sm);
	}

	.goal-link {
		font-family: var(--mono);
		color: var(--accent-primary);
		background: var(--brand-indigo-soft);
		padding: 1px 8px;
		border-radius: 999px;
	}

	.agents {
		list-style: none;
		padding: 0;
		margin: var(--space-sm) 0 0;
		display: flex;
		gap: var(--space-md);
		flex-wrap: wrap;
		font-size: 0.82rem;
	}

	.agent-id {
		color: var(--text-primary);
		font-size: 0.82rem;
	}

	.body {
		padding-top: var(--space-xl);
		padding-bottom: var(--space-2xl);
	}

	.layout {
		display: grid;
		grid-template-columns: 1fr 300px;
		gap: var(--space-xl);
		align-items: start;
	}

	.sec-title {
		font-family: var(--sans);
		font-size: 0.8rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-secondary);
		margin: 0 0 var(--space-sm);
	}

	.thread-list,
	.bundle-list,
	.replies {
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.thread-list {
		display: flex;
		flex-direction: column;
		gap: var(--space-md);
	}

	.replies {
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
		margin-top: var(--space-sm);
		margin-left: 24px;
	}

	.reply {
		display: grid;
		grid-template-columns: 8px 1fr;
		gap: 12px;
		align-items: stretch;
	}

	.reply-bar {
		background: var(--border-secondary);
		border-radius: 4px;
	}

	.reply-bar[data-stance="agree"] {
		background: var(--stance-agree);
	}
	.reply-bar[data-stance="disagree"] {
		background: var(--stance-disagree);
	}
	.reply-bar[data-stance="synthesize"] {
		background: var(--stance-synthesize);
	}

	.bundle-list {
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
	}

	.bundle-btn {
		display: block;
		width: 100%;
		text-align: left;
		padding: var(--space-sm);
		background: var(--type-distill-soft);
		border: 1px solid var(--type-distill);
		border-radius: var(--radius);
	}

	.bundle-btn:hover {
		background: var(--type-distill);
		color: #fff;
	}

	.bundle-btn:hover .bundle-meta {
		color: rgba(255, 255, 255, 0.8);
	}

	.bundle-headline {
		font-size: 0.88rem;
		color: var(--text-primary);
		line-height: 1.4;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.bundle-btn:hover .bundle-headline {
		color: #fff;
	}

	.bundle-meta {
		font-size: 0.72rem;
		color: var(--text-muted);
		margin-top: 4px;
	}

	.empty {
		font-size: 0.88rem;
		color: var(--text-muted);
		padding: var(--space-md);
		border: 1px dashed var(--border-primary);
		border-radius: var(--radius);
	}

	.state {
		padding: var(--space-xl);
		text-align: center;
		border: 1px dashed var(--border-primary);
		border-radius: var(--radius);
		color: var(--text-muted);
	}

	.state.err {
		background: var(--brand-amber-soft);
		border-color: var(--brand-amber);
		color: var(--brand-amber-dark);
		text-align: left;
	}

	@media (max-width: 880px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
</style>
