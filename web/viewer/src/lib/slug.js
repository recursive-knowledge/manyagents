// Goal-slug codec — the URL-normalized form of a goal label.
//
// MUST stay byte-for-byte identical to `src/manyagent/utils/slug.py` (`slugify`):
// the CLI builds its `open:` link with the Python version, and the goal board
// matches packets against the URL slug with this one. The slug is a *derived*
// match key, never an identity — two near-identical goals intentionally share a
// board, and the display name is recovered from the matched packets.

const MAX_CHARS = 80;
const UNGOALED = "ungoaled";

/**
 * URL-normalize a goal label to a stable, ≤80-char slug.
 *
 * Lowercase; every run of non-`[a-z0-9]` chars collapses to a single `-`;
 * leading/trailing `-` stripped; truncated to 80 chars, then any `-` left
 * dangling by the cut removed. `null`/`undefined`/blank/all-punctuation →
 * `"ungoaled"` (mirrors the `goal ?? "(ungoaled)"` grouping, and
 * `slugify("(ungoaled)") === "ungoaled"`). Non-ASCII letters drop out.
 *
 * @param {string|null|undefined} goal
 * @returns {string}
 */
export function slugify(goal) {
	if (goal === null || goal === undefined) return UNGOALED;
	let s = String(goal).trim().toLowerCase().replace(/[^a-z0-9]+/g, "-");
	s = s.replace(/^-+/, "").replace(/-+$/, "");
	s = s.slice(0, MAX_CHARS).replace(/-+$/, "");
	return s || UNGOALED;
}
