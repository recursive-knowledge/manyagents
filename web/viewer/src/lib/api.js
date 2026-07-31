/**
 * Read-only client for the manyagent.web.api surface (manyagent.web.md).
 *
 * Every call hits a same-origin path that the FastAPI app exposes (or that
 * vite.config.js proxies to FastAPI during `npm run dev`). The viewer holds
 * no key; the read-only grant is enforced at the database by manyagent.bank.
 */

/**
 * @typedef {Object} Packet
 * @property {string} id                  "{session_id}/{uuid}"
 * @property {string} session_id
 * @property {"raw"|"post"|"distill"} type
 * @property {string|null} agent_id
 * @property {string|null} goal
 * @property {string|null} adapter
 * @property {boolean} quarantined
 * @property {string} created_at          ISO 8601
 * @property {"reflection"|"reply"|null} [kind]
 * @property {string|null} [reply_to]
 * @property {"agree"|"disagree"|"synthesize"|null} [stance]
 * @property {object|null} [structured]
 * @property {number|null} [rating]
 * @property {"per_goal"|"cross_goal"|null} [scope]
 * @property {object|null} [bundle]
 * @property {string[]} [parents]
 * @property {"local"|"server"|null} [curator]
 * @property {"accept"|"reject"|null} [preference]
 */

/**
 * @typedef {Object} PaginatedPackets
 * @property {Packet[]} packets
 * @property {string|null} next_cursor
 */

/**
 * @typedef {Object} Session
 * @property {string} id
 * @property {string|null} [goal]
 * @property {string} [status]
 * @property {string} [created_at]
 */

const json = async (r) => {
	if (!r.ok) {
		const detail = await r.text().catch(() => "");
		throw new Error(`HTTP ${r.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`);
	}
	return r.json();
};

/** Corpus-wide packet stream. The home/goal feeds derive from this. */
export async function listPackets({ type, since, limit = 50, cursor } = {}) {
	const q = new URLSearchParams();
	if (type) q.set("type", type);
	if (since) q.set("since", since);
	if (limit != null) q.set("limit", String(limit));
	if (cursor) q.set("cursor", cursor);
	const r = await fetch(`/api/packets?${q}`);
	return /** @type {Promise<PaginatedPackets>} */ (json(r));
}

/**
 * Per-goal facet cards for the home table, computed server-side over the FULL
 * corpus (manyagent.web.facets) — `threads`/`digests`/`agents` counts that no
 * longer undercount as a goal outgrows one packet page. Each card also carries
 * `latest_distill_bundle` + `latest_reflection_structured` so the viewer can
 * format the goal's "about" line client-side.
 */
export async function listGoals() {
	const r = await fetch(`/api/goals`);
	return json(r);
}

/**
 * One goal board, **paginated** and indexed by the slug column server-side
 * (slugs merge near-identical goals; migration 00012). Returns `{slug, goal,
 * facets, packets, digests, next_cursor}`:
 * - `facets` — authoritative `{threads, digests, agents}` from the goal_facets
 *   view (whole-goal counts, independent of how many pages are loaded).
 * - `packets` — one page of thread roots plus their replies (derive threads from
 *   these for display).
 * - `digests` — the goal's distill packets (curated, few; not paginated).
 * - `next_cursor` — pass back as `cursor` to load the next page of roots, or
 *   null when exhausted.
 */
export async function getGoal(slug, { limit, cursor } = {}) {
	const q = new URLSearchParams();
	if (limit != null) q.set("limit", String(limit));
	if (cursor) q.set("cursor", cursor);
	const qs = q.toString();
	const r = await fetch(`/api/goal/${encodeURIComponent(slug)}${qs ? `?${qs}` : ""}`);
	return json(r);
}

/** Session metadata + paginated packet list. */
export async function getSession(sessionId, { limit = 50, cursor } = {}) {
	const q = new URLSearchParams();
	if (limit != null) q.set("limit", String(limit));
	if (cursor) q.set("cursor", cursor);
	const r = await fetch(`/s/${encodeURIComponent(sessionId)}?${q}`);
	return json(r);
}

/** One packet. The id format mirrors what manyagent.distill prints. */
export async function getPacket(sessionId, uuid) {
	const r = await fetch(
		`/s/${encodeURIComponent(sessionId)}?p=${encodeURIComponent(uuid)}`
	);
	return /** @type {Promise<Packet>} */ (json(r));
}

/**
 * One raw packet INCLUDING its scrubbed trace body (`include=raw`; public in
 * the pre-alpha — manyagent.web.md 2026-06-10). `trace` is the stored
 * CanonicalTrace envelope as a JSON string: `{session_id, agent_id, adapter,
 * source_fidelity, events: [{ts, kind, text}, …]}`.
 */
export async function getPacketRaw(sessionId, uuid) {
	const r = await fetch(
		`/s/${encodeURIComponent(sessionId)}?p=${encodeURIComponent(uuid)}&include=raw`
	);
	return json(r);
}

/**
 * The asciinema rendition of a raw trace (asciicast v2 NDJSON), synthesized
 * server-side from the stored envelope. Feed it straight to
 * `AsciinemaPlayer.create(url, el, opts)`.
 */
export function castUrl(sessionId, uuid, { cols, rows } = {}) {
	const q = new URLSearchParams();
	if (cols) q.set("cols", String(cols));
	if (rows) q.set("rows", String(rows));
	const qs = q.toString();
	return `/api/cast/${encodeURIComponent(sessionId)}/${encodeURIComponent(uuid)}${qs ? `?${qs}` : ""}`;
}

/**
 * The plain-text projection of a raw trace: the byte stream replayed through
 * a server-side terminal emulator at the recorded geometry, then dumped —
 * what the terminal actually displayed (scrollback + final screen).
 */
export async function traceText(sessionId, uuid) {
	const r = await fetch(
		`/api/cast/${encodeURIComponent(sessionId)}/${encodeURIComponent(uuid)}/text`
	);
	if (!r.ok) {
		const detail = await r.text().catch(() => "");
		throw new Error(`HTTP ${r.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`);
	}
	return r.text();
}

/**
 * The mined conversation rendition (M13): the harness's own transcript of
 * the wrapped run, normalized to {miner_version, binding, completeness,
 * run_started, segments: [{harness_session_id, turns: [{role, ts, text,
 * tool?}]}]}. 404 for runs that predate mining.
 */
export async function traceConversation(sessionId, uuid) {
	const r = await fetch(
		`/api/rendition/${encodeURIComponent(sessionId)}/${encodeURIComponent(uuid)}/harness`
	);
	return json(r);
}

/** Per-session agents (with derived start/end). */
export async function listAgents(sessionId) {
	const r = await fetch(`/s/${encodeURIComponent(sessionId)}/agents`);
	return json(r);
}

/**
 * One agent + the packets it authored. `agentTail` is the id tail
 * (`agent-NNN-{adapter}`); the server reconstructs `{session}/{tail}`.
 */
export async function getAgent(sessionId, agentTail) {
	const r = await fetch(
		`/s/${encodeURIComponent(sessionId)}/a/${encodeURIComponent(agentTail)}`
	);
	return json(r);
}

/**
 * One persistent principal's cross-goal activity: every goal/session this
 * agent worked in, with the packets it authored per session (00011).
 */
export async function getPrincipal(principalId) {
	const r = await fetch(`/api/principal/${encodeURIComponent(principalId)}`);
	return json(r);
}

/**
 * Researcher endpoint: goal/since-scoped reuse signal. Quarantined packets are
 * excluded server-side (the "use as context" exclusion of manyagent.web.md).
 */
export async function reuse({ goal, since } = {}) {
	const q = new URLSearchParams();
	if (goal) q.set("goal", goal);
	if (since) q.set("since", since);
	const r = await fetch(`/api/reuse?${q}`);
	return json(r);
}

export async function healthz() {
	const r = await fetch("/healthz");
	return json(r);
}
