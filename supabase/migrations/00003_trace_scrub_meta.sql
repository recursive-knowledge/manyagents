-- 00003_trace_scrub_meta
-- scrub_version + complete drive scripted re-scrub / retro-quarantine backfills
-- without silent overwrite (manyagent.bank.md Operations & recovery; manyagent.capture).

alter table traces add column if not exists scrub_version  text;
alter table traces add column if not exists complete       boolean not null default true;
