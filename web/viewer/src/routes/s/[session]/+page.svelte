<script>
	import { page } from "$app/stores";
	import { goto } from "$app/navigation";
	import { getSession, listAgents } from "$lib/api.js";
	import { deriveThreads, timeAgo } from "$lib/explorer.js";
	import { slugify } from "$lib/slug.js";
	import ThreadRow from "$components/ThreadRow.svelte";
	import CrumbBar from "$components/CrumbBar.svelte";
	import AgentLink from "$components/AgentLink.svelte";
	import Collapsible from "$components/Collapsible.svelte";

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
	$: traces = packets
		.filter((p) => p.type === "raw")
		.slice()
		.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
	$: rawCount = traces.length;
	$: bundles = packets.filter((p) => p.type === "distill");
	$: crumbs = [
		{ label: "Feed", href: "/" },
		...(session?.goal
			? [{ label: `/${session.goal}`, href: `/g/${slugify(session.goal)}`, mono: true }]
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
			<span class="eyebrow muted">Goal</span>
			{#if session?.goal}
				<a class="goal-title mono" href="/g/{slugify(session.goal)}">/{session.goal}</a>
			{:else}
				<span class="goal-title muted">(ungoaled)</span>
			{/if}
			<div class="sub muted">
				session <span class="mono">{sessionId}</span>
				{#if session?.status} · {session.status}{/if}
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

		<Collapsible label="Session stats">
			<table class="stats">
				<tbody>
					{#if session?.created_at}
						<tr><th>Started</th><td>{timeAgo(session.created_at)}</td></tr>
					{/if}
					<tr><th>Conversations</th><td>{threads.length}</td></tr>
					<tr><th>Bundles</th><td>{bundles.length}</td></tr>
					<tr><th>Raw traces</th><td>{rawCount}</td></tr>
					<tr><th>Agents</th><td>{agents.length}</td></tr>
				</tbody>
			</table>
		</Collapsible>

		{#if threads.length === 0}
			<div class="state">No conversations in this session.</div>
		{:else}
			<ul class="thread-list">
				{#each threads as t (t.root.id)}
					<li><ThreadRow thread={t} /></li>
				{/each}
			</ul>
		{/if}

		{#if traces.length > 0}
			<section class="traces">
				<h2 class="sec-title">Traces ({traces.length})</h2>
				<ul class="trace-list">
					{#each traces as p (p.id)}
						<li>
							<a class="trace-item" href="/t/{encodeURIComponent(sessionId)}/{encodeURIComponent(p.id.split('/')[1] ?? '')}">
								<span class="pill pill-raw">raw trace</span>
								<span class="mono">{(p.id.split("/")[1] ?? p.id).slice(0, 8)}</span>
								<span class="muted">— replay · text · conversation</span>
								<span class="trace-when muted">{timeAgo(p.created_at)}</span>
							</a>
						</li>
					{/each}
				</ul>
			</section>
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
		gap: 4px;
	}

	.eyebrow {
		font-size: 0.68rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.1em;
	}

	.goal-title {
		font-size: 1.5rem;
		font-weight: 700;
		letter-spacing: -0.01em;
		color: var(--accent-primary);
		word-break: break-word;
	}

	.goal-title:hover {
		text-decoration: underline;
	}

	.sub {
		font-size: 0.82rem;
	}

	.stats {
		border-collapse: collapse;
		font-size: 0.82rem;
	}

	.stats th {
		text-align: left;
		font-weight: 500;
		color: var(--text-muted);
		padding: 3px var(--space-lg) 3px 0;
	}

	.stats td {
		font-family: var(--mono);
		color: var(--text-primary);
		padding: 3px 0;
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

	.sec-title {
		font-family: var(--sans);
		font-size: 0.8rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-secondary);
		margin: 0 0 var(--space-sm);
	}

	.trace-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
	}

	.trace-item {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		padding: var(--space-sm) var(--space-md);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		color: var(--text-primary);
		font-size: 0.82rem;
		transition: border-color 140ms;
	}

	.trace-item:hover {
		border-color: var(--border-strong);
		text-decoration: none;
	}

	.trace-when {
		margin-left: auto;
		font-size: 0.74rem;
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
