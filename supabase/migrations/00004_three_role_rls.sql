-- 00004_three_role_rls
-- DB-enforced access model (datasmith's lesson: app-layer read-only leaked, so
-- it revoked the grant). Identities: public=anon, trusted=authenticated,
-- admin=service_role (bypasses RLS). curator added in 00007.
--
--   public  : SELECT only, public set (sessions/agents/packets); NEVER traces
--   trusted : INSERT/UPDATE sessions/agents/packets/traces
--   admin   : full (service_role bypasses RLS)

alter table sessions          enable row level security;
alter table agents            enable row level security;
alter table packets           enable row level security;
alter table traces            enable row level security;
alter table agent_seq_counter enable row level security;

-- Revoke Supabase's default broad anon/authenticated privileges, then re-grant
-- precisely. Table privilege AND RLS policy must both allow (PostgREST checks
-- both) — so the absence of a traces grant to anon is a hard second line.
revoke all on all tables in schema public from anon;
revoke all on all tables in schema public from authenticated;

-- Policies use DROP IF EXISTS + CREATE so a raw single-file re-apply (the
-- datasmith psql pattern) is idempotent (CREATE POLICY has no IF NOT EXISTS
-- in any PG version through 17).

-- public (anon): read-only, public set only. No traces grant at all.
grant select on sessions, agents, packets to anon;
drop policy if exists "public_read" on sessions;
drop policy if exists "public_read" on agents;
drop policy if exists "public_read" on packets;
create policy "public_read" on sessions FOR SELECT TO anon USING (true);
create policy "public_read" on agents   FOR SELECT TO anon USING (true);
create policy "public_read" on packets  FOR SELECT TO anon USING (true);

-- trusted (authenticated): read + write the corpus incl. raw traces.
grant select, insert, update on sessions, agents, packets, traces to authenticated;
drop policy if exists "trusted_read"   on sessions;
drop policy if exists "trusted_write"  on sessions;
drop policy if exists "trusted_update" on sessions;
drop policy if exists "trusted_read"   on agents;
drop policy if exists "trusted_write"  on agents;
drop policy if exists "trusted_update" on agents;
drop policy if exists "trusted_read"   on packets;
drop policy if exists "trusted_write"  on packets;
drop policy if exists "trusted_update" on packets;
drop policy if exists "trusted_read"   on traces;
drop policy if exists "trusted_write"  on traces;
drop policy if exists "trusted_update" on traces;
create policy "trusted_read"   on sessions FOR SELECT TO authenticated USING (true);
create policy "trusted_write"  on sessions FOR INSERT TO authenticated WITH CHECK (true);
create policy "trusted_update" on sessions FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
create policy "trusted_read"   on agents   FOR SELECT TO authenticated USING (true);
create policy "trusted_write"  on agents   FOR INSERT TO authenticated WITH CHECK (true);
create policy "trusted_update" on agents   FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
create policy "trusted_read"   on packets  FOR SELECT TO authenticated USING (true);
create policy "trusted_write"  on packets  FOR INSERT TO authenticated WITH CHECK (true);
create policy "trusted_update" on packets  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
create policy "trusted_read"   on traces   FOR SELECT TO authenticated USING (true);
create policy "trusted_write"  on traces   FOR INSERT TO authenticated WITH CHECK (true);
create policy "trusted_update" on traces   FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- admin (service_role) bypasses RLS; keep explicit grants for self-hosted parity.
grant all on sessions, agents, packets, traces, agent_seq_counter to service_role;

-- next_agent_seq is SECURITY DEFINER (declared in 00001 so re-apply is safe);
-- trusted only needs EXECUTE, not a counter-table grant.
grant execute on function next_agent_seq(text) to authenticated, service_role;
