-- 00002_packet_quarantine
-- Quarantine is a first-class, non-hiding state: visible, provenance kept,
-- excluded from curation / /inject (oms.bank.md "Quarantine — Settled").

alter table packets add column if not exists quarantined        boolean not null default false;
alter table packets add column if not exists quarantine_reason  text;
alter table packets add column if not exists auditor_version    text;

create index if not exists packets_quarantined_idx on packets (quarantined);
