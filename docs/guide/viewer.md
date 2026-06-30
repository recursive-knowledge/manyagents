# Viewer & read API

`make web-up` serves the read-only API plus a static viewer (anon / `public`
identity). The web tier holds only the Bank's read-only key and the grant is
**DB-enforced** — it is structurally incapable of mutating the corpus.

Routes (every payload is the canonical `KnowledgePacket` shape):

| Route | Returns |
|---|---|
| `GET /s/{session}` | session metadata + cursor-paginated packets |
| `GET /s/{session}?p={uuid}` | one `KnowledgePacket` (the exact URL the curator prints; a bundle resolves at `/s/curator?p=<hex>`) |
| `GET /s/{session}/agents` | session agents with a derived activity span |
| `GET /api/packets?type=&since=&limit=&cursor=` | corpus-wide packet stream |
| `GET /api/reuse?goal=&since=&limit=&cursor=` | the behavioral reuse signal for researchers (paginated) |

Two guarantees are load-bearing and tested:

- The anon API **never** returns a raw trace body, even with `?include=raw`
  (silently ignored — raw is outside the anon grant at the database). Only a
  `trusted`/`admin` key may fetch a trace body.
- A quarantined packet stays **visible but flagged** (`quarantined: true`) and
  is excluded from the reuse / "use as context" affordance.

The v1 viewer is a static page that talks only to this API; the API contract
is frozen, so a richer frontend is a future re-skin, not a re-spec.
