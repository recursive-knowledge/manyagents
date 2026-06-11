-- Break-glass rollback for 00009_trace_renditions' public grant. Lives
-- OUTSIDE supabase/migrations/ on purpose (see 00008_revoke_public_traces.sql
-- — a revoke placed in migrations/ would auto-apply). Pair with that script
-- in an incident: rendition bodies are derived from the same captured run,
-- so anything that must leave the public surface must leave through both.
-- For a single bad run, prefer quarantining the parent packet — the app
-- gates and both public_read policies exclude quarantined packets.

drop policy if exists "public_read" on trace_renditions;
revoke select on trace_renditions from anon;
