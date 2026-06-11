-- Break-glass rollback for 00008_public_traces. Lives OUTSIDE
-- supabase/migrations/ on purpose: `make bank-migrate` applies everything in
-- migrations/ in order, so a revoke placed there would auto-apply and undo
-- 00008 immediately. In an incident (a trace body that must leave the public
-- surface NOW):
--
--   1. psql "$MANYAGENT_BANK_DB_URL" -f supabase/rollbacks/00008_revoke_public_traces.sql
--   2. restart the web server with MANYAGENT_WEB_PUBLIC_RAW=0 (the app resolves the
--      switch ONCE at construction — a running server ignores the env change)
--
-- For a single bad trace, prefer the surgical tool: quarantine the parent
-- packet — both the app gates and 00008's policy exclude quarantined bodies.

drop policy if exists "public_read" on traces;
revoke select on traces from anon;
