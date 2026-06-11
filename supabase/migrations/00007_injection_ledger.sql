-- 00007_injection_ledger
-- The reuse ledger + recomputable reuse_score view (the load-bearing curation
-- signal) + the narrow curator identity (manyagent.bank.md:60,72-74,44).

create table if not exists injections (
    packet_id          text not null references packets(id),
    target_session_id  text not null references sessions(id),
    injected_at        timestamptz not null default now(),
    primary key (packet_id, target_session_id)
);

alter table injections enable row level security;
grant select on injections to anon;                    -- public behavioral signal
grant select, insert on injections to authenticated;   -- /inject writes as trusted
grant all on injections to service_role;
drop policy if exists "public_read"  on injections;
drop policy if exists "trusted_read"  on injections;
drop policy if exists "trusted_write" on injections;
create policy "public_read"  on injections FOR SELECT TO anon USING (true);
create policy "trusted_read"  on injections FOR SELECT TO authenticated USING (true);
create policy "trusted_write" on injections FOR INSERT TO authenticated WITH CHECK (true);

-- curator: deliberately narrow. Reads posts, writes distill bundles. No traces
-- grant at all (cannot read raw), no DELETE policy (cannot mutate the corpus).
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'curator') then
        create role curator nologin;
    end if;
end $$;
grant usage on schema public to curator;
grant select on packets to curator;
grant insert on packets to curator;
-- admin (postgres/service_role) ⊇ curator: lets the admin role assume curator
-- (manyagent.bank.md "admin: full oversight; curation"). Idempotent.
grant curator to postgres;
grant curator to service_role;
drop policy if exists "curator_read_posts"    on packets;
drop policy if exists "curator_write_distill" on packets;
create policy "curator_read_posts"    on packets FOR SELECT TO curator USING (type = 'post');
create policy "curator_write_distill" on packets FOR INSERT TO curator WITH CHECK (type = 'distill');

-- reuse_score: a VIEW, not a stored field — recomputable so weighting improves
-- retroactively without re-curation. For a packet: aggregate over the sessions
-- it was injected into, weighted by each session's later outcome (best post
-- rating, or a bonus if a distill in that session was accepted).
create or replace view reuse_score as
with target_outcome as (
    select s.id as session_id,
           greatest(
               coalesce((select max(p.rating) from packets p
                         where p.session_id = s.id and p.type = 'post'
                           and p.rating is not null), 0),
               coalesce((select case when bool_or(p.preference = 'accept') then 4 else 0 end
                         from packets p
                         where p.session_id = s.id and p.type = 'distill'), 0)
           ) as outcome
    from sessions s
)
select p.id as packet_id,
       count(distinct i.target_session_id) as inject_count,
       coalesce(sum(t.outcome), 0)::numeric as reuse_score
from packets p
left join injections i on i.packet_id = p.id
left join target_outcome t on t.session_id = i.target_session_id
group by p.id;

grant select on reuse_score to anon, authenticated, service_role;
