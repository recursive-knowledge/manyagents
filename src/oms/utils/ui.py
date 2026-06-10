"""oms.utils.ui — the CLI presentation layer (rich).

One rule: every styled surface renders to a *string* first (:func:`render`)
and is pushed through the existing injectable ``output_fn`` / ``input_fn``
seams, so programmatic callers and tests keep receiving plain ``str`` lines.
Styling is destination-gated: ANSI escapes appear only when the stream is a
terminal (``OMS_COLOR=auto``, the default) or when forced
(``OMS_COLOR=always``). ``OMS_COLOR=never`` strips them, and ``NO_COLOR``
(no-color.org) downgrades ``auto`` to ``never`` — an explicit
``OMS_COLOR=always`` wins, per the spec's "software-level config takes
precedence" rule — so ``oms status | grep`` and captured test output stay
byte-identical to the unstyled text.

Consoles are constructed per call, never cached: a monkeypatched env or a
redirected stream takes effect on the next render, with no reset hook needed.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console, RenderableType
from rich.text import Text

from oms.utils import config

__all__ = ["console", "render", "style_diff", "tilde"]


def console(*, stderr: bool = False, min_width: int | None = None) -> Console:
    """A fresh :class:`~rich.console.Console` honoring ``OMS_COLOR`` at call
    time. ``highlight=False``: oms output is styled explicitly, never by
    rich's auto-highlighter (which would recolor numbers/paths in messages
    the tests assert on). ``min_width`` floors the layout width so
    width-aware renderables (panels) never char-fold their content into
    unreadable/ungreppable shreds in pathologically narrow terminals."""
    mode = config.resolve("OMS_COLOR", config.OMS_COLOR).strip().lower()
    if mode not in ("always", "never") and os.environ.get("NO_COLOR"):
        mode = "never"  # NO_COLOR downgrades auto; explicit always/never win
    force = {"always": True, "never": False}.get(mode)  # anything else → auto-detect
    c = Console(stderr=stderr, force_terminal=force, highlight=False)
    if min_width is not None and c.width < min_width:
        c = Console(stderr=stderr, force_terminal=force, highlight=False, width=min_width)
    return c


def render(
    *renderables: RenderableType, stderr: bool = False, soft_wrap: bool = True, min_width: int | None = None
) -> str:
    """Render to a string for the ``output_fn(str)`` seam.

    ``soft_wrap=True`` (the default) keeps single-line messages unwrapped at
    any length so substring assertions and ``grep`` stay sound; pass
    ``soft_wrap=False`` for width-aware layouts (panels, tables) that must
    wrap *inside* their borders instead.
    """
    c = console(stderr=stderr, min_width=min_width)
    with c.capture() as cap:
        for renderable in renderables:
            c.print(renderable, soft_wrap=soft_wrap)
    return cap.get().rstrip("\n")


def tilde(path: Path | str) -> str:
    """Abbreviate ``$HOME`` to ``~`` for display (display-only; never feed the
    result back into file operations). Always renders with forward slashes —
    it's a human-facing label, not an OS path."""
    try:
        rel = Path(path).relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~" if rel == Path(".") else "~/" + rel.as_posix()


def style_diff(diff_text: str) -> Text:
    """Color a unified diff line-by-line. The text content is byte-preserved —
    only styles are layered on, so the plain (non-TTY) rendering is identical
    to the input."""
    out = Text()
    for line in diff_text.splitlines():
        if line.startswith(("===", "+++", "---")):
            style = "bold"
        elif line.startswith("@@"):
            style = "cyan"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        else:
            style = ""
        out.append(line, style or None)
        out.append("\n")
    return out
