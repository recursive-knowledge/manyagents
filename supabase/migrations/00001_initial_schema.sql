-- 00001_initial_schema
-- Core tables: sessions, agents, packets, traces. Append-only; later
-- migrations extend these (never edit this file). oms.bank.md is the rendering.

create table if not exists sessions (
    id          text primary key,
    created_at  timestamptz not null default now(),
    status      text not null default 'active'
);

create table if not exists agents (
    id          text primary key,                       -- {session}/agent-{NNN}-{adapter}
    session_id  text not null references sessions(id),
    adapter     text not null,
    seq         integer not null,
    created_at  timestamptz not null default now(),
    unique (session_id, seq)
);

create table if not exists packets (
    id          text primary key,                       -- {session_id}/{uuid}
    session_id  text not null references sessions(id),
    type        text not null,                          -- raw|post|distill (CHECK in 00006)
    agent_id    text,                                   -- canonical id | 'online' | 'curator' | null
    created_at  timestamptz not null default now()
);

create index if not exists packets_session_idx on packets (session_id);
create index if not exists packets_type_idx on packets (type);

create table if not exists traces (
    packet_id   text primary key references packets(id),
    body        text,                                   -- scrubbed raw trace (NOT public)
    created_at  timestamptz not null default now()
);

-- Atomic, contiguous per-session agent sequence. Never max(seq)+1 client-side
-- (oms.bank.md identity rule). A counter row + ON CONFLICT increment is
-- transactional and concurrency-safe. SECURITY DEFINER is declared here (not
-- only in 00004) so a raw re-apply of this file cannot silently reset it to
-- INVOKER (CREATE OR REPLACE resets the security flag); search_path is pinned.
create table if not exists agent_seq_counter (
    session_id  text primary key references sessions(id),
    next_seq    integer not null
);

create or replace function next_agent_seq(p_session_id text)
returns integer
language sql
security definer
set search_path = public
as $$
    insert into agent_seq_counter (session_id, next_seq)
    values (p_session_id, 1)
    on conflict (session_id)
        do update set next_seq = agent_seq_counter.next_seq + 1
    returning next_seq;
$$;
