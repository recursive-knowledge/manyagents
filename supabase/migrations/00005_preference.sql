-- 00005_preference
-- Distill artifact-quality signal: accept|reject on a distill packet (the
-- existing loop generalized; rejected attempts retained as negative data).
-- parent_attempt threads a re-curated bundle to the attempt it supersedes.
-- NOTE (C1): a rejected /self-distill *post* is never stored; preference is a
-- distill-only field (oms.core.md:70). Enforced in app (oms.forum/oms.cli).

alter table packets add column if not exists preference     text;  -- accept|reject|null (distill)
alter table packets add column if not exists parent_attempt text;
