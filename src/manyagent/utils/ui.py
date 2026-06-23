"""manyagent.utils.ui — the CLI presentation layer (rich).

One rule: every styled surface renders to a *string* first (:func:`render`)
and is pushed through the existing injectable ``output_fn`` / ``input_fn``
seams, so programmatic callers and tests keep receiving plain ``str`` lines.
Styling is destination-gated: ANSI escapes appear only when the stream is a
terminal (``MANYAGENT_COLOR=auto``, the default) or when forced
(``MANYAGENT_COLOR=always``). ``MANYAGENT_COLOR=never`` strips them, and ``NO_COLOR``
(no-color.org) downgrades ``auto`` to ``never`` — an explicit
``MANYAGENT_COLOR=always`` wins, per the spec's "software-level config takes
precedence" rule — so ``ma agent list | grep`` and captured test output stay
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

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from manyagent.utils import config, messages

__all__ = ["console", "pick_star", "read_key", "render", "render_post", "style_diff", "tilde"]


def console(*, stderr: bool = False, min_width: int | None = None) -> Console:
    """A fresh :class:`~rich.console.Console` honoring ``MANYAGENT_COLOR`` at call
    time. ``highlight=False``: manyagent output is styled explicitly, never by
    rich's auto-highlighter (which would recolor numbers/paths in messages
    the tests assert on). ``min_width`` floors the layout width so
    width-aware renderables (panels) never char-fold their content into
    unreadable/ungreppable shreds in pathologically narrow terminals."""
    mode = config.resolve("MANYAGENT_COLOR", config.MANYAGENT_COLOR).strip().lower()
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
        # Outside $HOME: still a forward-slash human label (never an OS path),
        # so it reads identically on Windows and POSIX.
        return Path(path).as_posix()
    return "~" if rel == Path(".") else "~/" + rel.as_posix()


# --------------------------------------------------------------------------- #
# the ★ number-line picker (the single commit gate's interactive form)
# --------------------------------------------------------------------------- #

# Symbolic key names produced by read_key() and consumed by pick_star().
_KEY_LEFT, _KEY_RIGHT, _KEY_ENTER, _KEY_ESC = "left", "right", "enter", "esc"


def read_key() -> str:
    """Read one keypress from a TTY stdin in cbreak mode and return a symbolic
    name: ``left``/``right`` (arrows, also ``h``/``l``), ``enter``, ``esc``,
    a literal digit ``1``-``5``, or the lowercased character.

    **POSIX only** (termios/tty), same contract as ``cli._pty_spawn`` — the
    early raise also keeps the termios calls unreachable for mypy's win32
    platform narrowing on the Windows CI leg."""
    if sys.platform == "win32":  # pragma: no cover — checked on Windows CI
        raise NotImplementedError("manyagent's interactive key reader is POSIX-only (termios/tty)")
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return _read_key_fd(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _read_key_fd(fd: int) -> str:
    """Decode one keypress from raw ``fd`` via ``os.read`` — NEVER buffered
    ``sys.stdin``: the text wrapper's readahead swallows an arrow's trailing
    ``[X`` bytes into its internal buffer, so the ``select`` poll below would
    see an empty fd and misread every arrow as a lone ESC (= discard at the
    commit gate). A lone ESC is distinguished from an arrow escape-sequence
    by the short poll (an arrow's ``[X`` bytes arrive within the same
    keystroke)."""
    ch = os.read(fd, 1).decode(errors="replace")
    if ch in ("\r", "\n"):
        return _KEY_ENTER
    if ch == "\x1b":
        if select.select([fd], [], [], 0.05)[0]:
            seq = os.read(fd, 1).decode(errors="replace")
            if seq == "[":
                # Drain the WHOLE CSI sequence — parameter bytes end at a
                # final byte in 0x40-0x7E — so a modified arrow (Shift-Left =
                # ``\x1b[1;2D``) or Home/End (``\x1b[1~``) never leaks its
                # tail as spurious literal keypresses (a stray '1' would yank
                # the picker's rating).
                final = ""
                while select.select([fd], [], [], 0.05)[0]:
                    b = os.read(fd, 1).decode(errors="replace")
                    if not b:
                        break
                    if "\x40" <= b <= "\x7e":
                        final = b
                        break
                if final == "D":
                    return _KEY_LEFT
                if final == "C":
                    return _KEY_RIGHT
            return _KEY_ESC
        return _KEY_ESC
    return ch.lower()


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
    detail: str | None = None,
) -> tuple[bool, int | None]:
    """Interactive ★ number-line for the single commit gate. Arrow keys (or
    ``1``-``5``) move the selection, Enter commits with the selected ★,
    ``s`` commits unrated, ``n``/Esc discards. When ``detail`` is given
    (the untruncated post), ``d`` prints it and the picker resumes. Returns
    ``(commit, rating)`` exactly like :func:`manyagent.cli.ask_commit`'s typed
    fallback.

    ``key_fn``/``out`` are injectable for tests; the default reads real
    keystrokes via :func:`read_key` and redraws in place with ``\\r``."""
    keys = key_fn or read_key
    current = min(5, max(1, propose))

    def _stdout_write(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    write = out or _stdout_write
    hint = messages.COMMIT_PICKER_HINT_DETAIL if detail is not None else messages.COMMIT_PICKER_HINT
    write(render(Text(hint, style="dim")) + "\n")
    while True:
        write("\r\x1b[2K" + render(_star_line(question, current)))
        k = keys()
        if k in (_KEY_LEFT, "h") and current > 1:
            current -= 1
        elif k in (_KEY_RIGHT, "l") and current < 5:
            current += 1
        elif k in ("1", "2", "3", "4", "5"):
            current = int(k)
        elif k == "d" and detail is not None:
            # Expand: print the full post above, re-anchor the picker below.
            write("\n" + detail + "\n" + render(Text(hint, style="dim")) + "\n")
        elif k == _KEY_ENTER:
            write("\n")
            return True, current
        elif k == "s":
            write("\n")
            return True, None
        elif k in ("n", _KEY_ESC):
            write("\n")
            return False, None


# --------------------------------------------------------------------------- #
# the proposed-post panel (the commit gate's preview)
# --------------------------------------------------------------------------- #

# Confidence → color, matching the picker's "5★ = best" explicitness about
# which direction is better.
_CONFIDENCE_STYLE = {"high": "green", "medium": "yellow", "low": "red"}
# Same floor as the installer panels: below this, long values char-fold into
# unreadable shreds, so the panel overflows a pathologically narrow terminal
# instead.
_POST_PANEL_MIN_WIDTH = 60
# Display order: schema key → its catalog label (manyagent.forum.schema's
# REQUIRED_FIELDS plus the optional evidence_ref; utils cannot import forum).
_POST_FIELDS: tuple[tuple[str, str], ...] = (
    ("load_bearing_assumption", messages.POST_FIELD_LABEL_ASSUMPTION),
    ("evidence", messages.POST_FIELD_LABEL_EVIDENCE),
    ("evidence_ref", messages.POST_FIELD_LABEL_EVIDENCE_REF),
    ("proposed_next", messages.POST_FIELD_LABEL_PROPOSED_NEXT),
    ("predicted_outcome", messages.POST_FIELD_LABEL_PREDICTED_OUTCOME),
)
_OPTIONAL_POST_FIELDS = frozenset({"evidence_ref"})


def render_post(structured: object, *, kind: str = "reflection", full: bool = False) -> str:
    """The commit-gate preview of a parser-validated post: a labeled panel
    (one section per schema field, confidence as the colored subtitle) when
    the body is the falsifiable post-mortem shape, else — defensively; the
    parser ran first — syntax-highlighted JSON under the plain header.

    Each field wraps in full up to ``MANYAGENT_POST_PREVIEW_FIELD_CHARS`` (280)
    characters, then is cut at a word boundary with a dim ``… (+N chars)``
    marker; the commit gate offers the ``full=True`` rendering behind ``d``."""
    fields = _POST_FIELDS
    is_post_mortem = (
        isinstance(structured, dict)
        and isinstance(structured.get("confidence"), str)
        and all(
            isinstance(structured.get(key), str) and str(structured[key]).strip()
            for key, _ in fields
            if key not in _OPTIONAL_POST_FIELDS
        )
    )
    if not is_post_mortem or not isinstance(structured, dict):
        from rich.json import JSON

        return messages.POST_PROPOSED_HEADER + "\n" + render(JSON.from_data(structured))
    cap = (
        0
        if full
        else config.resolve("MANYAGENT_POST_PREVIEW_FIELD_CHARS", config.MANYAGENT_POST_PREVIEW_FIELD_CHARS, cast=int)
    )
    lines: list[RenderableType] = []
    for key, label in fields:
        val = structured.get(key)
        if val is None or not str(val).strip():
            continue  # evidence_ref: null is a valid, unrendered state
        if lines:
            lines.append(Text())
        lines.append(Text(label, style="bold cyan"))
        text = str(val)
        if 0 < cap < len(text):
            shown = text[:cap].rsplit(" ", 1)[0] or text[:cap]
            lines.append(Text.assemble(shown, (messages.POST_FIELD_MORE.format(n=len(text) - len(shown)), "dim")))
        else:
            lines.append(Text(text))
    conf = str(structured.get("confidence", "")).strip().lower()
    subtitle = Text.assemble((messages.POST_CONFIDENCE_PREFIX, "dim"), (conf, _CONFIDENCE_STYLE.get(conf, "bold")))
    panel = Panel(
        Group(*lines),
        title=messages.POST_PANEL_TITLE.format(kind=kind),
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style="cyan",
        expand=False,
        padding=(1, 2),
    )
    return render(panel, soft_wrap=False, min_width=_POST_PANEL_MIN_WIDTH)


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
