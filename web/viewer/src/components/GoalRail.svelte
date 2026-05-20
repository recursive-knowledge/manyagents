<script>
	/**
	 * Goal facet rail. Goals are derived client-side from the recent packet
	 * stream (see explorer.js / About). Click toggles the goal filter.
	 *
	 * @typedef {{id: string, label: string, count: number}} GoalFacet
	 */

	/** @type {GoalFacet[]} */
	export let goals = [];
	/** @type {Set<string>} */
	export let selected = new Set();
	export let onToggle = (/** @type {string} */ _id) => {};
	export let onClear = () => {};
</script>

<aside class="rail">
	<div class="section">
		<div class="title">
			<span>Goals</span>
			{#if selected.size > 0}
				<button class="clear" on:click={onClear}>clear</button>
			{/if}
		</div>
		{#if goals.length === 0}
			<p class="empty">No goals in the recent corpus.</p>
		{:else}
			<ul class="chips">
				{#each goals as g (g.id)}
					<li>
						<button
							class="chip"
							class:active={selected.has(g.id)}
							on:click={() => onToggle(g.id)}
						>
							<span class="label">/{g.label}</span>
							<span class="chip-count">{g.count}</span>
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
	<slot />
</aside>

<style>
	.rail {
		display: flex;
		flex-direction: column;
		gap: var(--space-lg);
	}

	.section {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.title {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		font-family: var(--sans);
		font-size: 0.7rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-muted);
	}

	.clear {
		font-family: var(--sans);
		font-size: 0.7rem;
		font-weight: 600;
		color: var(--accent-primary);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.clear:hover {
		text-decoration: underline;
	}

	.chips {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.chip {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 6px 10px;
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
		font-family: var(--mono);
		font-size: 0.82rem;
		color: var(--text-secondary);
		text-align: left;
		transition:
			background 120ms,
			border-color 120ms,
			color 120ms;
	}

	.chip:hover {
		color: var(--text-primary);
		border-color: var(--border-secondary);
	}

	.chip.active {
		background: var(--brand-indigo-soft);
		color: var(--brand-indigo-dark);
		border-color: var(--brand-indigo);
		font-weight: 600;
	}

	.label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.chip-count {
		margin-left: auto;
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.chip.active .chip-count {
		color: var(--accent-primary);
	}

	.empty {
		font-size: 0.82rem;
		color: var(--text-muted);
	}
</style>
