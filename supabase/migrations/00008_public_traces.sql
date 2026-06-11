-- 00008_public_traces
-- Pre-alpha decision (2026-06-10; oms.web.md Decision log): the viewer is a
-- PUBLIC window over the whole corpus, including scrubbed raw trace bodies —
-- the captured trajectory is the product being demonstrated, and oms.capture
-- scrubs (v1) before persist so credential-shaped content never lands in the
-- Bank in the first place. This deliberately widens 00004's "public: NEVER
-- traces" stance for the pre-alpha. Two-layer rollback: the app keeps a kill
-- switch (OMS_WEB_PUBLIC_RAW=0 restores the trusted-only gate) and revoking
-- this grant is the DB-level reversal (a future migration, never an edit to
-- this one).

-- Quarantine-aware: retro-quarantine (the scrub leak-recovery seam — bump
-- SCRUB_VERSION, re-scrub, quarantine rows the old scrub missed) must pull a
-- body from the public surface, so the policy joins the parent packet. The
-- app layer enforces the same rule (api.py); both layers agree in both
-- directions. Break-glass rollback: supabase/rollbacks/00008_revoke_public_traces.sql.

grant select on traces to anon;
drop policy if exists "public_read" on traces;
create policy "public_read" on traces FOR SELECT TO anon
  USING (exists (select 1 from packets p where p.id = packet_id and not p.quarantined));
