"""oma.forum — the write-time contribution discipline (M6; oma.forum.md).

Every knowledge contribution is a ``post`` packet carrying a structured,
falsifiable, evidence-grounded body, optionally threaded as a stance-tagged
reply. The discipline is generated **by the agent**, never the human (Design
Principles §11 — structure is an agent tax, never a human tax). The curator
(``oma.distill``, M7) consumes posts and imports **this module's**
``ANTI_META_BLOCK`` (same object) so the rule the agent writes against is the
rule the curator filters against.

**C1:** the parser never persists and never sets ``preference``; a rejected
``/self-distill`` post is not stored — the CLI re-prompts (oma.forum.md:89).
"""

from __future__ import annotations

from oma.forum.anti_meta import (
    ANTI_META_BLOCK,
    BANNED_META_PHRASES,
    assert_anti_meta_rules_present,
)
from oma.forum.discuss import clear_discuss_gate, enforce_retrieved_before_reply, retrieve
from oma.forum.parser import parse_post
from oma.forum.prompt import render_post_prompt
from oma.forum.schema import REQUIRED_FIELDS, validate_schema

__all__ = [
    "ANTI_META_BLOCK",
    "BANNED_META_PHRASES",
    "REQUIRED_FIELDS",
    "assert_anti_meta_rules_present",
    "clear_discuss_gate",
    "enforce_retrieved_before_reply",
    "parse_post",
    "render_post_prompt",
    "retrieve",
    "validate_schema",
]
