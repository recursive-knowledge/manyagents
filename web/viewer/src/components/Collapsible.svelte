<script>
	/**
	 * A styled disclosure matching the viewer's card tokens. Collapsed by
	 * default — used for secondary information (reuse rankings, session stats,
	 * agent metadata) that should stay out of the way until asked for.
	 */

	/** @type {string} the always-visible summary label */
	export let label;
	/** @type {boolean} start expanded? */
	export let open = false;
	/** @type {string|null} optional muted count/hint shown next to the label */
	export let hint = null;
</script>

<details class="collapsible" {open}>
	<summary>
		<span class="label">{label}</span>
		{#if hint}<span class="hint muted">{hint}</span>{/if}
		<span class="chevron" aria-hidden="true">›</span>
	</summary>
	<div class="body">
		<slot />
	</div>
</details>

<style>
	.collapsible {
		background: var(--bg-primary);
		border: 1px solid var(--border-primary);
		border-radius: var(--radius);
	}

	summary {
		display: flex;
		align-items: center;
		gap: var(--space-sm);
		padding: var(--space-sm) var(--space-md);
		cursor: pointer;
		list-style: none;
		font-family: var(--sans);
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-secondary);
		user-select: none;
	}

	summary::-webkit-details-marker {
		display: none;
	}

	.hint {
		font-weight: 400;
		text-transform: none;
		letter-spacing: 0;
		font-size: 0.74rem;
	}

	.chevron {
		margin-left: auto;
		font-size: 1rem;
		color: var(--text-muted);
		transition: transform 140ms ease;
	}

	.collapsible[open] .chevron {
		transform: rotate(90deg);
	}

	.body {
		padding: 0 var(--space-md) var(--space-md);
	}
</style>
