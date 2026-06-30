-- 00013_injection_helpful
-- Per-injection "did this help?" tap (capture-only). Stores the human's
-- end-of-session verdict on whether injected knowledge helped, per injection
-- row (reviews/2026-06-22-1920/adoption-reuse.md HIGH: the reuse signal
-- measures correlation, not causation). Owner decision #2: ship the signal,
-- DEFER the formal eval — so this CAPTURES helpful/helpful_note but does NOT
-- touch the reuse_score view / weighting.

alter table injections add column if not exists helpful      boolean;
alter table injections add column if not exists helpful_note text;

-- The tap is set after the injection row exists, by the same trusted role that
-- wrote it (/inject), so authenticated needs UPDATE in addition to its existing
-- INSERT grant. Mirrors the 00007 grant/policy idioms (drop-if-exists/create).
grant update on injections to authenticated;
drop policy if exists "trusted_update" on injections;
create policy "trusted_update" on injections FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
