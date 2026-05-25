"""Unit tests for ``scripts/check_no_growth_in_god_modules.py``.

The gate's job: a diff that touches an explicit god-module allowlist
must NET-SHRINK that file. The unit tests inject staged/HEAD LOC
counts directly so the suite does not need a git repository.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_growth_in_god_modules.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_no_growth_in_god_modules",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]


# ── Allowlist is the canonical set ──────────────────────────────


def test_allowlist_contains_expected_modules() -> None:
    """All seven allowlisted god-modules (api/app.py, api/state.py, ...) are present."""
    expected = {
        "src/synthorg/api/app.py",
        "src/synthorg/api/state.py",
        "src/synthorg/api/auto_wire.py",
        "src/synthorg/api/lifecycle.py",
        "src/synthorg/api/lifecycle_builder.py",
        "src/synthorg/core/enums.py",
        "src/synthorg/observability/events/persistence.py",
    }
    assert expected == set(_GATE.GOD_MODULE_ALLOWLIST)


# ── classify_change pure function ───────────────────────────────


def test_net_shrink_passes() -> None:
    result = _GATE.classify_change(
        path="src/synthorg/api/app.py", head_loc=2152, staged_loc=2100
    )
    assert result is None


def test_no_change_passes() -> None:
    result = _GATE.classify_change(
        path="src/synthorg/api/app.py", head_loc=2152, staged_loc=2152
    )
    assert result is None


def test_net_grow_fails() -> None:
    result = _GATE.classify_change(
        path="src/synthorg/api/app.py", head_loc=2152, staged_loc=2200
    )
    assert result is not None
    rendered = result.render()
    assert "src/synthorg/api/app.py" in rendered
    assert "2152" in rendered
    assert "2200" in rendered


def test_one_loc_grow_fails() -> None:
    result = _GATE.classify_change(
        path="src/synthorg/api/state.py", head_loc=2313, staged_loc=2314
    )
    assert result is not None


def test_non_allowlisted_returns_none() -> None:
    """Files not in the allowlist are out of scope for this gate."""
    result = _GATE.classify_change(
        path="src/synthorg/some/random_file.py",
        head_loc=100,
        staged_loc=10_000,
    )
    assert result is None


# ── Newly-created allowlisted file ──────────────────────────────


def test_new_allowlisted_file_passes() -> None:
    """An allowlisted file that did not exist in HEAD is allowed (creation)."""
    result = _GATE.classify_change(
        path="src/synthorg/api/app.py", head_loc=None, staged_loc=10
    )
    assert result is None


# ── classify_paths wraps multiple files ─────────────────────────


def test_classify_paths_aggregates() -> None:
    def staged(path: str) -> int | None:
        return {"src/synthorg/api/app.py": 2200}.get(path)

    def head(path: str) -> int | None:
        return {"src/synthorg/api/app.py": 2152}.get(path)

    violations = _GATE.classify_paths(
        paths=("src/synthorg/api/app.py",),
        read_staged_loc=staged,
        read_head_loc=head,
    )
    assert len(violations) == 1


def test_classify_paths_returns_empty_when_no_growth() -> None:
    def staged(path: str) -> int | None:
        return {"src/synthorg/api/app.py": 2100}.get(path)

    def head(path: str) -> int | None:
        return {"src/synthorg/api/app.py": 2152}.get(path)

    violations = _GATE.classify_paths(
        paths=("src/synthorg/api/app.py",),
        read_staged_loc=staged,
        read_head_loc=head,
    )
    assert violations == []


def test_classify_paths_skips_unstaged() -> None:
    """If neither staged nor HEAD has the path, skip silently (no diff)."""

    def staged(path: str) -> int | None:
        return None

    def head(path: str) -> int | None:
        return None

    violations = _GATE.classify_paths(
        paths=("src/synthorg/api/app.py",),
        read_staged_loc=staged,
        read_head_loc=head,
    )
    assert violations == []


# ── --list mode prints allowlist ────────────────────────────────


def test_main_list_mode_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = _GATE.main(["--list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    # Output contains every allowlisted path sorted lexically
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines == sorted(_GATE.GOD_MODULE_ALLOWLIST)
