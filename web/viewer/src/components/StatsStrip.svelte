<script>
	/**
	 * @typedef {{total: number, posts: number, distills: number, goals: number, sessions: number, quarantined: number}} Stats
	 */
	/** @type {Stats} */
	export let stats;
</script>

<div class="strip">
	<div class="item">
		<div class="num">{stats.total.toLocaleString()}</div>
		<div class="lbl">Packets</div>
	</div>
	<div class="item">
		<div class="num">{stats.posts.toLocaleString()}</div>
		<div class="lbl">Posts</div>
	</div>
	<div class="item">
		<div class="num">{stats.distills.toLocaleString()}</div>
		<div class="lbl">Bundles</div>
	</div>
	<div class="item">
		<div class="num">{stats.goals.toLocaleString()}</div>
		<div class="lbl">Goals</div>
	</div>
	<div class="item">
		<div class="num">{stats.sessions.toLocaleString()}</div>
		<div class="lbl">Sessions</div>
	</div>
	{#if stats.quarantined > 0}
		<div class="item warn">
			<div class="num">{stats.quarantined.toLocaleString()}</div>
			<div class="lbl">Withdrawn</div>
		</div>
	{/if}
</div>

<style>
	.strip {
		display: flex;
		gap: var(--space-lg);
		flex-wrap: wrap;
		padding: var(--space-md);
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
	}

	.item {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 64px;
	}

	.num {
		font-family: var(--display);
		font-size: 1.4rem;
		font-weight: 700;
		color: var(--text-primary);
		font-variant-numeric: tabular-nums;
		line-height: 1;
	}

	.lbl {
		font-family: var(--sans);
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-muted);
	}

	.warn .num {
		color: var(--brand-amber);
	}

	.warn .lbl {
		color: var(--brand-amber-dark);
	}
</style>
