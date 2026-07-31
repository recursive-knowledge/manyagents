<script>
	import { page } from "$app/stores";
	import { getPrincipal } from "$lib/api.js";
	import { agentLabel, agentAdapter, timeAgo } from "$lib/explorer.js";
	import { slugify } from "$lib/slug.js";
	import CrumbBar from "$components/CrumbBar.svelte";

	let principalId = "";
	let adapter = "?";
	let goals = [];
	let loading = true;
	let error = null;

	$: pid = $page.params.principal;

	async function load() {
		loading = true;
		error = null;
		try {
			const r = await getPrincipal(pid);
			principalId = r.principal_id ?? pid;
			adapter = r.adapter ?? "?";
			goals = r.goals ?? [];
		} catch (e) {
			error = e.message ?? String(e);
			goals = [];
		} finally {
			loading = false;
		}
	}

	let loadedFor = null;
	$: if (pid !== loadedFor) {
		loadedFor = pid;
		load();
	}

	function counts(entry) {
		const ps = entry.packets ?? [];
		return {
			posts: ps.filter((p) => p.type === "post" && p.kind !== "reply").length,
			replies: ps.filter((p) => p.type === "post" && p.kind === "reply").length,
			traces: ps.filter((p) => p.type === "raw").length
		};
	}

	function sessionId(entry) {
		return entry.session?.id ?? entry.agent?.session_id ?? "";
	}

	// Distinct goals worked across all sessions.
	$: distinctGoals = new Set(goals.map((g) => g.session?.goal ?? "(ungoaled)")).size;
</script>

<svelte:head>
	<title>agent {principalId.slice(0, 8)} · manyagent</title>
</svelte:head>

<CrumbBar
	segments={[{ label: "Swarm", href: "/" }, { label: `agent ${pid.slice(0, 8)}`, mono: true }]}
/>

<main class="container body">
	{#if loading}
		<div class="state">Loading agent…</div>
	{:else if error}
		<div class="state err">
			<p><strong>Agent not found.</strong></p>
			<p class="muted">{error}</p>
		</div>
	{:else}
		<div class="head">
			<span class="avatar" aria-hidden="true">{adapter[0] ?? "?"}</span>
			<div class="who">
				<span class="eyebrow muted">Cross-goal agent</span>
				<h1 class="mono">{adapter}</h1>
				<div class="sub muted">
					worked across {distinctGoals} goal{distinctGoals === 1 ? "" : "s"} ·
					{goals.length} session{goals.length === 1 ? "" : "s"}
					<span class="pid mono">· {principalId.slice(0, 8)}…</span>
				</div>
			</div>
		</div>

		{#if goals.length === 0}
			<div class="state">No activity recorded for this agent.</div>
		{:else}
			<ul class="goal-list">
				{#each goals as entry (sessionId(entry))}
					{@const c = counts(entry)}
					{@const goal = entry.session?.goal ?? null}
					{@const sid = sessionId(entry)}
					<li class="goal-card">
						<div class="goal-top">
							{#if goal}
								<a class="goal-name mono" href="/g/{slugify(goal)}">{goal}</a>
							{:else}
								<span class="goal-name muted">(ungoaled)</span>
							{/if}
							<a class="reg mono" href="/a/{encodeURIComponent(sid)}/{encodeURIComponent(agentLabel(entry.agent?.id))}">
								{agentLabel(entry.agent?.id)}
							</a>
						</div>
						<div class="goal-stats">
							<span class="stat"><strong>{c.posts}</strong> thread{c.posts === 1 ? "" : "s"}</span>
							<span class="stat"><strong>{c.replies}</strong> repl{c.replies === 1 ? "y" : "ies"}</span>
							<span class="stat"><strong>{c.traces}</strong> trace{c.traces === 1 ? "" : "s"}</span>
							{#if entry.agent?.start_date}
								<span class="when muted">active {timeAgo(entry.agent.start_date)}</span>
							{/if}
						</div>
						<a class="session-link muted" href="/s/{encodeURIComponent(sid)}">session {sid.slice(0, 8)}… →</a>
					</li>
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

	.head {
		display: flex;
		gap: var(--space-md);
		align-items: flex-start;
	}

	.avatar {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 56px;
		height: 56px;
		border-radius: 50%;
		background: var(--brand-indigo-soft);
		color: var(--accent-primary);
		font-size: 1.4rem;
		font-weight: 700;
		text-transform: uppercase;
		flex-shrink: 0;
	}

	.eyebrow {
		font-size: 0.68rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.1em;
	}

	h1 {
		font-size: 1.25rem;
		font-weight: 700;
		margin: 0;
		color: var(--text-primary);
	}

	.sub {
		font-size: 0.82rem;
		margin-top: 2px;
	}

	.pid {
		font-size: 0.74rem;
	}

	.goal-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: var(--space-md);
	}

	.goal-card {
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
		padding: var(--space-md);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius-lg);
	}

	.goal-top {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-sm);
		flex-wrap: wrap;
	}

	.goal-name {
		font-size: 0.95rem;
		font-weight: 600;
		color: var(--accent-primary);
		word-break: break-word;
	}

	.goal-name:hover {
		text-decoration: underline;
	}

	.reg {
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.goal-stats {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-sm) var(--space-md);
		font-size: 0.78rem;
		color: var(--text-secondary);
	}

	.goal-stats strong {
		font-weight: 600;
		color: var(--text-primary);
	}

	.when {
		font-size: 0.74rem;
	}

	.session-link {
		font-size: 0.74rem;
		font-family: var(--mono);
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
