/**
 * Shared search query store.
 *
 * The search field lives in the (collapsible) SiteHeader so it is available on
 * every page; pages that want to filter subscribe to this store rather than
 * owning their own input. Reset to "" whenever a consumer wants a clean slate.
 */
import { writable } from "svelte/store";

export const searchQuery = writable("");
