"""oms._installer — the M11 transparency contract.

Adapters that install in-agent skills (`oms.adapters.skills.{claude,codex,gemini}`)
write files into the user's filesystem (`~/.claude/`, `~/.codex/`, `~/.gemini/`,
optionally `<cwd>/.claude/` etc.). The contract here is the same for every
adapter:

  1. **Plan first.** Each installer builds an :class:`InstallPlan` of
     :class:`FileOp` records — absolute paths, create-vs-merge semantics, the
     keys we add to a merged file, and a human-readable description.
  2. **Consent.** First run: print the plan and ask `[y/n/diff]`
     (``OMS_INSTALL_SKILLS=auto|prompt|deny`` overrides). Once recorded in the
     manifest, subsequent runs are silent and idempotent.
  3. **Apply atomically.** Every write is temp-file + ``os.replace``; JSON
     merges preserve the third-party keys already present; TOML merges go
     through ``tomlkit`` so comments and key order survive.
  4. **Manifest.** Every write is logged at ``$OMS_HOME/installed/<adapter>.json``
     with create-vs-merge, keys added, and the sha256 of the file at write time
     so :func:`uninstall` can reverse cleanly without disturbing third-party
     content (e.g. another MCP server the user installed in the same config).
  5. **Idempotency.** Re-running an install is a no-op (twice == once
     byte-identical), tested.

There is no separate ``dry-run`` mode in the file API — :func:`apply_plan` takes
a ``dry_run`` flag that returns the manifest as it *would* look without
touching disk. The caller (CLI) prints it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

OpKind = Literal["create", "merge"]
Scope = Literal["user", "project"]

# OMS_INSTALL_SKILLS=auto|prompt|deny. Default: prompt on first run, then auto.
_PROMPT_MODES = {"auto", "prompt", "deny"}


@dataclass(frozen=True)
class FileOp:
    """One filesystem operation in an install plan."""

    kind: OpKind
    path: Path
    payload: str | dict[str, Any]
    description: str
    # For 'merge' ops: the top-level key(s) we own under this file. uninstall
    # pops exactly these and leaves everything else alone.
    merge_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CLIAction:
    """An external CLI invocation paired with its inverse (used when the
    target tool exposes an official register/unregister command — e.g.
    ``claude mcp add`` / ``claude mcp remove`` — that's more reliable than
    file-poking. ``install_argv`` runs at apply time; ``uninstall_argv`` at
    uninstall time. Both are logged transparently.

    ``stdin_input`` (optional) is piped to the install command's stdin. Use
    this for CLIs whose only non-interactive escape is an interactive prompt
    we can answer (e.g. gemini's workspace-trust prompt — see the M11.3
    Gemini installer where ``"1\\n"`` selects "Trust folder")."""

    install_argv: tuple[str, ...]
    uninstall_argv: tuple[str, ...]
    description: str
    stdin_input: str | None = None


@dataclass
class InstallPlan:
    adapter: str
    scope: Scope
    ops: list[FileOp]
    cli_actions: list[CLIAction] = field(default_factory=list)
    session_id: str | None = None


@dataclass
class ManifestEntry:
    kind: OpKind
    path: str
    description: str
    merge_keys: list[str] = field(default_factory=list)
    # sha256 of the file when we last wrote it; on uninstall we refuse to
    # delete/restore if the user has edited it since.
    sha256_after: str = ""
    sha256_before: str | None = None  # for 'merge' only — pre-write hash


@dataclass
class ManifestCLIEntry:
    """Audit record of an external CLI invocation we ran at install time, plus
    the inverse command :func:`uninstall` will run to reverse it."""

    install_argv: list[str]
    uninstall_argv: list[str]
    description: str
    ran: bool = True  # False if the binary was absent and we skipped


@dataclass
class Manifest:
    adapter: str
    scope: Scope
    installed_at: str
    session_id: str | None
    entries: list[ManifestEntry] = field(default_factory=list)
    cli_entries: list[ManifestCLIEntry] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# atomic write + hashing
# --------------------------------------------------------------------------- #


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via tempfile + ``os.replace`` (atomic on
    POSIX). Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):  # pragma: no cover — cleanup on rare failure
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------- #
# JSON + TOML idempotent merges (preserve third-party content)
# --------------------------------------------------------------------------- #


def _trailing_nl(prev: str | None) -> str:
    """Preserve the original file's trailing-newline policy (or default to a
    POSIX-style trailing newline for a fresh file). Transparency contract:
    install→uninstall must leave the user's file byte-identical, including
    whether it ends with ``\\n`` or not."""
    return "\n" if (prev is None or prev.endswith("\n")) else ""


def merge_json_keys(path: Path, top_key: str, our_key: str, our_value: Any) -> tuple[str, str | None]:
    """Idempotent upsert of ``data[top_key][our_key] = our_value``. Preserves
    everything else. Returns (new_text, prev_text-or-None)."""
    prev = _read_text(path)
    data: dict[str, Any] = json.loads(prev) if prev else {}
    bucket = data.setdefault(top_key, {})
    if not isinstance(bucket, dict):
        raise TypeError(f"{path} {top_key!r} is not a JSON object")
    bucket[our_key] = our_value
    return json.dumps(data, indent=2) + _trailing_nl(prev), prev


def merge_json_flat_key(path: Path, key: str, value: Any) -> tuple[str, str | None]:
    """Idempotent upsert of ``data[key] = value`` in a FLAT JSON object (no
    nesting). Used for files like ``~/.gemini/trustedFolders.json`` whose
    shape is ``{"/abs/path": "TRUST_FOLDER", …}``. Preserves siblings."""
    prev = _read_text(path)
    data: dict[str, Any] = json.loads(prev) if prev else {}
    if not isinstance(data, dict):
        raise TypeError(f"{path} is not a flat JSON object")
    data[key] = value
    return json.dumps(data, indent=2) + _trailing_nl(prev), prev


def unmerge_json_flat_keys(path: Path, our_keys: list[str]) -> str | None:
    """Pop ``our_keys`` from a flat JSON object. Returns the new file text
    (or ``None`` if the file became empty so the caller can delete it)."""
    prev = _read_text(path)
    if prev is None:
        return None
    data: dict[str, Any] = json.loads(prev)
    for k in our_keys:
        data.pop(k, None)
    if not data:
        return None
    return json.dumps(data, indent=2) + _trailing_nl(prev)


def _trailing_nl_unmerge(prev: str) -> str:
    """For uninstall paths: preserve the file's trailing newline as-was."""
    return "\n" if prev.endswith("\n") else ""


def unmerge_json_keys(path: Path, top_key: str, our_keys: list[str]) -> str | None:
    """Pop ``our_keys`` from ``data[top_key]``. Returns the new file text
    (or None to delete the file if it became empty). Preserves all other
    content."""
    prev = _read_text(path)
    if prev is None:
        return None
    data: dict[str, Any] = json.loads(prev)
    bucket = data.get(top_key)
    if isinstance(bucket, dict):
        for k in our_keys:
            bucket.pop(k, None)
        if not bucket:
            data.pop(top_key, None)
    if not data:
        return None  # caller deletes the file
    return json.dumps(data, indent=2) + _trailing_nl_unmerge(prev)


def merge_toml_section(path: Path, section_path: str, value: dict[str, Any]) -> tuple[str, str | None]:
    """Idempotent upsert of a TOML table at ``section_path`` (e.g.
    ``mcp_servers.oms``). Comments and key order of the surrounding document
    are preserved by tomlkit. Returns (new_text, prev_text-or-None)."""
    import tomlkit

    prev = _read_text(path)
    doc = tomlkit.parse(prev) if prev else tomlkit.document()
    parts = section_path.split(".")
    cursor: Any = doc
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = tomlkit.table()
        cursor = cursor[part]
    cursor[parts[-1]] = value
    return tomlkit.dumps(doc), prev


def unmerge_toml_section(path: Path, section_path: str) -> str | None:
    """Remove the TOML table at ``section_path``. Preserves the surrounding
    document. Returns the new file text or None to delete the file if it
    became empty (after pruning empty parents we created)."""
    import tomlkit

    prev = _read_text(path)
    if prev is None:
        return None
    doc = tomlkit.parse(prev)
    parts = section_path.split(".")

    def _remove(cursor: Any, rest: list[str]) -> None:
        if not rest:
            return
        head = rest[0]
        if head not in cursor:
            return
        if len(rest) == 1:
            del cursor[head]
            return
        _remove(cursor[head], rest[1:])
        if hasattr(cursor[head], "items") and not list(cursor[head].items()):
            del cursor[head]  # prune empty parent

    _remove(doc, parts)
    text = tomlkit.dumps(doc).strip()
    return text + "\n" if text else None


# --------------------------------------------------------------------------- #
# the consent prompt
# --------------------------------------------------------------------------- #


def _format_plan(plan: InstallPlan) -> str:
    lines = [
        f"oms → install in-agent skills for {plan.adapter!r} (scope={plan.scope}):",
        "",
    ]
    for op in plan.ops:
        verb = "CREATE" if op.kind == "create" else "MERGE "
        lines.append(f"  [{verb}] {op.path}")
        lines.append(f"           {op.description}")
        if op.merge_keys:
            lines.append(f"           keys we own: {', '.join(op.merge_keys)}")
    lines.append("")
    lines.append("MERGE means we read the existing file, upsert only our keys, and write back atomically.")
    lines.append("CREATE writes a new file (oms owns it). `oms uninstall <adapter>` reverses cleanly.")
    return "\n".join(lines)


def _diff_plan(plan: InstallPlan) -> str:
    """A 'what would change' view — current vs. proposed for each path."""
    import difflib

    chunks: list[str] = []
    for op in plan.ops:
        before = _read_text(op.path) or ""
        if op.kind == "create":
            after = op.payload if isinstance(op.payload, str) else json.dumps(op.payload, indent=2) + "\n"
        else:
            # for merges we render the merged text the same way apply_plan will
            after = _render_merge(op) or ""
        d = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{op.path}",
            tofile=f"b/{op.path}",
            lineterm="",
        )
        chunks.append(f"=== {op.path} ===\n" + "".join(d))
    return "\n".join(chunks) or "(no changes)"


def _render_merge(op: FileOp) -> str | None:
    """Compute what a 'merge' op would write, without writing it. Returns
    None if the merge is a no-op (file already contains our key with the same
    value)."""
    payload = op.payload
    if not isinstance(payload, dict):
        return None
    if op.path.suffix == ".json":
        if "__flat_key__" in payload:  # flat dict (e.g. trustedFolders.json)
            new_text, _prev = merge_json_flat_key(
                op.path,
                payload["__flat_key__"],
                payload["__value__"],
            )
            return new_text
        top_key = payload["__top_key__"]
        our_key = payload["__our_key__"]
        our_value = payload["__value__"]
        new_text, _prev = merge_json_keys(op.path, top_key, our_key, our_value)
        return new_text
    if op.path.suffix == ".toml":
        section_path = payload["__section__"]
        value = payload["__value__"]
        new_text, _prev = merge_toml_section(op.path, section_path, value)
        return new_text
    return None


def consent_prompt(
    plan: InstallPlan,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    manifest_exists: bool = False,
) -> bool:
    """First-run consent gate. ``OMS_INSTALL_SKILLS`` env overrides:
    ``auto`` ⇒ silent yes; ``deny`` ⇒ silent no; ``prompt`` ⇒ always ask.
    Default: ask once (no manifest yet), then act like ``auto``."""
    mode = os.environ.get("OMS_INSTALL_SKILLS", "").strip().lower()
    if mode == "auto":
        return True
    if mode == "deny":
        output_fn("oms: OMS_INSTALL_SKILLS=deny — skipping skill install")
        return False
    if mode != "prompt" and manifest_exists:
        return True  # silent re-run after first consent
    if mode and mode not in _PROMPT_MODES:
        output_fn(f"oms: unknown OMS_INSTALL_SKILLS={mode!r}; treating as 'prompt'")

    output_fn(_format_plan(plan))
    while True:
        ans = input_fn("Proceed? [y]es / [n]o / [d]iff: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no", ""):
            output_fn("oms: install declined")
            return False
        if ans in ("d", "diff"):
            output_fn(_diff_plan(plan))
            continue
        output_fn("(unrecognized — type y, n, or d)")


# --------------------------------------------------------------------------- #
# apply / uninstall / status
# --------------------------------------------------------------------------- #


def _manifest_path(adapter: str, oma_home: Path) -> Path:
    return oma_home / "installed" / f"{adapter}.json"


def load_manifest(adapter: str, oma_home: Path) -> Manifest | None:
    p = _manifest_path(adapter, oma_home)
    text = _read_text(p)
    if text is None:
        return None
    raw = json.loads(text)
    raw["entries"] = [ManifestEntry(**e) for e in raw.get("entries", [])]
    raw["cli_entries"] = [ManifestCLIEntry(**e) for e in raw.get("cli_entries", [])]
    return Manifest(**raw)


def save_manifest(manifest: Manifest, oma_home: Path) -> None:
    p = _manifest_path(manifest.adapter, oma_home)
    payload = asdict(manifest)
    _atomic_write(p, json.dumps(payload, indent=2) + "\n")


def apply_plan(plan: InstallPlan, *, oma_home: Path, dry_run: bool = False) -> Manifest:
    """Execute the plan. Returns the manifest of what was (or would be)
    written. ``dry_run=True`` skips disk writes and skips the manifest save."""
    entries: list[ManifestEntry] = []
    for op in plan.ops:
        if op.kind == "create":
            content = op.payload if isinstance(op.payload, str) else json.dumps(op.payload, indent=2) + "\n"
            if not dry_run:
                _atomic_write(op.path, content)
            entries.append(
                ManifestEntry(
                    kind="create",
                    path=str(op.path),
                    description=op.description,
                    sha256_after=_sha256(content),
                )
            )
        elif op.kind == "merge":
            assert isinstance(op.payload, dict)  # noqa: S101 — invariant
            prev = _read_text(op.path)
            new_text = _render_merge(op)
            assert new_text is not None  # noqa: S101 — invariant for known suffixes
            if not dry_run:
                _atomic_write(op.path, new_text)
            entries.append(
                ManifestEntry(
                    kind="merge",
                    path=str(op.path),
                    description=op.description,
                    merge_keys=list(op.merge_keys),
                    sha256_before=_sha256(prev) if prev else None,
                    sha256_after=_sha256(new_text),
                )
            )
    cli_entries: list[ManifestCLIEntry] = []
    for action in plan.cli_actions:
        argv = list(action.install_argv)
        bin_path = shutil.which(argv[0]) if argv else None
        if bin_path is None:
            cli_entries.append(
                ManifestCLIEntry(
                    install_argv=argv,
                    uninstall_argv=list(action.uninstall_argv),
                    description=action.description,
                    ran=False,
                )
            )
            continue
        if not dry_run:
            _run_cli(argv, description=action.description, stdin_input=action.stdin_input)
        cli_entries.append(
            ManifestCLIEntry(
                install_argv=argv,
                uninstall_argv=list(action.uninstall_argv),
                description=action.description,
                ran=True,
            )
        )

    manifest = Manifest(
        adapter=plan.adapter,
        scope=plan.scope,
        installed_at=datetime.now().astimezone().isoformat(),
        session_id=plan.session_id,
        entries=entries,
        cli_entries=cli_entries,
    )
    if not dry_run:
        save_manifest(manifest, oma_home)
    return manifest


def _run_cli(argv: list[str], *, description: str, stdin_input: str | None = None) -> None:
    """Run an external command, swallowing benign nonzero exits (e.g. "remove
    a server that isn't there") with a printed note rather than raising.
    Generous 120s timeout since some agent CLIs (``gemini extensions install``)
    do non-trivial setup; surface stderr on failure so the user can diagnose.
    ``stdin_input`` is piped to the child for CLIs whose only non-interactive
    escape is answering a prompt."""
    import subprocess

    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            input=stdin_input,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"oms: {description} — command failed ({type(exc).__name__}: {exc})")
        return
    if proc.returncode != 0:
        # Print stderr (truncated) so the user can act on the real cause —
        # silent nonzero exits hide real bugs (the M11.3 `--consent` lesson).
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        if tail:
            print(f"oms: {description} — exit {proc.returncode}: {tail[-1]}")


def uninstall(  # noqa: C901 — three reversal paths (CLI actions, create files, merge files: flat or nested) in sequence; refactoring would just shuffle the same complexity
    adapter: str,
    oma_home: Path,
    *,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Reverse the install via the saved manifest. CREATEd files are deleted
    iff their checksum still matches what we wrote (otherwise left in place —
    we don't touch user-edited content). MERGE entries are reopened, our keys
    popped, written back atomically (or the file deleted if it became empty
    AND we created it). Returns 0 on success, 1 if the manifest is missing."""
    manifest = load_manifest(adapter, oma_home)
    if manifest is None:
        output_fn(f"oms: no install manifest for {adapter!r} — nothing to do")
        return 1

    # Run the external CLI uninstalls FIRST so the agent can unregister cleanly
    # while our bundle is still on disk. Reversing this order (file ops first)
    # leaves the agent looking at a broken symlink/dangling path and its
    # uninstall command refuses (the M11.3 Gemini lesson).
    for ce in manifest.cli_entries:
        if not ce.ran:
            continue
        argv = list(ce.uninstall_argv)
        bin_path = shutil.which(argv[0]) if argv else None
        if bin_path is None:
            output_fn(f"  SKIPPED  {' '.join(argv)}  ({argv[0]} not on PATH; remove manually)")
            continue
        _run_cli(argv, description=f"reverse: {ce.description}")
        output_fn(f"  RAN      {' '.join(argv)}  (reverse: {ce.description})")

    for entry in manifest.entries:
        path = Path(entry.path)
        cur = _read_text(path)
        if cur is None:
            output_fn(f"  (gone)   {path}")
            continue
        if entry.kind == "create":
            if _sha256(cur) == entry.sha256_after:
                path.unlink()
                output_fn(f"  REMOVED  {path}")
            else:
                output_fn(f"  KEPT     {path}  (user-edited since install — left in place)")
        else:  # merge
            if path.suffix == ".json":
                # Flat-key merges (e.g. ~/.gemini/trustedFolders.json) are tagged
                # ``flat:<abs-path>`` in merge_keys; 2-level merges encode as
                # ``<top_key>.<our_key>``. Dispatch on the prefix.
                flat_keys = [k.removeprefix("flat:") for k in entry.merge_keys if k.startswith("flat:")]
                nested = [k for k in entry.merge_keys if not k.startswith("flat:")]
                if flat_keys:
                    new_text = unmerge_json_flat_keys(path, flat_keys)
                    if new_text is None:
                        path.unlink()
                        output_fn(f"  REMOVED  {path}  (became empty after popping our keys)")
                    else:
                        _atomic_write(path, new_text)
                        output_fn(f"  UNMERGED {path}  (popped {flat_keys})")
                elif nested:
                    top_key = nested[0].split(".")[0]
                    our_keys = [k.split(".", 1)[1] for k in nested if "." in k]
                    new_text = unmerge_json_keys(path, top_key, our_keys)
                    if new_text is None:
                        path.unlink()
                        output_fn(f"  REMOVED  {path}  (became empty after popping our keys)")
                    else:
                        _atomic_write(path, new_text)
                        output_fn(f"  UNMERGED {path}  (popped {our_keys})")
            elif path.suffix == ".toml":
                for section in entry.merge_keys:
                    new_text = unmerge_toml_section(path, section)
                    if new_text is None:
                        path.unlink()
                        output_fn(f"  REMOVED  {path}  (became empty)")
                        break
                    _atomic_write(path, new_text)
                output_fn(f"  UNMERGED {path}  (removed {entry.merge_keys})")
    # Remove the manifest itself.
    _manifest_path(adapter, oma_home).unlink(missing_ok=True)
    # Best-effort: prune empty install-root subdirs (`~/.claude/skills/oms-*`).
    for entry in manifest.entries:
        parent = Path(entry.path).parent
        with __import__("contextlib").suppress(OSError):
            if parent.is_dir() and not any(parent.iterdir()):
                shutil.rmtree(parent, ignore_errors=True)
    output_fn(f"oms: uninstalled {adapter} (manifest cleared)")
    return 0


def list_installed(oma_home: Path) -> list[Manifest]:
    """Every adapter that currently has an install manifest under
    ``$OMS_HOME/installed/``."""
    d = oma_home / "installed"
    if not d.is_dir():
        return []
    out: list[Manifest] = []
    for p in sorted(d.glob("*.json")):
        m = load_manifest(p.stem, oma_home)
        if m is not None:
            out.append(m)
    return out
