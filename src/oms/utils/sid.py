"""Session-id codec — Crockford Base32, 8 chars shown as ``XXXX-XXXX``.

~40 bits (8 chars x 5 bits), case-insensitive, no ambiguous chars (``I L O U``
excluded), URL-safe. This is a real key, never a derived string (datasmith
identity rule). ``new()`` takes an optional ``exists`` predicate so it is
collision-safe against the Bank (oms.bank wires it in M2/oms.core).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

# Crockford Base32 encode alphabet: 0-9 A-Z minus I L O U.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}
# Crockford decode aliases for human-entered ambiguous glyphs.
_DECODE.update({"I": 1, "L": 1, "O": 0})

_N_CHARS = 8
_BITS = _N_CHARS * 5  # 40


def _encode(value: int) -> str:
    chars = []
    for _ in range(_N_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def format(raw: str) -> str:  # noqa: A001 — codec verb, intentional
    """Insert the canonical hyphen into an 8-char body: ``XXXXYYYY`` → ``XXXX-YYYY``."""
    if len(raw) != _N_CHARS:
        raise ValueError(f"sid body must be {_N_CHARS} chars, got {len(raw)!r}")
    return f"{raw[:4]}-{raw[4:]}"


def new(exists: Callable[[str], bool] | None = None) -> str:
    """Generate a fresh canonical sid.

    If ``exists`` is given it is called with each candidate; generation retries
    until it returns ``False`` (collision-safe against the Bank).
    """
    while True:
        candidate = format(_encode(secrets.randbits(_BITS)))
        if exists is None or not exists(candidate):
            return candidate


def parse(s: str) -> str:
    """Normalize lenient input (lowercase, missing hyphen, Crockford aliases)
    to the canonical ``XXXX-XXXX`` form. Raises ``ValueError`` if it cannot."""
    body = s.strip().upper().replace("-", "").replace(" ", "")
    if len(body) != _N_CHARS:
        raise ValueError(f"sid must be {_N_CHARS} symbols, got {len(body)} in {s!r}")
    out = []
    for ch in body:
        if ch not in _DECODE:
            raise ValueError(f"invalid sid symbol {ch!r} in {s!r}")
        out.append(_ALPHABET[_DECODE[ch]])
    return format("".join(out))


def is_valid(s: str) -> bool:
    """True iff ``s`` is exactly the canonical form: ``XXXX-XXXX``, uppercase,
    all symbols in the Crockford encode alphabet (so ``I L O U`` are rejected)."""
    if len(s) != _N_CHARS + 1 or s[4] != "-":
        return False
    body = s[:4] + s[5:]
    return all(c in _ALPHABET for c in body)
