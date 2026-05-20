// SPA mode: dynamic routes (/s/[session], /g/[goal]) are populated at runtime
// from the read API. SSR/prerender are off so adapter-static emits the
// `200.html` fallback shell for every path (svelte.config.js).
export const ssr = false;
export const prerender = false;
