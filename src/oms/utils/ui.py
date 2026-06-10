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
import select
import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console, RenderableType
from rich.text import Text

from oms.utils import config, messages

__all__ = ["console", "pick_star", "read_key", "render", "style_diff", "tilde"]


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


# --------------------------------------------------------------------------- #
# the ★ number-line picker (the single commit gate's interactive form)
# --------------------------------------------------------------------------- #

# Symbolic key names produced by read_key() and consumed by pick_star().
_KEY_LEFT, _KEY_RIGHT, _KEY_ENTER, _KEY_ESC = "left", "right", "enter", "esc"


def read_key() -> str:
    """Read one keypress from a TTY stdin in cbreak mode and return a symbolic
    name: ``left``/``right`` (arrows, also ``h``/``l``), ``enter``, ``esc``,
    a literal digit ``1``-``5``, or the lowercased character. A lone ESC is
    distinguished from an arrow escape-sequence by a short ``select`` poll
    (an arrow's ``[X`` bytes arrive within the same keystroke)."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch in ("\r", "\n"):
            return _KEY_ENTER
        if ch == "\x1b":
            if select.select([sys.stdin], [], [], 0.05)[0]:
                seq = sys.stdin.read(1)
                if seq == "[" and select.select([sys.stdin], [], [], 0.05)[0]:
                    code = sys.stdin.read(1)
                    if code == "D":
                        return _KEY_LEFT
                    if code == "C":
                        return _KEY_RIGHT
                return _KEY_ESC
            return _KEY_ESC
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _star_line(question: str, current: int) -> Text:
    """One redrawable line: the question, the 1★…5★ number line with the
    current value bracketed, and the low/high anchors so the direction of
    'better' is explicit (5★ = best)."""
    t = Text()
    t.append(question + "  ", style="bold")
    for n in range(1, 6):
        label = f"{n}★"
        if n == current:
            t.append(f"❰{label}❱", style="bold yellow")
        else:
            t.append(f" {label} ", style="dim")
    t.append(f"  {messages.COMMIT_PICKER_SCALE_LOW} → {messages.COMMIT_PICKER_SCALE_HIGH}", style="dim")
    return t


def pick_star(
    propose: int,
    *,
    question: str = messages.COMMIT_QUESTION,
    key_fn: Callable[[], str] | None = None,
    out: Callable[[str], None] | None = None,
) -> tuple[bool, int | None]:
    """Interactive ★ number-line for the single commit gate. Arrow keys (or
    ``1``-``5``) move the selection, Enter commits with the selected ★,
    ``s`` commits unrated, ``n``/Esc discards. Returns ``(commit, rating)``
    exactly like :func:`oms.cli.ask_commit`'s typed fallback.

    ``key_fn``/``out`` are injectable for tests; the default reads real
    keystrokes via :func:`read_key` and redraws in place with ``\\r``."""
    keys = key_fn or read_key
    current = min(5, max(1, propose))

    def _stdout_write(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    write = out or _stdout_write
    write(render(Text(messages.COMMIT_PICKER_HINT, style="dim")) + "\n")
    while True:
        write("\r\x1b[2K" + render(_star_line(question, current)))
        k = keys()
        if k in (_KEY_LEFT, "h") and current > 1:
            current -= 1
        elif k in (_KEY_RIGHT, "l") and current < 5:
            current += 1
        elif k in ("1", "2", "3", "4", "5"):
            current = int(k)
        elif k == _KEY_ENTER:
            write("\n")
            return True, current
        elif k == "s":
            write("\n")
            return True, None
        elif k in ("n", _KEY_ESC):
            write("\n")
            return False, None


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
