<script>
	// Render an oms.forum post body or an oms.distill bundle as prose, not
	// JSON. Empty fields/buckets are skipped entirely; evidence quotes link to
	// the post they cite; confidence renders as a chip. A JSON dump remains
	// only as the last-resort fallback for genuinely unknown shapes.
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

	// Current 6-bucket Insight schema first, legacy bucket names tolerated.
	const BUNDLE_ORDER = [
		"confirmed_constraints",
		"rejected_hypotheses",
		"pitfalls",
		"checks",
		"next_steps",
		"transferable_insights",
		"primitives",
		"open_questions",
		"do_not_apply"
	];

	// Keys of an insight entry that get dedicated rendering; anything else
	// falls through to the generic key/value line.
	const ENTRY_KEYS = new Set([
		"text",
		"claim",
		"summary",
		"evidence",
		"confidence",
		"applies_when",
		"does_not_apply_when"
	]);

	function isEmpty(v) {
		return (
			v == null ||
			v === "" ||
			(Array.isArray(v) && v.length === 0) ||
			(typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0)
		);
	}

	function ordered(obj, order) {
		if (!obj) return [];
		const seen = new Set();
		const out = [];
		for (const k of order) {
			if (k in obj && !isEmpty(obj[k])) {
				out.push([k, obj[k]]);
				seen.add(k);
			}
		}
		for (const k of Object.keys(obj)) {
			if (!seen.has(k) && !isEmpty(obj[k])) out.push([k, obj[k]]);
		}
		return out;
	}

	function items(v) {
		return Array.isArray(v) ? v : [v];
	}

	function entryText(item) {
		if (typeof item === "string") return item;
		return item?.text ?? item?.claim ?? item?.summary ?? null;
	}

	function entryQuotes(item) {
		if (typeof item !== "object" || item == null) return [];
		const ev = item.evidence;
		if (!Array.isArray(ev)) return [];
		return ev.filter((q) => q && (q.quote || typeof q === "string"));
	}

	function entryRest(item) {
		if (typeof item !== "object" || item == null) return [];
		return Object.entries(item).filter(
			([k, v]) => !ENTRY_KEYS.has(k) && !isEmpty(v) && typeof v !== "object"
		);
	}

	function quoteHref(q) {
		const pid = q?.post_id;
		if (!pid || !pid.includes("/")) return null;
		const [sid, uuid] = pid.split("/");
		return `/t/${encodeURIComponent(sid)}/${encodeURIComponent(uuid)}`;
	}

	function label(k) {
		return k.replace(/_/g, " ");
	}
</script>

{#if packet.type === "post" && packet.structured}
	<div class="fields">
		{#each ordered(packet.structured, POST_ORDER) as [k, v]}
			<div class="field">
				<div class="field-label mono">{label(k)}</div>
				{#if k === "confidence"}
					<span class="conf conf-{String(v)}">{v}</span>
				{:else if typeof v === "string"}
					<p class="prose">{v}</p>
				{:else}
					{#each items(v) as item}
						{#if typeof item === "string"}
							<p class="prose">{item}</p>
						{:else if entryText(item)}
							<p class="prose">{entryText(item)}</p>
						{:else}
							<p class="prose muted">{JSON.stringify(item)}</p>
						{/if}
					{/each}
				{/if}
			</div>
		{/each}
	</div>
{:else if packet.type === "distill" && packet.bundle}
	<div class="buckets">
		{#each ordered(packet.bundle, BUNDLE_ORDER) as [bucket, v]}
			<section class="bucket">
				<h3 class="bucket-label mono">{label(bucket)}</h3>
				<ul class="entries">
					{#each items(v) as item}
						<li class="entry">
							{#if typeof item === "string"}
								<p class="prose">{item}</p>
							{:else}
								{#if entryText(item)}
									<p class="prose">
										{entryText(item)}
										{#if item.confidence}
											<span class="conf conf-{String(item.confidence)}">{item.confidence}</span>
										{/if}
									</p>
								{/if}
								{#each entryQuotes(item) as q}
									<blockquote class="quote">
										“{q.quote ?? q}”
										{#if quoteHref(q)}
											<a class="cite mono" href={quoteHref(q)}>{q.post_id}</a>
										{/if}
									</blockquote>
								{/each}
								{#if item.applies_when}
									<p class="when"><span class="when-label">applies when</span> {item.applies_when}</p>
								{/if}
								{#if item.does_not_apply_when}
									<p class="when">
										<span class="when-label not">not when</span>
										{item.does_not_apply_when}
									</p>
								{/if}
								{#each entryRest(item) as [k, rv]}
									<p class="when"><span class="when-label">{label(k)}</span> {rv}</p>
								{/each}
							{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/each}
	</div>
{:else}
	<pre class="raw-dump">{JSON.stringify(packet.structured ?? packet.bundle ?? packet, null, 2)}</pre>
{/if}

<style>
	.fields,
	.buckets {
		display: flex;
		flex-direction: column;
		gap: var(--space-md);
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.field-label,
	.bucket-label {
		font-size: 0.7rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		margin: 0;
	}

	.prose {
		font-size: 0.92rem;
		line-height: 1.6;
		color: var(--text-primary);
		margin: 0;
	}

	.bucket {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.entries {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-sm);
	}

	.entry {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.entry + .entry {
		border-top: 1px dashed var(--border-primary);
		padding-top: var(--space-sm);
	}

	.quote {
		margin: 0;
		padding: 4px 12px;
		border-left: 3px solid var(--border-secondary);
		font-size: 0.85rem;
		line-height: 1.5;
		color: var(--text-secondary);
		font-style: italic;
	}

	.cite {
		font-style: normal;
		font-size: 0.72rem;
		margin-left: 8px;
		color: var(--accent-primary);
	}

	.when {
		font-size: 0.8rem;
		line-height: 1.5;
		color: var(--text-secondary);
		margin: 0;
	}

	.when-label {
		display: inline-block;
		font-family: var(--mono);
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--type-distill);
		background: var(--type-distill-soft);
		border-radius: 4px;
		padding: 0 6px;
		margin-right: 4px;
	}

	.when-label.not {
		color: var(--stance-disagree);
		background: #fee2e2;
	}

	.conf {
		display: inline-block;
		font-family: var(--mono);
		font-size: 0.7rem;
		padding: 0 8px;
		border-radius: 999px;
		border: 1px solid var(--border-primary);
		color: var(--text-secondary);
		background: var(--bg-tertiary);
		vertical-align: 2px;
		margin-left: 6px;
	}

	.conf-high {
		color: var(--type-distill);
		background: var(--type-distill-soft);
		border-color: rgba(4, 120, 87, 0.25);
	}

	.conf-low {
		color: var(--brand-amber-dark);
		background: var(--brand-amber-soft);
		border-color: rgba(180, 83, 9, 0.25);
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
</style>
