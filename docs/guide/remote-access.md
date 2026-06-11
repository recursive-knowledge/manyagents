# Remote access via Cloudflare Tunnel

Two **named Cloudflare tunnels** expose the local stack on the `formulacode.org`
zone, with independent lifecycles (start/stop one without touching the other):

| `make` target prefix | Hostname                    | Local upstream                        | Serve it with |
| -------------------- | --------------------------- | ------------------------------------- | ------------- |
| `web-tunnel-*`       | `swarms.formulacode.org`    | `127.0.0.1:8580` (read-only viewer)   | `make web-up` |
| `db-tunnel-*`        | `db-swarms.formulacode.org` | `127.0.0.1:54421` (Supabase HTTP API) | `make bank-up`|

A tunnel makes an **outbound** connection from your machine to Cloudflare, so
nothing needs to be port-forwarded and no inbound firewall hole is opened. The
rendered ingress configs land in `infra/cloudflared/` (gitignored); credentials
live in `~/.cloudflared/` and are never committed.

## One-time setup

```bash
make tunnel-install   # fetches the cloudflared binary (Homebrew, or ~/.local/bin)
make tunnel-login     # opens a browser; authorize the formulacode.org zone
```

`tunnel-login` writes `~/.cloudflared/cert.pem` (the per-zone cert that lets
`cloudflared` create tunnels and DNS records). Run it once per machine.

## Bring a tunnel up

Each `*-tunnel-create` is idempotent — it creates the named tunnel if absent,
(re)writes `infra/cloudflared/<name>.yml`, and routes the hostname's DNS CNAME:

```bash
make web-tunnel-create   # -> swarms.formulacode.org
make db-tunnel-create    # -> db-swarms.formulacode.org
```

Then run each tunnel in the foreground (one terminal each; the upstream service
must already be running):

```bash
make web-up &  make web-tunnel-run      # website
make bank-up &  make db-tunnel-run      # database API
```

`tunnel-run` is foreground (Ctrl-C stops it). To run a tunnel as a background
service that survives logout, use cloudflared's own service installer instead:
`cloudflared --config infra/cloudflared/swarms-web.yml service install`.

Check state any time with `make tunnel-list`.

## ⚠ TLS: why the DB host is `db-swarms`, not `db.swarms`

Cloudflare's **free Universal SSL only covers one subdomain level**
(`formulacode.org` and `*.formulacode.org`). `swarms.formulacode.org` is covered.
A **two-level** name (`db.swarms.*`) is **not** — the tunnel routes traffic but
the edge fails the TLS handshake (`sslv3 alert handshake failure` /
`ERR_SSL_VERSION_OR_CIPHER_MISMATCH`). So the Bank tunnel uses the single-level
**`db-swarms.formulacode.org`** (the default `DB_HOSTNAME`), which Universal SSL
covers out of the box.

If you specifically want the nested `db.swarms.*` form, set `DB_HOSTNAME`
accordingly and either **enable Total TLS** (SSL/TLS → Edge Certificates —
auto-issues a per-hostname cert) or **buy an Advanced Certificate** for
`*.swarms.formulacode.org` (Advanced Certificate Manager).

## ⚠ Security: do not expose the database openly

The two endpoints have very different exposure:

- **`swarms.formulacode.org` is safe to expose publicly.** The web tier holds
  only the read-only anon key and the grant is DB-enforced (RLS) — it is
  structurally incapable of mutating the corpus (see [Viewer & read API](viewer.md)).

- **`db-swarms.formulacode.org` is the raw Supabase API and must be gated.** The
  local Bank started by `make bank-up` uses Supabase's **default demo JWT secret**
  (`supabase/config.toml` does not set `signing_keys_path`), so its `service_role`
  key is *publicly known*. Exposed openly, anyone could present that key and
  bypass RLS for full read/write. Before pointing `db.` at the internet, do
  **both** of:
    1. Put **Cloudflare Access** in front of `db-swarms.formulacode.org` (Zero
       Trust → Access → Applications) so only authenticated callers reach it —
       this is the posture the sibling `datasmith` project settled on.
    2. Rotate the JWT signing keys (set `signing_keys_path` in
       `supabase/config.toml`, `make bank-reset`) so the default `service_role`
       key no longer validates, and update `MANYAGENT_BANK_*` in `manyagent.env`.

## Point a remote `manyagent` at the Bank

Once `db.` is up (and gated), a machine elsewhere talks to the Bank over HTTPS by
overriding the connection tunables (`manyagent.utils`, documented in `manyagent.env.example`):

```bash
export MANYAGENT_BANK_URL=https://db-swarms.formulacode.org
export MANYAGENT_BANK_ANON_KEY=...      # the anon key for your (rotated) stack
export MANYAGENT_BANK_TRUSTED_KEY=...   # only on writers
python -m manyagent.preflight           # validates env + Bank reachability + keys
```

## Tear down

```bash
make web-tunnel-delete
make db-tunnel-delete
```

This stops and deletes the named tunnel and removes its local config. The DNS
CNAME is **not** removed automatically — delete it in the Cloudflare dashboard if
the hostname is no longer needed.
