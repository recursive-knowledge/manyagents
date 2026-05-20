-- 00006_swarms_taxonomy
-- The raw|post|distill taxonomy + forum/curator subfields + soft goal scope
-- (oma.bank.md:59, oma.core.md Packet). DB CHECKs mirror the oma.core
-- validators as defense-in-depth. Constraints are guarded so the migration is
-- idempotent under a raw re-apply.

alter table packets add column if not exists kind        text;       -- reflection|reply
alter table packets add column if not exists reply_to    text;       -- parent post id (reply)
alter table packets add column if not exists stance      text;       -- agree|disagree|synthesize
alter table packets add column if not exists structured  jsonb;      -- falsifiable post-mortem
alter table packets add column if not exists rating      smallint;   -- 1..5 | null (unrated valid)
alter table packets add column if not exists scope       text;       -- per_goal|cross_goal
alter table packets add column if not exists bundle      jsonb;      -- 6 Insight buckets
alter table packets add column if not exists parents     text[] not null default '{}';
alter table packets add column if not exists curator     text;       -- local|server
alter table packets add column if not exists goal        text;       -- soft scope (null=ungoaled)
alter table sessions add column if not exists goal       text;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'packets_type_chk') then
        alter table packets add constraint packets_type_chk
            check (type in ('raw', 'post', 'distill'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'packets_rating_chk') then
        alter table packets add constraint packets_rating_chk
            check (rating is null or (rating between 1 and 5));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'packets_kind_chk') then
        alter table packets add constraint packets_kind_chk
            check (kind is null or kind in ('reflection', 'reply'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'packets_stance_chk') then
        alter table packets add constraint packets_stance_chk
            check (stance is null or stance in ('agree', 'disagree', 'synthesize'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'packets_scope_chk') then
        alter table packets add constraint packets_scope_chk
            check (scope is null or scope in ('per_goal', 'cross_goal'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'packets_curator_chk') then
        alter table packets add constraint packets_curator_chk
            check (curator is null or curator in ('local', 'server'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'packets_preference_chk') then
        alter table packets add constraint packets_preference_chk
            check (preference is null or preference in ('accept', 'reject'));
    end if;
    -- A reply must carry reply_to + stance; a distill must carry scope + bundle.
    if not exists (select 1 from pg_constraint where conname = 'packets_reply_shape_chk') then
        alter table packets add constraint packets_reply_shape_chk
            check (kind is distinct from 'reply'
                   or (reply_to is not null and stance is not null));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'packets_distill_shape_chk') then
        alter table packets add constraint packets_distill_shape_chk
            check (type <> 'distill'
                   or (scope is not null and bundle is not null));
    end if;
end $$;

create index if not exists packets_goal_idx on packets (goal);
create index if not exists sessions_goal_idx on sessions (goal);
