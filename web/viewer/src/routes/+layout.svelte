<script>
	import "$styles/app.css";
	import SiteHeader from "$components/SiteHeader.svelte";
</script>

<SiteHeader />
<main class="main">
	<slot />
</main>
<footer class="site-foot">
	<div class="container">
		<span class="muted">
			Read API: <code>oms.web</code> · grant enforced at the database
			(oms.bank migration 00004).
		</span>
		<span class="muted right">
			<a href="/about">About</a> · <a href="https://github.com/" rel="noopener">source</a>
		</span>
	</div>
</footer>

<style>
	.main {
		flex: 1;
		display: flex;
		flex-direction: column;
	}

	.site-foot {
		padding: var(--space-md) 0;
		border-top: 1px solid var(--border-primary);
		background: var(--bg-primary);
	}

	.site-foot .container {
		display: flex;
		justify-content: space-between;
		gap: var(--space-md);
		font-size: 0.78rem;
		color: var(--text-muted);
		flex-wrap: wrap;
	}

	.site-foot code {
		font-family: var(--mono);
		font-size: 0.74rem;
	}

	.right {
		margin-left: auto;
	}
</style>
