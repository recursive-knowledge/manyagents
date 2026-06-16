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
		<span class="muted"><a href="https://github.com/manyagent/manyagent"><code>github.com/manyagent/manyagent</code></a></span>
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
		background: transparent;
	}

	.site-foot .container {
		text-align: center;
		font-size: 0.74rem;
		color: var(--text-muted);
	}

	.site-foot code {
		font-family: var(--mono);
		font-size: 0.72rem;
	}
</style>
