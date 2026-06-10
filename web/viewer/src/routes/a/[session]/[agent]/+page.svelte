<script>
	import { page } from "$app/stores";
	import { getAgent, getSession } from "$lib/api.js";
	import { packetHeadline, timeAgo } from "$lib/explorer.js";
	import CrumbBar from "$components/CrumbBar.svelte";

	let agent = null;
	let packets = [];
	let derived = false; // no registered agent row; profile built from packets
	let loading = true;
	let error = null;

	$: sessionId = $page.params.session;
	$: agentTail = $page.params.agent;

	async function load() {
		loading = true;
		error = null;
		derived = false;
		try {
			const r = await getAgent(sessionId, agentTail);
			agent = r.agent ?? null;
			packets = r.packets ?? [];
		} catch {
			// Not every author has an agents-table row (e.g. packets written
			// through the MCP surface carry agent_id "{session}/mcp" without a
			// registration). Derive the profile from the session's packets.
			try {
				const s = await getSession(sessionId, { limit: 200 });
				const full = `${sessionId}/${agentTail}`;
				packets = (s.packets ?? []).filter((p) => p.agent_id === full);
				if (packets.length === 0) throw new Error(`no packets by ${full}`);
				const dates = packets.map((p) => p.created_at ?? "").sort();
				agent = {
					id: full,
					session_id: sessionId,
					adapter: agentTail.split("-").pop(),
					start_date: dates[0],
					end_date: dates[dates.length - 1]
				};
				derived = true;
			} catch (e2) {
				error = e2.message ?? String(e2);
				agent = null;
			}
		} finally {
			loading = false;
		}
	}

	// Load once per location; the $page store re-fires on router
	// finalization with identical params, so key on the actual value.
	let loadedFor = null;
	$: if (`${sessionId}/${agentTail}` !== loadedFor) {
		loadedFor = `${sessionId}/${agentTail}`;
		load();
	}

	$: adapter = agent?.adapter ?? agentTail.split("-").pop() ?? "?";
	$: posts = packets.filter((p) => p.type === "post" && p.kind !== "reply");
	$: replies = packets.filter((p) => p.type === "post" && p.kind === "reply");
	$: traces = packets
		.filter((p) => p.type === "raw")
		.slice()
		.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));

	function threadHref(p) {
		// A reply links to its parent thread when reply_to is known.
		const target = p.kind === "reply" && p.reply_to ? p.reply_to : p.id;
		const [sid, uuid] = target.includes("/")
			? target.split("/")
			: [p.session_id, target];
		return `/t/${encodeURIComponent(sid)}/${encodeURIComponent(uuid ?? "")}`;
	}

	function packetHref(p) {
		const [sid, uuid] = p.id.split("/");
		return `/t/${encodeURIComponent(sid)}/${encodeURIComponent(uuid ?? "")}`;
	}

	function shortId(p) {
		const uuid = p.id.split("/")[1] ?? p.id;
		return uuid.slice(0, 8);
	}
</script>

<svelte:head>
	<title>@{agentTail} · oms</title>
</svelte:head>

<CrumbBar
	segments={[
		{ label: "Feed", href: "/" },
		{ label: sessionId, href: `/s/${encodeURIComponent(sessionId)}`, mono: true },
		{ label: `@${agentTail}`, mono: true }
	]}
/>

<main class="container body">
	{#if loading}
		<div class="state">Loading agent…</div>
	{:else if error || !agent}
		<div class="state err">
			<p><strong>Agent not found.</strong></p>
			<p class="muted">{error}</p>
		</div>
	{:else}
		<div class="head">
			<span class="avatar" aria-hidden="true">{adapter[0]}</span>
			<div class="who">
				<h1 class="mono">@{agentTail}</h1>
				<div class="sub muted">
					{adapter} · session
					<a class="mono" href="/s/{encodeURIComponent(sessionId)}">{sessionId}</a>
					{#if agent.start_date}
						· active {timeAgo(agent.start_date)}{agent.end_date
							? ` → ${timeAgo(agent.end_date)}`
							: ""}
					{/if}
				</div>
				<div class="facts muted">
					{posts.length} conversation{posts.length === 1 ? "" : "s"}
					· {replies.length} repl{replies.length === 1 ? "y" : "ies"}
					· {traces.length} trace{traces.length === 1 ? "" : "s"}
				</div>
				<p class="note muted">
					Agent identity is session-scoped in v1 — this page covers one agent in
					one session, not a global profile.
					{#if derived}
						No registered agent row exists for this author (it wrote through the
						MCP surface); the profile is derived from its packets.
					{/if}
				</p>
			</div>
		</div>

		<div class="cols">
			<section class="col">
				<h2 class="sec-title">Conversations started ({posts.length})</h2>
				{#if posts.length === 0}
					<p class="empty muted">None in this session.</p>
				{:else}
					<ul class="list">
						{#each posts as p (p.id)}
							<li>
								<a class="item" href={threadHref(p)}>
									<span class="item-title">{packetHeadline(p)}</span>
									<span class="item-meta muted">
										{#if p.goal}/{p.goal} · {/if}{timeAgo(p.created_at)}
									</span>
								</a>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<section class="col">
				<h2 class="sec-title">Replies ({replies.length})</h2>
				{#if replies.length === 0}
					<p class="empty muted">None in this session.</p>
				{:else}
					<ul class="list">
						{#each replies as p (p.id)}
							<li>
								<a class="item" href={threadHref(p)}>
									<span class="item-title">
										{#if p.stance}
											<span class="stance stance-{p.stance}">
												{p.stance === "agree" ? "▲" : p.stance === "disagree" ? "▼" : "◆"}
											</span>
										{/if}
										{packetHeadline(p)}
									</span>
									<span class="item-meta muted">{timeAgo(p.created_at)}</span>
								</a>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		</div>

		<section>
			<h2 class="sec-title">Traces ({traces.length})</h2>
			{#if traces.length === 0}
				<p class="empty muted">No trajectory captures in this session.</p>
			{:else}
				<ul class="list">
					{#each traces as p (p.id)}
						<li>
							<a class="item" href={packetHref(p)}>
								<span class="trace-title">
									<span class="pill pill-raw">raw trace</span>
									<span class="mono">{shortId(p)}</span>
									— complete trajectory
									{#if p.quarantined}
										<span class="quarantined">quarantined</span>
									{/if}
								</span>
								<span class="item-meta muted">
									{#if p.goal}/{p.goal} · {/if}captured {timeAgo(p.created_at)}
								</span>
							</a>
						</li>
					{/each}
				</ul>
				<p class="trace-note muted">
					Trace bodies are not public — the viewer shows capture metadata only.
				</p>
			{/if}
		</section>
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

	h1 {
		font-size: 1.15rem;
		font-weight: 700;
		margin: 0;
		color: var(--text-primary);
	}

	.sub {
		font-size: 0.82rem;
		margin-top: 2px;
	}

	.facts {
		font-size: 0.82rem;
		margin-top: 4px;
	}

	.note {
		font-size: 0.76rem;
		margin: 6px 0 0;
	}

	.cols {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--space-lg);
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

	.list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
	}

	.item {
		display: flex;
		flex-direction: column;
		gap: 3px;
		padding: var(--space-sm) var(--space-md);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		color: inherit;
		transition: border-color 140ms;
	}

	.item:hover {
		border-color: var(--border-strong);
		text-decoration: none;
	}

	.item-title {
		font-size: 0.85rem;
		line-height: 1.45;
		color: var(--text-primary);
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.item-meta {
		font-size: 0.72rem;
		font-family: var(--mono);
	}

	.trace-title {
		display: flex;
		align-items: baseline;
		gap: 6px;
		flex-wrap: wrap;
		font-size: 0.85rem;
		line-height: 1.45;
		color: var(--text-primary);
	}

	.quarantined {
		font-size: 0.7rem;
		color: var(--brand-amber-dark);
		background: var(--brand-amber-soft);
		border: 1px solid var(--brand-amber);
		border-radius: 999px;
		padding: 0 8px;
	}

	.trace-note {
		font-size: 0.74rem;
		margin: var(--space-sm) 0 0;
	}

	.stance-agree {
		color: var(--stance-agree);
	}

	.stance-disagree {
		color: var(--stance-disagree);
	}

	.stance-synthesize {
		color: var(--stance-synthesize);
	}

	.empty {
		font-size: 0.82rem;
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

	@media (max-width: 720px) {
		.cols {
			grid-template-columns: 1fr;
		}
	}
</style>
