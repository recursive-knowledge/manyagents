"""KSI protocol scenarios — realistic forum corpora for the three benchmark
families the KSI paper evaluates (abstract reasoning, coding, terminal).

Each scenario is a list of ``post`` packet records shaped exactly like the ones
``manyagent.forum.parse_post`` persists, so ``build_distill_prompt`` and
``validate_bundle`` can run over them unmodified. The posts are written the way
real agents write them: concrete, evidence-bearing, and *partially wrong* —
several attempts falsify a hypothesis whose neighbours are still viable.

That last property is the point. The KSI paper reports that roughly 4 in 10
eventual solves came from hypothesis families an earlier bundle had rejected
wholesale, so a scenario that cannot expose over-rejection cannot measure the
protocol's most expensive failure mode.
"""

from __future__ import annotations

from typing import Any


def _post(
    pid: str,
    session: str,
    goal: str,
    *,
    assumption: str,
    evidence: str,
    proposed: str,
    predicted: str,
    confidence: str = "medium",
    rating: int | None = None,
) -> dict[str, Any]:
    return {
        "id": pid,
        "session_id": session,
        "type": "post",
        "agent_id": f"{session}/agent-001-claude",
        "kind": "reflection",
        "goal": goal,
        "rating": rating,
        "structured": {
            "load_bearing_assumption": assumption,
            "evidence": evidence,
            "evidence_ref": None,
            "proposed_next": proposed,
            "predicted_outcome": predicted,
            "confidence": confidence,
        },
    }


# --------------------------------------------------------------------------- #
# Scenario A — abstract reasoning (ARC-like grid transforms).
#
# Three sessions attack the same goal. Two falsify DIFFERENT parameterizations
# of the same family (colour-mapping), one succeeds with a third. A curator that
# rejects "colour mapping" wholesale poisons the family that actually solved it.
# --------------------------------------------------------------------------- #
ARC_POSTS: list[dict[str, Any]] = [
    _post(
        "S-arc-1/p1",
        "S-arc-1",
        "arc-grid-recolour",
        assumption="solve_task() assumed a global colour map keyed on input colour alone",
        evidence="verbatim from trace: 'grid 3 of 5 mismatched: expected 8 at (2,1), produced 3'",
        proposed="key the colour map on (colour, cell_degree) instead of colour alone in solve_task()",
        predicted="the 3 grids whose recolour depends on neighbour count will match",
        confidence="medium",
        rating=4,
    ),
    _post(
        "S-arc-2/p1",
        "S-arc-2",
        "arc-grid-recolour",
        assumption="a colour map keyed on (colour, cell_degree) still mismatched on grids with holes",
        evidence="verbatim from trace: 'grid 5 mismatch at (0,3): degree=2 mapped to 4, expected 6'",
        proposed="key the map on (colour, enclosed_by_ring) — a topological property, not a count",
        predicted="hole-bearing grids 4 and 5 match; degree-only grids stay correct",
        confidence="medium",
        rating=3,
    ),
    _post(
        "S-arc-3/p1",
        "S-arc-3",
        "arc-grid-recolour",
        assumption="keying the colour map on (colour, enclosed_by_ring) solved all 5 training grids",
        evidence="verbatim from trace: 'all 5 training grids match; submitted, evaluator returned 1.0'",
        proposed="reuse the enclosed_by_ring predicate from grid_topology.py for other recolour tasks",
        predicted="recolour tasks with ring structure solve on the first attempt",
        confidence="high",
        rating=5,
    ),
]

# --------------------------------------------------------------------------- #
# Scenario B — coding (SWE-bench-like repo repair).
#
# Two sessions, one goal. The pitfall is real but narrowly scoped: it holds for
# the async test path only. A curator that drops the boundary turns a targeted
# warning into a blanket ban on a correct idiom.
# --------------------------------------------------------------------------- #
CODE_POSTS: list[dict[str, Any]] = [
    _post(
        "S-code-1/p1",
        "S-code-1",
        "swe-async-timeout",
        assumption="pytest.mark.asyncio tests inherited the default 5s timeout from setup.cfg",
        evidence="verbatim from trace: 'test_stream_large_payload FAILED: asyncio timeout after 5.0s'",
        proposed="set asyncio_default_timeout=30 in setup.cfg rather than per-test markers",
        predicted="test_stream_large_payload passes; sync tests keep the 5s bound",
        confidence="high",
        rating=5,
    ),
    _post(
        "S-code-2/p1",
        "S-code-2",
        "swe-async-timeout",
        assumption="raising the global timeout masked a real deadlock in connection_pool.acquire()",
        evidence="verbatim from trace: 'pool.acquire() waited 28.4s then succeeded; max_size=2, 3 waiters'",
        proposed="raise max_size to 4 in connection_pool.py and restore the 5s timeout",
        predicted="acquire() returns under 100ms and the original 5s timeout stops failing",
        confidence="high",
        rating=5,
    ),
]

# --------------------------------------------------------------------------- #
# Scenario C — terminal (Terminal-Bench-like ops).
#
# One goal, two sessions. Deliberately seeded with a vague post so the corpus
# contains the exact failure the prompts must suppress: process advice that
# reads as wisdom and carries no discriminating condition.
# --------------------------------------------------------------------------- #
TERM_POSTS: list[dict[str, Any]] = [
    _post(
        "S-term-1/p1",
        "S-term-1",
        "terminal-service-restart",
        assumption="systemctl restart nginx returned 0 while the config was still invalid",
        evidence="verbatim from trace: 'nginx -t: [emerg] duplicate listen 0.0.0.0:80 in /etc/nginx/sites-enabled/api'",
        proposed="run `nginx -t` before every `systemctl restart nginx` in the deploy script",
        predicted="an invalid config fails the deploy step instead of silently keeping the old worker",
        confidence="high",
        rating=5,
    ),
    _post(
        "S-term-2/p1",
        "S-term-2",
        "terminal-service-restart",
        assumption="the task needed careful attention to detail and systematic verification",
        evidence="verbatim from trace: 'it worked after I checked things more carefully'",
        proposed="be more thorough when validating the configuration",
        predicted="fewer mistakes overall",
        confidence="low",
        rating=1,
    ),
]

SCENARIOS: dict[str, dict[str, Any]] = {
    "arc": {
        "goal": "arc-grid-recolour",
        "posts": ARC_POSTS,
        "family": "abstract reasoning",
        "probe": "over-rejection: does the bundle ban colour-mapping wholesale?",
    },
    "code": {
        "goal": "swe-async-timeout",
        "posts": CODE_POSTS,
        "family": "coding",
        "probe": "boundary: does the timeout pitfall keep its async-only scope?",
    },
    "terminal": {
        "goal": "terminal-service-restart",
        "posts": TERM_POSTS,
        "family": "terminal",
        "probe": "noise: is the vague low-rated post excluded from the bundle?",
    },
}
