"""M0 smoke tests: package import surface, lazy loading, entrypoints."""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

import oms


def test_version_and_setup_environment() -> None:
    assert isinstance(oms.__version__, str)
    assert oms.__version__ == "0.1.0"
    oms.setup_environment()  # idempotent, no oms.env present in test cwd


def test_dir_matches_all() -> None:
    # __dir__() returns __all__ verbatim; the builtin dir() sorts its result.
    assert oms.__dir__() == oms.__all__
    assert dir(oms) == sorted(oms.__all__)
    assert "utils" in oms.__all__
    assert "__version__" in oms.__all__
    assert "setup_environment" in oms.__all__


@pytest.mark.parametrize(
    "name",
    ["utils", "core", "bank", "capture", "adapters", "forum", "distill", "web"],
)
def test_lazy_submodule_access(name: str) -> None:
    mod = getattr(oms, name)
    assert mod is importlib.import_module(f"oms.{name}")
    # cached into globals() after first access
    assert getattr(oms, name) is mod


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = oms.does_not_exist  # type: ignore[attr-defined]


def test_cli_main_returns_zero() -> None:
    from oms.cli import main

    assert main([]) == 0


def test_cli_help_exits_zero() -> None:
    from oms.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_preflight_is_real_in_m8(monkeypatch: pytest.MonkeyPatch) -> None:
    """M8 replaced the M0 stub with real checks: a missing Bank URL now fails
    fast (nonzero). Full coverage lives in tests/test_preflight.py."""
    from oms.preflight import run_preflight

    monkeypatch.delenv("OMS_BANK_URL", raising=False)
    assert run_preflight() == 1


def test_pyi_mirrors_runtime_surface() -> None:
    """Guard against __init__.py ⇄ __init__.pyi drift as milestones add symbols.

    mypy trusts the stub, so a symbol added to _LAZY_IMPORTS/_SUBMODULES without
    a matching `as`-aliased import in the .pyi would silently break the static
    surface. This invariant must hold at every milestone boundary.
    """
    pyi_path = pathlib.Path(oms.__file__).with_suffix(".pyi")
    tree = ast.parse(pyi_path.read_text())
    aliased = {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname is not None
    }
    missing_lazy = set(oms._LAZY_IMPORTS) - aliased
    missing_subs = set(oms._SUBMODULES) - aliased
    assert not missing_lazy, f"_LAZY_IMPORTS not in __init__.pyi: {sorted(missing_lazy)}"
    assert not missing_subs, f"_SUBMODULES not in __init__.pyi: {sorted(missing_subs)}"
