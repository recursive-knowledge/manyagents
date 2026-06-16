<script>
	import { onMount, tick } from "svelte";
	import { searchQuery } from "$lib/search.js";

	// Transparent at the top of the page; gains a translucent backdrop and
	// shrinks once the reader scrolls. Pure presentation — no nav, no badges.
	let scrolled = false;
	let searchOpen = false;
	let inputEl;

	function onScroll() {
		scrolled = window.scrollY > 8;
	}

	async function toggleSearch() {
		searchOpen = !searchOpen;
		if (searchOpen) {
			await tick();
			inputEl?.focus();
		}
	}

	function onBlur() {
		// Collapse only when empty, so a live query stays reachable.
		if (!$searchQuery.trim()) searchOpen = false;
	}

	function onKeydown(e) {
		if (e.key === "Escape") {
			searchQuery.set("");
			searchOpen = false;
			inputEl?.blur();
		}
	}

	onMount(() => {
		onScroll();
		window.addEventListener("scroll", onScroll, { passive: true });
		return () => window.removeEventListener("scroll", onScroll);
	});
</script>

<header class="topbar" class:scrolled>
	<div class="bar">
		<span class="spacer" aria-hidden="true"></span>
		<a class="brand" href="/">ManyAgent</a>
		<div class="tools">
			<div class="search" class:open={searchOpen}>
				<input
					bind:this={inputEl}
					type="search"
					placeholder="Search…"
					bind:value={$searchQuery}
					on:blur={onBlur}
					on:keydown={onKeydown}
				/>
				<button
					class="search-btn"
					title="Search"
					aria-label="Search"
					on:click={toggleSearch}
				>
					⌕
				</button>
			</div>
		</div>
	</div>
</header>

<style>
	.topbar {
		position: sticky;
		top: 0;
		z-index: 50;
		background: transparent;
		transition:
			background 160ms ease,
			border-color 160ms ease,
			box-shadow 160ms ease;
		border-bottom: 1px solid transparent;
	}

	.topbar.scrolled {
		background: rgba(255, 255, 255, 0.82);
		backdrop-filter: blur(8px);
		border-bottom-color: var(--border-primary);
	}

	.bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-md);
		width: 100%;
		max-width: 1024px;
		margin: 0 auto;
		padding: 0 var(--space-md);
		height: 64px;
		transition: height 160ms ease;
	}

	.topbar.scrolled .bar {
		height: 48px;
	}

	/* Three-column flex so the brand stays optically centered while the search
	   tool sits at the right. */
	.spacer,
	.tools {
		flex: 1 1 0;
		display: flex;
		align-items: center;
	}

	.tools {
		justify-content: flex-end;
	}

	.brand {
		flex: 0 0 auto;
		font-family: var(--sans);
		font-size: 1.05rem;
		font-weight: 700;
		letter-spacing: -0.01em;
		color: var(--text-primary);
		text-align: center;
	}

	.brand:hover {
		text-decoration: none;
		color: var(--accent-primary);
	}

	.search {
		display: flex;
		align-items: center;
	}

	.search input {
		width: 0;
		padding: 0;
		border: 1px solid transparent;
		border-radius: 999px;
		font-family: var(--sans);
		font-size: 0.82rem;
		background: var(--bg-primary);
		outline: none;
		opacity: 0;
		transition:
			width 180ms ease,
			padding 180ms ease,
			opacity 180ms ease;
	}

	.search.open input {
		width: 180px;
		padding: 5px 12px;
		border-color: var(--border-primary);
		opacity: 1;
	}

	.search.open input:focus {
		border-color: var(--accent-primary);
		box-shadow: 0 0 0 3px rgba(67, 56, 202, 0.18);
	}

	.search-btn {
		flex: 0 0 auto;
		width: 32px;
		height: 32px;
		margin-left: 4px;
		border-radius: 999px;
		font-size: 1.1rem;
		line-height: 1;
		color: var(--text-muted);
		background: transparent;
	}

	.search-btn:hover {
		color: var(--text-primary);
		background: var(--bg-tertiary);
	}

	@media (max-width: 640px) {
		.search.open input {
			width: 120px;
		}
	}
</style>
