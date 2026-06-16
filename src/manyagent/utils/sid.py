"""Session-id codec — UUID4.

A session id is a random UUID4 in canonical form (lowercase, hyphenated, 36
chars). This is a real key, never a derived string (datasmith identity rule).
``new()`` takes an optional ``exists`` predicate so it is collision-safe against
the Bank (manyagent.bank wires it in M2/manyagent.core). UUIDs contain no ``/``,
so the ``{session_id}/{suffix}`` packet-id scheme stays unambiguous
(manyagent.capture.conformance forbids ``/`` in a session id).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable


def new(exists: Callable[[str], bool] | None = None) -> str:
    """Generate a fresh canonical session id (UUID4, lowercase hyphenated).

    If ``exists`` is given it is called with each candidate; generation retries
    until it returns ``False`` (collision-safe against the Bank).
    """
    while True:
        candidate = str(uuid.uuid4())
        if exists is None or not exists(candidate):
            return candidate


def parse(s: str) -> str:
    """Normalize lenient user input (case, surrounding whitespace, the no-hyphen
    32-hex / brace / ``urn:uuid:`` forms) to the canonical lowercase hyphenated
    UUID. Raises ``ValueError`` if it is not a UUID. (Only applied to a
    user-supplied ``ma start --id`` — stored ids are never re-parsed on read.)
    """
    try:
        return str(uuid.UUID(s.strip()))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"not a valid session id (expected a UUID): {s!r}") from exc


def is_valid(s: str) -> bool:
    """True iff ``s`` is exactly the canonical UUID form (lowercase, hyphenated,
    36 chars) — the shape ``new()`` emits and ``parse()`` normalizes to.
    Uppercase, braced, and no-hyphen forms are parseable but NOT canonical, so
    they are rejected here (use ``parse`` to canonicalize them first).
    """
    try:
        return str(uuid.UUID(s)) == s
    except (ValueError, AttributeError, TypeError):
        return False
