<script>
	export let value = null; // 1..5 or null ("unrated" is valid; oma.forum)
</script>

<span class="stars" title={value == null ? "unrated" : `${value} of 5`}>
	{#if value == null}
		<span class="unrated">unrated</span>
	{:else}
		{#each Array(5) as _, i}
			<span class="star" class:on={i < value}>★</span>
		{/each}
	{/if}
</span>

<style>
	.stars {
		display: inline-flex;
		gap: 1px;
		font-size: 0.85rem;
		color: var(--text-muted);
		line-height: 1;
	}

	.star.on {
		color: var(--brand-amber);
	}

	.unrated {
		font-family: var(--mono);
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}
</style>
