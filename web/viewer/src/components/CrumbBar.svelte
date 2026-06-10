<script>
	/**
	 * The one navigation band, identical on every page: breadcrumb segments on
	 * the left, optional muted context on the right.
	 *
	 * @type {{label: string, href?: string, mono?: boolean}[]}
	 * The last segment is the current location (accent, no link).
	 */
	export let segments = [];
	/** @type {string|null} right-aligned muted context line */
	export let meta = null;
</script>

<header class="crumb-band">
	<div class="container crumb-row">
		<nav class="crumb">
			{#each segments as s, i}
				{#if i > 0}
					<span class="sep">/</span>
				{/if}
				{#if s.href}
					<a href={s.href} class:mono={s.mono}>{s.label}</a>
				{:else}
					<span class="here" class:mono={s.mono}>{s.label}</span>
				{/if}
			{/each}
			<slot />
		</nav>
		{#if meta}
			<span class="meta muted">{meta}</span>
		{/if}
	</div>
</header>

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
		flex-wrap: wrap;
		min-width: 0;
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
		font-weight: 600;
		color: var(--accent-primary);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 420px;
	}

	.meta {
		font-size: 0.78rem;
		text-align: right;
	}
</style>
