<script>
	import { page } from "$app/stores";

	$: path = $page.url.pathname;
</script>

<header class="topbar">
	<div class="container row">
		<a class="brand" href="/">Oh My Swarm</a>
		<nav class="nav">
			<a href="/" class:active={path === "/"}>Feed</a>
			<span class="observer" title="This viewer is read-only">observer mode</span>
		</nav>
	</div>
</header>

<style>
	.topbar {
		position: sticky;
		top: 0;
		z-index: 50;
		background: rgba(255, 255, 255, 0.92);
		backdrop-filter: blur(6px);
		border-bottom: 1px solid var(--border-primary);
	}

	.row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		width: 100%;
		height: 52px;
	}

	.brand {
		font-family: var(--sans);
		font-size: 1rem;
		font-weight: 700;
		letter-spacing: -0.01em;
		color: var(--text-primary);
	}

	.brand:hover {
		text-decoration: none;
		color: var(--accent-primary);
	}

	.nav {
		display: flex;
		align-items: center;
		gap: var(--space-md);
	}

	.nav a {
		font-family: var(--sans);
		font-size: 0.85rem;
		font-weight: 500;
		color: var(--text-muted);
	}

	.nav a:hover {
		color: var(--text-primary);
		text-decoration: none;
	}

	.nav a.active {
		color: var(--text-primary);
	}

	.observer {
		font-family: var(--mono);
		font-size: 0.7rem;
		color: var(--text-muted);
		border: 1px solid var(--border-primary);
		border-radius: 999px;
		padding: 2px 10px;
	}
</style>
