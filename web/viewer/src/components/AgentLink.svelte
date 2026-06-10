<script>
	/** Accent-colored author link → /a/{session}/{tail} (minibook's AgentLink). */

	/** @type {string} full agent id "{session}/agent-NNN-{adapter}" */
	export let agentId;

	$: [session, tail] = (() => {
		const i = agentId.indexOf("/");
		return i === -1 ? [agentId, agentId] : [agentId.slice(0, i), agentId.slice(i + 1)];
	})();
</script>

<a
	class="agent mono"
	href="/a/{encodeURIComponent(session)}/{encodeURIComponent(tail)}"
	on:click|stopPropagation
>
	@{tail}
</a>

<style>
	.agent {
		font-size: 0.74rem;
		font-weight: 500;
		color: var(--accent-primary);
	}

	.agent:hover {
		text-decoration: underline;
	}
</style>
