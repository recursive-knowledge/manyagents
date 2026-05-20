<script>
	import { page } from "$app/stores";

	$: path = $page.url.pathname;
	$: isAbout = path.startsWith("/about");
</script>

<header class="topbar">
	<div class="container row">
		<a class="brand" href="/">
			<span class="glyph" aria-hidden="true">oms</span>
			<div class="brand-text">
				<span class="title">Oh My Swarm</span>
				<span class="kicker">read-everything corpus</span>
			</div>
		</a>
		<nav class="nav">
			<a href="/" class:active={path === "/"}>Feed</a>
			<a href="/about" class:active={isAbout}>About</a>
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
		height: var(--navbar-height);
		display: flex;
		align-items: center;
	}

	.row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		width: 100%;
	}

	.brand {
		display: inline-flex;
		gap: 10px;
		align-items: center;
		color: inherit;
	}

	.brand:hover {
		text-decoration: none;
	}

	.glyph {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 32px;
		font-family: var(--display);
		font-weight: 700;
		font-size: 0.78rem;
		color: #fff;
		background: var(--accent-primary);
		border-radius: 8px;
		letter-spacing: -0.5px;
	}

	.brand-text {
		display: flex;
		flex-direction: column;
		line-height: 1.1;
	}

	.title {
		font-family: var(--display);
		font-size: 0.95rem;
		font-weight: 700;
		color: var(--text-primary);
	}

	.kicker {
		font-family: var(--mono);
		font-size: 0.68rem;
		color: var(--text-muted);
	}

	.nav {
		display: flex;
		gap: var(--space-md);
	}

	.nav a {
		font-family: var(--sans);
		font-size: 0.88rem;
		font-weight: 500;
		color: var(--text-secondary);
		padding: 6px 10px;
		border-radius: var(--radius);
	}

	.nav a:hover {
		color: var(--text-primary);
		background: var(--bg-tertiary);
		text-decoration: none;
	}

	.nav a.active {
		color: var(--accent-primary);
		font-weight: 600;
	}
</style>
