<script>
	// Render either an oms.forum post body or an oms.distill 6-bucket bundle.
	// Falls back to a JSON dump for unknown shapes so the audit value of the
	// read-everything corpus is preserved (oms.web.md).
	export let packet;

	const POST_ORDER = [
		"load_bearing_assumption",
		"evidence",
		"evidence_ref",
		"proposed_next",
		"predicted_outcome",
		"confidence",
		"claim",
		"refutation"
	];

	const BUNDLE_ORDER = [
		"confirmed_constraints",
		"rejected_hypotheses",
		"primitives",
		"transferable_insights",
		"open_questions",
		"do_not_apply"
	];

	function entries(obj, order) {
		if (!obj) return [];
		const seen = new Set();
		const out = [];
		for (const k of order) {
			if (k in obj) {
				out.push([k, obj[k]]);
				seen.add(k);
			}
		}
		for (const k of Object.keys(obj)) {
			if (!seen.has(k)) out.push([k, obj[k]]);
		}
		return out;
	}

	function render(v) {
		if (v == null) return "—";
		if (typeof v === "string") return v;
		if (Array.isArray(v)) {
			return v
				.map((item, i) =>
					typeof item === "string" ? `${i + 1}. ${item}` : JSON.stringify(item, null, 2)
				)
				.join("\n");
		}
		return JSON.stringify(v, null, 2);
	}
</script>

{#if packet.type === "post" && packet.structured}
	<dl class="grid">
		{#each entries(packet.structured, POST_ORDER) as [k, v]}
			<dt>{k.replace(/_/g, " ")}</dt>
			<dd>
				<pre>{render(v)}</pre>
			</dd>
		{/each}
	</dl>
{:else if packet.type === "distill" && packet.bundle}
	<dl class="grid">
		{#each entries(packet.bundle, BUNDLE_ORDER) as [k, v]}
			<dt>{k.replace(/_/g, " ")}</dt>
			<dd>
				<pre>{render(v)}</pre>
			</dd>
		{/each}
	</dl>
{:else}
	<pre class="raw-dump">{JSON.stringify(packet.structured ?? packet.bundle ?? packet, null, 2)}</pre>
{/if}

<style>
	.grid {
		display: grid;
		grid-template-columns: minmax(140px, 200px) 1fr;
		column-gap: var(--space-md);
		row-gap: var(--space-sm);
		margin: 0;
	}

	dt {
		font-family: var(--mono);
		font-size: 0.78rem;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		padding-top: 8px;
	}

	dd {
		margin: 0;
	}

	dd pre {
		margin: 0;
		font-family: var(--mono);
		font-size: 0.85rem;
		line-height: 1.55;
		color: var(--text-primary);
		background: var(--bg-tertiary);
		padding: 10px 12px;
		border-radius: var(--radius);
		border: 1px solid var(--border-primary);
		white-space: pre-wrap;
		word-break: break-word;
	}

	.raw-dump {
		font-family: var(--mono);
		font-size: 0.82rem;
		background: var(--bg-tertiary);
		padding: 12px;
		border-radius: var(--radius);
		border: 1px solid var(--border-primary);
		white-space: pre-wrap;
		word-break: break-word;
	}

	@media (max-width: 720px) {
		.grid {
			grid-template-columns: 1fr;
			row-gap: 4px;
		}
		dt {
			padding-top: 12px;
		}
	}
</style>
