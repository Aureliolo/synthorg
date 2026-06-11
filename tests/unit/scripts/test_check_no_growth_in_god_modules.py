"""Unit tests for ``scripts/check_no_growth_in_god_modules.py``.

The gate's job: a diff that touches an explicit god-module allowlist
must NET-SHRINK that file. The unit tests inject staged/HEAD LOC
counts directly so the suite does not need a git repository.

The live allowlist is currently empty (``core/enums.py`` was dissolved
and ``events/persistence.py`` plus the api entries drained earlier), so
the pure-function tests monkeypatch a synthetic allowlist to exercise
the growth logic.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_growth_in_god_modules.py"

_FAKE = "src/synthorg/_fake_god_module.py"


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


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


@pytest.fixture
def allowlisted(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a synthetic single-entry allowlist and return its path."""
    monkeypatch.setattr(_GATE, "GOD_MODULE_ALLOWLIST", (_FAKE,))
    return _FAKE


# ── Allowlist is empty (no current god-modules) ─────────────────


def test_allowlist_is_empty() -> None:
    """No file is currently governed by the net-shrink gate.

    ``core/enums.py`` was dissolved into per-feature ``enums.py`` modules
    (its non-existence is enforced by ``check_no_central_junk_drawer.py``);
    ``events/persistence.py`` dissolved into a per-subdomain package; the
    five api entries drained when the controller decomposition brought
    them under their tier caps. All are now governed by
    ``check_module_size_budget.py``.
    """
    assert _GATE.GOD_MODULE_ALLOWLIST == ()


# ── classify_change pure function ───────────────────────────────


def test_net_shrink_passes(allowlisted: str) -> None:
    result = _GATE.classify_change(path=allowlisted, head_loc=2152, staged_loc=2100)
    assert result is None


def test_no_change_passes(allowlisted: str) -> None:
    result = _GATE.classify_change(path=allowlisted, head_loc=2152, staged_loc=2152)
    assert result is None


def test_net_grow_fails(allowlisted: str) -> None:
    result = _GATE.classify_change(path=allowlisted, head_loc=2152, staged_loc=2200)
    assert result is not None
    rendered = result.render()
    assert allowlisted in rendered
    assert "2152" in rendered
    assert "2200" in rendered


def test_one_loc_grow_fails(allowlisted: str) -> None:
    result = _GATE.classify_change(path=allowlisted, head_loc=2313, staged_loc=2314)
    assert result is not None
    assert "(+1)" in result.render()


def test_nonallowlisted_returns_none(allowlisted: str) -> None:
    """Files not in the allowlist are out of scope for this gate."""
    result = _GATE.classify_change(
        path="src/synthorg/some/random_file.py",
        head_loc=100,
        staged_loc=10_000,
    )
    assert result is None


# ── Newly-created allowlisted file ──────────────────────────────


def test_newallowlisted_file_passes(allowlisted: str) -> None:
    """An allowlisted file that did not exist in HEAD is allowed (creation)."""
    result = _GATE.classify_change(path=allowlisted, head_loc=None, staged_loc=10)
    assert result is None


# ── classify_paths wraps multiple files ─────────────────────────


def test_classify_paths_aggregates(allowlisted: str) -> None:
    def staged(path: str) -> int | None:
        return {allowlisted: 2200}.get(path)

    def head(path: str) -> int | None:
        return {allowlisted: 2152}.get(path)

    violations = _GATE.classify_paths(
        paths=(allowlisted,),
        read_staged_loc=staged,
        read_head_loc=head,
    )
    assert len(violations) == 1


def test_classify_paths_returns_empty_when_no_growth(allowlisted: str) -> None:
    def staged(path: str) -> int | None:
        return {allowlisted: 2100}.get(path)

    def head(path: str) -> int | None:
        return {allowlisted: 2152}.get(path)

    violations = _GATE.classify_paths(
        paths=(allowlisted,),
        read_staged_loc=staged,
        read_head_loc=head,
    )
    assert violations == []


def test_classify_paths_skips_unstaged(allowlisted: str) -> None:
    """If neither staged nor HEAD has the path, skip silently (no diff)."""

    def staged(path: str) -> int | None:
        return None

    def head(path: str) -> int | None:
        return None

    violations = _GATE.classify_paths(
        paths=(allowlisted,),
        read_staged_loc=staged,
        read_head_loc=head,
    )
    assert violations == []


# ── --list mode prints allowlist ────────────────────────────────


def test_main_list_mode_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = _GATE.main(["--list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    # The live allowlist is empty, so --list prints nothing.
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines == sorted(_GATE.GOD_MODULE_ALLOWLIST)


def test_main_list_mode_prints_allowlisted_paths(
    allowlisted: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """--list prints each allowlisted path (exercises the print loop)."""
    exit_code = _GATE.main(["--list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines == [allowlisted]
