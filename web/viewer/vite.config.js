import { sveltekit } from "@sveltejs/kit/vite";
import path from "node:path";

// The FastAPI read API (`make web-up`) binds 127.0.0.1:8580 (see Makefile).
// During `npm run dev`, proxy the API surface so the viewer can talk to it
// without CORS or hardcoded origins.
const API_TARGET = process.env.OMS_API_TARGET ?? "http://127.0.0.1:8580";

const config = {
	plugins: [sveltekit()],
	resolve: {
		alias: {
			$components: path.resolve("./src/components"),
			$lib: path.resolve("./src/lib"),
			$styles: path.resolve("./src/styles")
		}
	},
	server: {
		port: 5173,
		strictPort: false,
		proxy: {
			"/api": { target: API_TARGET, changeOrigin: true },
			"/s": { target: API_TARGET, changeOrigin: true },
			"/healthz": { target: API_TARGET, changeOrigin: true }
		}
	}
};

export default config;
