-- 00010_uuid_session_ids
-- One-shot backfill: rewrite every Crockford-Base32 session id (XXXX-XXXX) to a
-- fresh UUID (stored as text; no column type change). The codec switched to
-- UUID4 in manyagent.utils.sid (2026-06-13 Decision log); storage was always
-- format-agnostic (session id is opaque `text`; manyagent.capture.conformance only
-- forbids `/`, which UUIDs honor), so NEW sessions needed no migration — this
-- file uniformises the EXISTING corpus.
--
-- Scope note (NOT just FK columns): `packets` embeds the old session prefix in
-- four UN-CONSTRAINED columns — agent_id, reply_to, parent_attempt, parents[] —
-- with no FK. They would pass every FK check but silently break the reply graph,
-- distill provenance, and agent attribution, so they are rewritten here too.
-- The synthetic `curator` session id is NOT Crockford, so the guard skips it
-- (its content-addressed `curator/<digest>` packet ids must stay stable); only
-- its parents[] entries that point at remapped posts are rewritten.
--
-- FK strategy: DROP all 7 FKs, UPDATE, re-ADD identical FKs — all in ONE
-- transaction. End-state FK topology is byte-identical to 00001/00007/00009;
-- any error rolls the whole thing back. With FKs down there is no
-- transient-violation ordering trap.
--
-- Idempotent: the id map is built only from sessions whose id matches the
-- Crockford pattern (== sid.is_valid before this change), so a re-run, or rows
-- minted as UUIDs after cutover, are no-ops.
--
-- SAFETY: the local stack backs the hosted db-swarms corpus. Run `make
-- bank-backup` and dry-run on a scratch copy BEFORE applying (see the plan /
-- manyagent.utils.md Decision log). Rollback = `make bank-restore FILE=...` (the
-- rename is not losslessly invertible, so the dump is the rollback).

begin;

-- 0) Immutable map old_sid -> new uuid (text). TEMP, dropped at commit. Built
--    only from Crockford-shaped sessions (alphabet 0-9 A-Z minus I L O U).
create temp table _sid_map on commit drop as
select id as old_sid, gen_random_uuid()::text as new_sid
from sessions
where id ~ '^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$';

-- 1) Drop the 7 FKs (verified names; defensive IF EXISTS).
alter table agents            drop constraint if exists agents_session_id_fkey;
alter table agent_seq_counter drop constraint if exists agent_seq_counter_session_id_fkey;
alter table packets           drop constraint if exists packets_session_id_fkey;
alter table injections        drop constraint if exists injections_target_session_id_fkey;
alter table injections        drop constraint if exists injections_packet_id_fkey;
alter table traces            drop constraint if exists traces_packet_id_fkey;
alter table trace_renditions  drop constraint if exists trace_renditions_packet_id_fkey;

-- 2) Rewrite. Composite ids are rebuilt as new_sid || substr(id, len(old_sid)+1):
--    the cut is anchored at the leading sid (old_sid has no '/', matching the
--    code's split('/')[0] contract), so an old_sid recurring in a suffix can't
--    be double-replaced.

-- 2a) sessions PK
update sessions s
set id = m.new_sid
from _sid_map m where s.id = m.old_sid;

-- 2b) agents: composite PK (id) + FK column (session_id)
update agents a
set session_id = m.new_sid,
    id = m.new_sid || substr(a.id, length(m.old_sid) + 1)
from _sid_map m where a.session_id = m.old_sid;

-- 2c) packets: composite PK + FK column (keyed on owning session_id)
update packets p
set session_id = m.new_sid,
    id = m.new_sid || substr(p.id, length(m.old_sid) + 1)
from _sid_map m where p.session_id = m.old_sid;

-- 2c-i) packets.agent_id ('{sid}/...'; literals online/curator/NULL preserved by the LIKE)
update packets p
set agent_id = m.new_sid || substr(p.agent_id, length(m.old_sid) + 1)
from _sid_map m
where p.agent_id is not null and p.agent_id like m.old_sid || '/%';

-- 2c-ii) packets.reply_to (a packet id pointing anywhere; key on its own prefix)
update packets p
set reply_to = m.new_sid || substr(p.reply_to, length(m.old_sid) + 1)
from _sid_map m
where p.reply_to is not null and p.reply_to like m.old_sid || '/%';

-- 2c-iii) packets.parent_attempt
update packets p
set parent_attempt = m.new_sid || substr(p.parent_attempt, length(m.old_sid) + 1)
from _sid_map m
where p.parent_attempt is not null and p.parent_attempt like m.old_sid || '/%';

-- 2c-iv) packets.parents text[]: remap each element whose prefix is in the map
--        (covers curator distill provenance pointing at remapped posts).
update packets p
set parents = (
    select array_agg(
        coalesce(
            (select m.new_sid || substr(elem, length(m.old_sid) + 1)
             from _sid_map m where elem like m.old_sid || '/%' limit 1),
            elem)
        order by ord)
    from unnest(p.parents) with ordinality as u(elem, ord))
where exists (
    select 1 from unnest(p.parents) elem
    join _sid_map m on elem like m.old_sid || '/%');

-- 2d) agent_seq_counter: FK column only (PK == session_id)
update agent_seq_counter c
set session_id = m.new_sid
from _sid_map m where c.session_id = m.old_sid;

-- 2e) injections: target_session_id (FK) + packet_id (composite, part of PK)
update injections i
set target_session_id = m.new_sid
from _sid_map m where i.target_session_id = m.old_sid;

update injections i
set packet_id = m.new_sid || substr(i.packet_id, length(m.old_sid) + 1)
from _sid_map m where i.packet_id like m.old_sid || '/%';

-- 2f) traces.packet_id (PK, composite)
update traces t
set packet_id = m.new_sid || substr(t.packet_id, length(m.old_sid) + 1)
from _sid_map m where t.packet_id like m.old_sid || '/%';

-- 2g) trace_renditions.packet_id (part of composite PK)
update trace_renditions r
set packet_id = m.new_sid || substr(r.packet_id, length(m.old_sid) + 1)
from _sid_map m where r.packet_id like m.old_sid || '/%';

-- 3) Re-ADD the 7 FKs, byte-identical to 00001/00007/00009.
alter table agents            add constraint agents_session_id_fkey
    foreign key (session_id) references sessions(id);
alter table agent_seq_counter add constraint agent_seq_counter_session_id_fkey
    foreign key (session_id) references sessions(id);
alter table packets           add constraint packets_session_id_fkey
    foreign key (session_id) references sessions(id);
alter table injections        add constraint injections_target_session_id_fkey
    foreign key (target_session_id) references sessions(id);
alter table injections        add constraint injections_packet_id_fkey
    foreign key (packet_id) references packets(id);
alter table traces            add constraint traces_packet_id_fkey
    foreign key (packet_id) references packets(id);
alter table trace_renditions  add constraint trace_renditions_packet_id_fkey
    foreign key (packet_id) references packets(id);

-- 4) In-transaction assertion: no Crockford sid survives -> ROLLBACK if any do.
do $$
declare n int;
begin
    select count(*) into n from sessions
    where id ~ '^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$';
    if n <> 0 then raise exception 'aborting: % Crockford session ids remain', n; end if;
end $$;

commit;
