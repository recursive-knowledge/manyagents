import adapterStatic from "@sveltejs/adapter-static";
import sveltePreprocess from "svelte-preprocess";
import autoprefixer from "autoprefixer";

const preprocess = sveltePreprocess({
	postcss: { plugins: [autoprefixer] }
});

const config = {
	preprocess,
	kit: {
		// Single-page-app mode: the dynamic routes (/s/[session], /g/[goal])
		// are populated at runtime from the read API. `fallback: "index.html"`
		// makes the static adapter emit the SPA shell under `index.html` so
		// FastAPI's `StaticFiles(html=True)` mount serves it for `/`, and the
		// SPA-negotiation middleware in `oms.web.server.build_app` serves it
		// for `/s/*`, `/g/*`, `/about` HTML requests (the read API's
		// `/s/{session}` JSON route still wins for programmatic callers).
		adapter: adapterStatic({ fallback: "index.html" }),
		prerender: { entries: [] }
	}
};

export default config;
