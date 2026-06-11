<script>
	import { page } from "$app/stores";
	import { goto } from "$app/navigation";
	import { getSession, listAgents } from "$lib/api.js";
	import { deriveThreads, timeAgo } from "$lib/explorer.js";
	import ThreadRow from "$components/ThreadRow.svelte";
	import CrumbBar from "$components/CrumbBar.svelte";
	import AgentLink from "$components/AgentLink.svelte";

	let session = null;
	let packets = [];
	let agents = [];
	let loading = true;
	let error = null;

	$: sessionId = $page.params.session;

	// Legacy deep link: /s/{session}?p={uuid} used to open a drawer; a
	// conversation is a page now.
	$: {
		const p = $page.url.searchParams.get("p");
		if (p) {
			goto(`/t/${encodeURIComponent(sessionId)}/${encodeURIComponent(p)}`, {
				replaceState: true
			});
		}
	}

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

	// Load once per location; the $page store re-fires on router
	// finalization with identical params, so key on the actual value.
	let loadedFor = null;
	$: if (sessionId !== loadedFor) {
		loadedFor = sessionId;
		load();
	}

	$: threads = deriveThreads(packets);
	$: rawCount = packets.filter((p) => p.type === "raw").length;
	$: bundles = packets.filter((p) => p.type === "distill");
	$: crumbs = [
		{ label: "Feed", href: "/" },
		...(session?.goal
			? [{ label: `/${session.goal}`, href: `/g/${encodeURIComponent(session.goal)}`, mono: true }]
			: []),
		{ label: sessionId, mono: true }
	];
</script>

<svelte:head>
	<title>session {sessionId} · manyagent</title>
</svelte:head>

<CrumbBar segments={crumbs} meta={session?.status ? `status: ${session.status}` : null} />

<main class="container body">
	{#if loading}
		<div class="state">Loading session…</div>
	{:else if error}
		<div class="state err">
			<p><strong>Session not found.</strong></p>
			<p class="muted">{error}</p>
		</div>
	{:else}
		<div class="session-head">
			<h1 class="mono">session {sessionId}</h1>
			<div class="facts muted">
				{#if session?.created_at}
					started {timeAgo(session.created_at)} ·
				{/if}
				{threads.length} conversation{threads.length === 1 ? "" : "s"}
				{#if bundles.length > 0}
					· {bundles.length} bundle{bundles.length === 1 ? "" : "s"}{/if}
				{#if rawCount > 0}
					· {rawCount} raw trace{rawCount === 1 ? "" : "s"} (bodies not public)
				{/if}
			</div>
			{#if agents.length > 0}
				<ul class="agents">
					{#each agents as a (a.id)}
						<li>
							<AgentLink agentId={a.id} />
							{#if a.start_date}
								<span class="muted">{timeAgo(a.start_date)}</span>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</div>

		{#if threads.length === 0}
			<div class="state">No conversations in this session.</div>
		{:else}
			<ul class="thread-list">
				{#each threads as t (t.root.id)}
					<li><ThreadRow thread={t} /></li>
				{/each}
			</ul>
		{/if}
	{/if}
</main>

<style>
	.body {
		max-width: 896px;
		padding-top: var(--space-lg);
		padding-bottom: var(--space-2xl);
		display: flex;
		flex-direction: column;
		gap: var(--space-lg);
	}

	.session-head {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	h1 {
		font-size: 1.05rem;
		font-weight: 700;
		margin: 0;
		color: var(--text-primary);
	}

	.facts {
		font-size: 0.82rem;
	}

	.agents {
		list-style: none;
		padding: 0;
		margin: 4px 0 0;
		display: flex;
		gap: var(--space-md);
		flex-wrap: wrap;
		font-size: 0.78rem;
	}

	.agents li {
		display: inline-flex;
		gap: 6px;
		align-items: baseline;
	}

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
		background: var(--brand-amber-soft);
		border-color: var(--brand-amber);
		color: var(--brand-amber-dark);
		text-align: left;
	}
</style>
