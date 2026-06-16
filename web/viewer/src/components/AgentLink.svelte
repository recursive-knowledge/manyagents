<script>
	/** Accent-colored author link → /a/{session}/{tail} (minibook's AgentLink). */
	import { agentLabel } from "$lib/explorer.js";

	/** @type {string} full agent id "{session}/agent-NNN-{adapter}" */
	export let agentId;

	$: [session, tail] = (() => {
		const i = agentId.indexOf("/");
		return i === -1 ? [agentId, agentId] : [agentId.slice(0, i), agentId.slice(i + 1)];
	})();
	$: label = agentLabel(agentId);
</script>

<a
	class="agent mono"
	href="/a/{encodeURIComponent(session)}/{encodeURIComponent(tail)}"
	on:click|stopPropagation
>
	{label}
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
