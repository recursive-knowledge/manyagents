-- 00009_trace_renditions
-- M13.0 (Trace Renditions & Mining design doc §4a): derived projections of a
-- captured run, keyed (packet_id, format). First format: 'harness' — the
-- conversation mined from the agent harness's own local transcript
-- (~/.claude/projects/<munged-cwd>/<session-id>.jsonl), bound to the run by
-- the oms._hook lifecycle records. Upsert on the PK makes re-mining
-- idempotent (the oms.distill re-run discipline). Widening the format check
-- (e.g. a stored 'cast' in M12 proper) is a new migration, never an edit.
-- Break-glass rollback: supabase/rollbacks/00009_revoke_public_renditions.sql.

create table if not exists trace_renditions (
  packet_id     text not null references packets(id),
  format        text not null check (format in ('harness')),
  body          text not null,
  miner_version text,
  complete      boolean not null default true,
  created_at    timestamptz not null default now(),
  primary key (packet_id, format)
);

alter table trace_renditions enable row level security;

-- public: the pre-alpha stance + quarantine join, exactly as 00008's traces
-- policy — retro-quarantining the parent packet pulls every projection of it
-- from the public surface at the database layer too.
grant select on trace_renditions to anon;
drop policy if exists "public_read" on trace_renditions;
create policy "public_read" on trace_renditions FOR SELECT TO anon
  USING (exists (select 1 from packets p where p.id = packet_id and not p.quarantined));

-- trusted: read + write (the wrapper mines and upserts at run end).
grant select, insert, update on trace_renditions to authenticated;
drop policy if exists "trusted_read"   on trace_renditions;
drop policy if exists "trusted_write"  on trace_renditions;
drop policy if exists "trusted_update" on trace_renditions;
create policy "trusted_read"   on trace_renditions FOR SELECT TO authenticated USING (true);
create policy "trusted_write"  on trace_renditions FOR INSERT TO authenticated WITH CHECK (true);
create policy "trusted_update" on trace_renditions FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- admin (service_role) bypasses RLS; explicit grant for self-hosted parity.
grant all on trace_renditions to service_role;
