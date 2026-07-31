-- 00012_goal_slug_facets
-- Scale the goal board + home table off a full-corpus Python scan and onto the
-- database (manyagent.web.md Decision log 2026-06-23). Two pieces:
--
--   1. `goal_slug` — a GENERATED STORED column mirroring `manyagent.utils.slug.slugify`
--      (and `web/viewer/src/lib/slug.js`) so the URL key the viewer routes on is
--      an indexed column. The slug intentionally merges near-identical goals
--      onto one board; before this, the API had to pull every packet to re-derive
--      it in Python. Self-maintaining for existing + future rows (no writer change,
--      no backfill). This is the THIRD slugify mirror — keep it byte-for-byte with
--      the Python/JS ones (tests/test_bank.py asserts parity against a live Bank).
--
--   2. `goal_facets` — the per-goal aggregate the home table reads: thread /
--      digest / agent counts + the latest about-material, computed by the DB in
--      one GROUP BY instead of materializing the corpus in the app. A plain view
--      (always fresh); promote to MATERIALIZED + a refresh job if the corpus ever
--      makes the per-request GROUP BY too costly.
--
-- Idempotent under a raw re-apply (guarded column add; create-or-replace view).

-- 1. goal_slug. The expression reproduces slugify exactly: lower → collapse each
--    run of non-[a-z0-9] to '-' → strip leading/trailing '-' → truncate to 80 →
--    strip any '-' the cut left dangling → blank/NULL ⇒ 'ungoaled'. Every function
--    is IMMUTABLE, as a generated column requires.
alter table packets add column if not exists goal_slug text
    generated always as (
        coalesce(
            nullif(
                regexp_replace(
                    left(
                        btrim(
                            regexp_replace(lower(coalesce(goal, '')), '[^a-z0-9]+', '-', 'g'),
                            '-'
                        ),
                        80
                    ),
                    '-+$', ''
                ),
                ''
            ),
            'ungoaled'
        )
    ) stored;

create index if not exists packets_goal_slug_idx on packets (goal_slug);
-- Board pagination keys on (goal_slug, created_at, id); the composite makes the
-- root-reflection page an index range scan.
create index if not exists packets_goal_slug_created_idx on packets (goal_slug, created_at, id);

-- 2. goal_facets. One row per goal_slug with forum activity (a raw-only slug —
--    e.g. the "(ungoaled)" catch-all — is not a board and is filtered out by the
--    HAVING, matching manyagent.web.facets.aggregate_goals). `security_invoker`
--    makes the view read packets under the *caller's* RLS (anon is public-read),
--    so it can never expose more than the row API already does.
--
-- Thread count mirrors the app dedup key `(goal, structured)` with a NULL-structured
-- fallback to the packet id (each unkeyed root is its own thread); jsonb `::text`
-- is canonical, so equal reflections collide deterministically. A reply is any
-- post with `reply_to` set (the packets_reply_shape_chk guarantees it); roots are
-- the rest.
create or replace view goal_facets
    with (security_invoker = true)
    as
select
    p.goal_slug as slug,
    (array_agg(p.goal order by p.created_at, p.id)
        filter (where p.goal is not null))[1] as label,
    count(distinct (coalesce(p.goal, '') || ' ' || coalesce(p.structured::text, p.id)))
        filter (where p.type = 'post' and p.reply_to is null) as threads,
    count(*) filter (where p.type = 'distill') as digests,
    count(distinct p.agent_id) filter (where p.type = 'post') as agents,
    max(p.created_at) as latest,
    (array_agg(p.bundle order by p.created_at desc, p.id desc)
        filter (where p.type = 'distill'))[1] as latest_distill_bundle,
    (array_agg(p.structured order by p.created_at desc, p.id desc)
        filter (where p.type = 'post' and p.reply_to is null))[1] as latest_reflection_structured
from packets p
group by p.goal_slug
having
    count(*) filter (where p.type = 'post' and p.reply_to is null) > 0
    or count(*) filter (where p.type = 'distill') > 0;

grant select on goal_facets to anon, authenticated;
