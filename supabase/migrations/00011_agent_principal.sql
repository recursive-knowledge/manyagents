-- 00011_agent_principal
-- Persistent cross-goal agent identity. principal_id stamps every agents row
-- with the operator's stable per-(machine, adapter) principal (minted client-
-- side as a UUID4; manyagent.cli._principal_for, persisted in
-- ~/.manyagent/principals.json). The agent *id* scheme is unchanged — principal
-- is a column, not part of the id — so packet ids / FK topology / the 00010
-- prefix rewrite are untouched.
--
-- Nullable: every pre-existing row predates the column and stays NULL. The
-- viewer degrades to the session-scoped agent view for NULL principals
-- (manyagent.web.md / manyagent.bank.md Decision logs). No new RLS: the agents
-- table already grants select to anon/authenticated with USING (true) policies
-- (00004), and a new column inherits the table's row-level policies + grants.

alter table agents add column if not exists principal_id text;
create index if not exists agents_principal_idx on agents (principal_id);
