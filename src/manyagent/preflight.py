"""``python -m manyagent.preflight`` — validate env / Bank / keys before real work
(M8; manyagent.cli.md "preflight.py").

Three checks, fail-fast with a specific message:
  1. env presence — ``MANYAGENT_BANK_URL`` and at least one Bank key.
  2. Bank reachability — an HTTP GET to the PostgREST root (any 2xx/4xx means
     the service answered; 5xx / connect-timeout means down).
  3. migration inventory — the ``supabase/migrations/*.sql`` files manyagent.bank
     expects are present on disk.

The live ``schema_migrations`` diff (what the DB has actually applied vs. the
file list) is the M10 addition: a **best-effort, advisory** check gated on the
privileged admin key (``schema_migrations`` is outside the anon grant). It
never raises and never changes the preflight exit code — without the admin key
(the default / offline path) it is a no-op "skipped" line, so behavior for
every non-privileged caller is unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BANK_KEYS = (
    "MANYAGENT_BANK_ANON_KEY",
    "MANYAGENT_BANK_TRUSTED_KEY",
    "MANYAGENT_BANK_ADMIN_KEY",
    "MANYAGENT_BANK_CURATOR_KEY",
)
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _check_env() -> str | None:
    if not os.environ.get("MANYAGENT_BANK_URL"):
        return "MANYAGENT_BANK_URL is unset (point it at the Supabase PostgREST URL)"
    if not any(os.environ.get(k) for k in _BANK_KEYS):
        return f"no Bank key set (need at least one of {', '.join(_BANK_KEYS)})"
    return None


def _check_bank_reachable(url: str) -> str | None:
    import httpx

    try:
        resp = httpx.get(f"{url.rstrip('/')}/rest/v1/", timeout=5.0)
    except Exception as exc:  # connect/timeout/DNS → down
        return f"Bank unreachable at {url}: {exc!r}"
    if resp.status_code >= 500:
        return f"Bank returned {resp.status_code} at {url} (service up but erroring)"
    return None  # any 2xx/4xx: PostgREST answered (401/404 are normal without a key)


def _check_migrations() -> str | None:
    if not _MIGRATIONS_DIR.is_dir():
        return f"migrations dir missing: {_MIGRATIONS_DIR}"
    files = sorted(p.name for p in _MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        return f"no .sql migrations under {_MIGRATIONS_DIR}"
    return None


def _live_schema_diff(url: str) -> str:
    """Best-effort: applied DB migrations vs. the on-disk list. Returns a
    human-readable status; **never raises, never changes the exit code**
    (an advisory privileged-conn check). ``schema_migrations`` is outside the
    anon grant, so without ``MANYAGENT_BANK_ADMIN_KEY`` this is skipped, not failed —
    the offline / non-privileged path is byte-for-byte unchanged."""
    admin = os.environ.get("MANYAGENT_BANK_ADMIN_KEY")
    if not admin:
        return "skipped (no MANYAGENT_BANK_ADMIN_KEY; schema_migrations needs the privileged role)"
    files = {p.stem for p in _MIGRATIONS_DIR.glob("*.sql")}
    try:
        import httpx

        resp = httpx.get(
            f"{url.rstrip('/')}/rest/v1/schema_migrations",
            params={"select": "version"},
            headers={"apikey": admin, "Authorization": f"Bearer {admin}"},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return f"skipped (schema_migrations not readable: HTTP {resp.status_code})"
        applied = {str(r.get("version")) for r in resp.json()}
    except Exception as exc:  # connect / parse / unmocked — advisory only
        return f"skipped (error: {exc!r})"
    missing = sorted(f for f in files if not any(f.startswith(v) for v in applied))
    extra = sorted(v for v in applied if not any(f.startswith(v) for f in files))
    if not missing and not extra:
        return f"OK — {len(applied)} applied migration(s) match the {len(files)} file(s)"
    return f"DRIFT — files-not-applied={missing} applied-not-in-files={extra}"


def run_preflight() -> int:
    """Run preflight checks. Returns 0 when the environment is usable, 1 on the
    first failing check (with a specific stderr message)."""
    from rich.text import Text

    from manyagent.utils import ui

    def fail(category: str, reason: str) -> None:
        print(ui.render(Text.assemble(("[FAIL] ", "bold red"), f"{category}: {reason}"), stderr=True), file=sys.stderr)

    reason = _check_env()
    if reason is not None:
        fail("env", reason)
        return 1

    reason = _check_bank_reachable(os.environ["MANYAGENT_BANK_URL"])
    if reason is not None:
        fail("bank", reason)
        return 1

    reason = _check_migrations()
    if reason is not None:
        fail("migrations", reason)
        return 1

    n = len(sorted(_MIGRATIONS_DIR.glob("*.sql")))
    print(ui.render(Text.assemble(("[OK] ", "bold green"), f"env + Bank reachable + {n} migration file(s) present.")))
    print(
        ui.render(
            Text.assemble(
                ("[INFO] ", "bold cyan"), f"schema diff: {_live_schema_diff(os.environ['MANYAGENT_BANK_URL'])}"
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_preflight())
