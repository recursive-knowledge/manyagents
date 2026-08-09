"""Drive manyagent's real curator prompts through a live LLM over the KSI
scenarios, then score the result against the protocol's known failure modes.

This is a *diagnostic*, not a test: it reports what the current prompts produce
so prompt changes can be justified by evidence instead of taste. It calls the
real ``build_distill_prompt``, the real ``_extract_json``, and the real
``validate_bundle``, so whatever it reports is what the shipped pipeline does.

Usage (points at any OpenAI-compatible endpoint; the local debug model by
default)::

    MANYAGENT_LLM_BASE_URL=http://localhost:30005/v1 \
    MANYAGENT_LLM_API_KEY=local \
    MANYAGENT_LLM_MODEL=qwen3.6-35b-a3b \
    uv run python scripts/simulate_ksi.py

Add ``--json out.json`` to write the full record for a before/after diff.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ksi_scenarios import SCENARIOS

from manyagent.distill.curator import _extract_json
from manyagent.distill.parse import validate_bundle
from manyagent.distill.prompts import build_distill_prompt
from manyagent.distill.schema import BUCKETS

# A wholesale rejection names a hypothesis family with no parameterization and
# no surviving variant. The KSI paper's headline failure mode: ~4/10 eventual
# solves came from families an earlier bundle had rejected outright.
_PARAMETERIZED = re.compile(r"FALSIFIED:.*UNTRIED:", re.IGNORECASE | re.DOTALL)

# Process advice that holds for any task regardless of content. Mirrors the
# families named in the curator's own generic-advice ban.
_GENERIC = re.compile(
    r"\b(be (more )?(careful|thorough)|pay attention|systematic(ally)?|"
    r"validate first|think (carefully|step by step)|double[- ]check|"
    r"attention to detail|more rigorous|best practice)\b",
    re.IGNORECASE,
)

# A concrete primitive: a path, a call, a flag, an identifier, a number+unit.
_CONCRETE = re.compile(
    r"(\w+\.(py|cfg|toml|json|md|sh|conf)\b|\w+\(\)|--?[a-z][\w-]+|"
    r"\b[a-z_]+\.[a-z_]+\(|\b\d+(\.\d+)?\s*(s|ms|kb|mb|gb)\b|`[^`]+`|\b[A-Z_]{3,}\b)",
    re.IGNORECASE,
)


def _model() -> Any:
    from manyagent.distill.resolve import _OpenAICompatModel

    base = os.environ.get("MANYAGENT_LLM_BASE_URL", "http://localhost:30005/v1")
    key = os.environ.get("MANYAGENT_LLM_API_KEY", "local")
    name = os.environ.get("MANYAGENT_LLM_MODEL", "qwen3.6-35b-a3b")
    return _OpenAICompatModel(base_url=base, api_key=key, model=name)


def _score(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Score one validated bundle against the KSI failure modes."""
    insights = [(b, i) for b in BUCKETS for i in bundle.get(b, [])]
    texts = [str(i.get("text", "")) for _, i in insights]

    rejects = [str(i.get("text", "")) for b, i in insights if b == "rejected_hypotheses"]
    wholesale = [t for t in rejects if not _PARAMETERIZED.search(t)]
    generic = [t for t in texts if _GENERIC.search(t)]
    vague = [t for t in texts if not _CONCRETE.search(t)]

    return {
        "total_insights": len(insights),
        "per_bucket": {b: len(bundle.get(b, [])) for b in BUCKETS},
        "rejected_total": len(rejects),
        "wholesale_rejections": wholesale,
        "generic_advice": generic,
        "no_concrete_primitive": vague,
    }


def run_one(key: str, *, model: Any, verbose: bool) -> dict[str, Any]:
    spec = SCENARIOS[key]
    posts = spec["posts"]
    system, user = build_distill_prompt(posts=posts, scope="per_goal", goal=spec["goal"])

    raw = model.complete(f"{system}\n\n{user}")
    payload = _extract_json(raw)
    recovered = payload is not None
    bundle = validate_bundle(payload, posts=posts) if recovered else {b: [] for b in BUCKETS}

    # How much of what the model emitted survived the mechanical parser?
    emitted = 0
    if isinstance(payload, dict):
        emitted = sum(len(payload.get(b) or []) for b in BUCKETS if isinstance(payload.get(b), list))
    kept = sum(len(bundle.get(b, [])) for b in BUCKETS)

    rec = {
        "scenario": key,
        "family": spec["family"],
        "probe": spec["probe"],
        "prompt_chars": len(system) + len(user),
        "raw_chars": len(raw),
        "json_recovered": recovered,
        "emitted_insights": emitted,
        "kept_insights": kept,
        "drop_rate": None if emitted == 0 else round(1 - kept / emitted, 3),
        "score": _score(bundle),
        "bundle": bundle,
    }
    if verbose:
        rec["raw"] = raw
    return rec


_FLAGS = (
    ("wholesale_rejections", "WHOLESALE REJECTIONS"),
    ("generic_advice", "GENERIC ADVICE"),
    ("no_concrete_primitive", "NO CONCRETE PRIMITIVE"),
)


def _report(rec: dict[str, Any]) -> None:
    """Print one scenario's result and every failure mode it tripped."""
    s = rec["score"]
    print(f"  json recovered : {rec['json_recovered']}")
    print(f"  emitted -> kept: {rec['emitted_insights']} -> {rec['kept_insights']}  (drop {rec['drop_rate']})")
    print(f"  per bucket     : {s['per_bucket']}")
    for key, label in _FLAGS:
        hits = s[key]
        if not hits:
            continue
        print(f"  !! {label} ({len(hits)}):")
        for text in hits:
            print(f"       - {text[:150]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    ap.add_argument("--json", dest="out", help="write the full record here")
    ap.add_argument("--verbose", action="store_true", help="include raw model output")
    args = ap.parse_args()

    model = _model()
    keys = list(SCENARIOS) if args.scenario == "all" else [args.scenario]

    records = []
    for key in keys:
        print(f"\n{'=' * 72}\nSCENARIO {key}  ({SCENARIOS[key]['family']})")
        print(f"probe: {SCENARIOS[key]['probe']}\n{'=' * 72}")
        try:
            rec = run_one(key, model=model, verbose=args.verbose)
        except Exception as exc:  # a diagnostic must report failures, not die
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            records.append({"scenario": key, "error": f"{type(exc).__name__}: {exc}"})
            continue
        records.append(rec)
        _report(rec)

    tot = [r for r in records if "score" in r]
    if tot:
        print(f"\n{'=' * 72}\nTOTALS across {len(tot)} scenarios")
        print(f"  insights kept        : {sum(r['kept_insights'] for r in tot)}")
        print(f"  wholesale rejections : {sum(len(r['score']['wholesale_rejections']) for r in tot)}")
        print(f"  generic advice       : {sum(len(r['score']['generic_advice']) for r in tot)}")
        print(f"  no concrete primitive: {sum(len(r['score']['no_concrete_primitive']) for r in tot)}")

    if args.out:
        Path(args.out).write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
