"""Unit tests for ``scripts/check_no_central_junk_drawer.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_central_junk_drawer.py"

_ENUMS_REL = "src/synthorg/core/enums.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_no_central_junk_drawer",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


# ── Project scaffolding ─────────────────────────────────────────


def _make_project(tmp_path: Path) -> Path:
    """Materialise a synthetic project tree.

    ``core/enums.py`` is deliberately absent: it has been dissolved and
    must-not-exist enforcement expects it gone.
    """
    project = tmp_path
    (project / "src" / "synthorg" / "api").mkdir(parents=True)
    return project


# ── must-not-exist enforcement ──────────────────────────────────


def test_check_must_not_exist_passes_when_absent(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert _GATE.check_must_not_exist(project_root=project) == []


def test_check_must_not_exist_fails_when_recreated(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    enums = project / _ENUMS_REL
    enums.parent.mkdir(parents=True, exist_ok=True)
    enums.write_text("class A:\n    pass\n", encoding="utf-8")
    assert _GATE.check_must_not_exist(project_root=project) == [_ENUMS_REL]


def test_main_passes_when_enums_absent(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    exit_code = _GATE.main(["--project-root", str(project)])
    assert exit_code == 0


def test_main_fails_when_enums_recreated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _make_project(tmp_path)
    enums = project / _ENUMS_REL
    enums.parent.mkdir(parents=True, exist_ok=True)
    enums.write_text("class A:\n    pass\n", encoding="utf-8")
    exit_code = _GATE.main(["--project-root", str(project)])
    assert exit_code == 1
    assert "dissolved junk-drawer modules" in capsys.readouterr().err
