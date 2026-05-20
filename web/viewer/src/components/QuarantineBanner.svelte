<script>
	// Visible-but-flagged invariant (oma.web.md): a quarantined packet stays
	// visible — the corpus is a read-everything audit record — but the "use
	// as context" affordance is disabled wherever a packet renders this banner.
	export let compact = false;
</script>

<div class="qb" class:compact>
	<span class="glyph" aria-hidden="true">⚠</span>
	<div class="body">
		<strong>withdrawn from distribution</strong>
		<span> — flagged as suspect. Excluded from <em>use as context</em>.</span>
	</div>
</div>

<style>
	.qb {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		padding: 10px 12px;
		background: var(--brand-amber-soft);
		border: 1px solid var(--brand-amber);
		border-radius: var(--radius);
		color: var(--brand-amber-dark);
		font-size: 0.85rem;
		line-height: 1.4;
	}

	.qb.compact {
		padding: 6px 10px;
		font-size: 0.78rem;
	}

	.glyph {
		font-size: 1.1rem;
		line-height: 1.2;
		color: var(--brand-amber);
	}

	.body strong {
		font-weight: 700;
	}

	.body em {
		font-style: italic;
		font-weight: 600;
	}
</style>
