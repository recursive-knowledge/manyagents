"""M0 smoke tests: package import surface, lazy loading, entrypoints."""

from __future__ import annotations

import ast
import importlib
import os
import pathlib

import pytest

import manyagent


def test_version_and_setup_environment() -> None:
    assert isinstance(manyagent.__version__, str)
    assert manyagent.__version__ == "0.2.0"
    manyagent.setup_environment()  # idempotent, no manyagent.env present in test cwd


def test_setup_environment_expands_tilde_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A literal-tilde MANYAGENT_HOME (dotenv files / MCP-config env blocks don't
    shell-expand) must resolve to the same file `ma init` writes — the loader
    used to check the relative literal path and silently load nothing."""
    monkeypatch.setenv("HOME", str(tmp_path))  # posixpath.expanduser
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # ntpath.expanduser
    monkeypatch.setenv("MANYAGENT_HOME", "~/maghome")
    (tmp_path / "maghome").mkdir()
    (tmp_path / "maghome" / "env").write_text("MANYAGENT_TILDE_PROBE=hit\n", encoding="utf-8")
    monkeypatch.delenv("MANYAGENT_TILDE_PROBE", raising=False)
    manyagent.setup_environment()
    assert os.environ.get("MANYAGENT_TILDE_PROBE") == "hit"
    monkeypatch.delenv("MANYAGENT_TILDE_PROBE", raising=False)


def test_dir_matches_all() -> None:
    # __dir__() returns __all__ verbatim; the builtin dir() sorts its result.
    assert manyagent.__dir__() == manyagent.__all__
    assert dir(manyagent) == sorted(manyagent.__all__)
    assert "utils" in manyagent.__all__
    assert "__version__" in manyagent.__all__
    assert "setup_environment" in manyagent.__all__


@pytest.mark.parametrize(
    "name",
    ["utils", "core", "bank", "capture", "adapters", "forum", "distill", "web"],
)
def test_lazy_submodule_access(name: str) -> None:
    mod = getattr(manyagent, name)
    assert mod is importlib.import_module(f"manyagent.{name}")
    # cached into globals() after first access
    assert getattr(manyagent, name) is mod


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = manyagent.does_not_exist  # type: ignore[attr-defined]


def test_cli_main_returns_zero() -> None:
    from manyagent.cli import main

    assert main([]) == 0


def test_cli_help_exits_zero() -> None:
    from manyagent.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_preflight_is_real_in_m8(monkeypatch: pytest.MonkeyPatch) -> None:
    """M8 replaced the M0 stub with real checks: a missing Bank URL now fails
    fast (nonzero). Full coverage lives in tests/test_preflight.py."""
    from manyagent.preflight import run_preflight

    monkeypatch.delenv("MANYAGENT_BANK_URL", raising=False)
    assert run_preflight() == 1


def test_pyi_mirrors_runtime_surface() -> None:
    """Guard against __init__.py ⇄ __init__.pyi drift as milestones add symbols.

    mypy trusts the stub, so a symbol added to _LAZY_IMPORTS/_SUBMODULES without
    a matching `as`-aliased import in the .pyi would silently break the static
    surface. This invariant must hold at every milestone boundary.
    """
    pyi_path = pathlib.Path(manyagent.__file__).with_suffix(".pyi")
    tree = ast.parse(pyi_path.read_text())
    aliased = {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname is not None
    }
    missing_lazy = set(manyagent._LAZY_IMPORTS) - aliased
    missing_subs = set(manyagent._SUBMODULES) - aliased
    assert not missing_lazy, f"_LAZY_IMPORTS not in __init__.pyi: {sorted(missing_lazy)}"
    assert not missing_subs, f"_SUBMODULES not in __init__.pyi: {sorted(missing_subs)}"
