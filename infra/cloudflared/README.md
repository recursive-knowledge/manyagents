# `infra/cloudflared/` — Cloudflare Tunnel configs

Holds the rendered ingress configs for the two named tunnels that expose the
local stack on the `formulacode.org` zone. Driven entirely by the `*-tunnel-*`
targets in the repo `Makefile`; see `docs/guide/remote-access.md` for the full
walkthrough.

| Tunnel       | Hostname                     | Local upstream                       | Started by      |
| ------------ | ---------------------------- | ------------------------------------ | --------------- |
| `swarms-web` | `swarms.formulacode.org`     | `127.0.0.1:8580` (read-only viewer)  | `make web-up`   |
| `swarms-db`  | `db-swarms.formulacode.org`  | `127.0.0.1:54421` (Supabase HTTP API)| `make bank-up`  |

## What lives here

- **`README.md`** (this file) — committed.
- **`<tunnel>.yml`** — *generated* by `make {web,db}-tunnel-create`, **gitignored**.
  Machine-specific: each references a tunnel UUID and the per-host credentials
  file under `~/.cloudflared/<UUID>.json`.

Credentials (`~/.cloudflared/<UUID>.json`) and the zone cert
(`~/.cloudflared/cert.pem`) are **secrets** and live in your home directory,
never in the repo.

## Lifecycle

```bash
make tunnel-install        # one-time: fetch the cloudflared binary
make tunnel-login          # one-time: browse + authorize the formulacode.org zone

make web-tunnel-create     # create tunnel + write swarms-web.yml + route DNS
make db-tunnel-create      # create tunnel + write swarms-db.yml  + route DNS

make web-tunnel-run        # foreground; needs `make web-up`  running
make db-tunnel-run         # foreground; needs `make bank-up` running

make web-tunnel-delete     # tear a tunnel down (DNS record removed manually)
make db-tunnel-delete
```

> **TLS note:** the DB host is single-level (`db-swarms`, not `db.swarms`) on
> purpose — Cloudflare's free Universal SSL covers only one subdomain level, so a
> two-level `db.swarms.*` would fail the TLS handshake. `db-swarms.formulacode.org`
> and `swarms.formulacode.org` both work out of the box.
