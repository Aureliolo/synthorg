# mypy: disable-error-code="explicit-any"
"""Unit tests for ``scripts/check_state_slice_immutability.py``.

The gate ships with an empty baseline; these tests exercise the
synthetic-input pass / fail behaviour on tmp_path trees.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_state_slice_immutability.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_state_slice_immutability", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())


def _write(tmp_path: Path, content: str, name: str = "x.py") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ── Frozen + extra=forbid: PASS ─────────────────────────────────


def test_correct_state_slice_passes(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n"
        "class CharterStateSlice(BaseModel):\n"
        '    """A frozen slice."""\n'
        "    model_config = ConfigDict(frozen=True, extra='forbid')\n"
        "    field: int = 0\n"
    )
    findings = _GATE.find_state_slice_issues(_write(tmp_path, src))
    assert findings == []


def test_inherits_base_feature_state_slice_with_correct_config_passes(
    tmp_path: Path,
) -> None:
    src = (
        "from pydantic import ConfigDict\n"
        "class FooSlice(BaseFeatureStateSlice):\n"
        "    model_config = ConfigDict(frozen=True, extra='forbid')\n"
        "    field: int = 0\n"
    )
    findings = _GATE.find_state_slice_issues(_write(tmp_path, src))
    assert findings == []


# ── Missing frozen / extra=forbid: FAIL ─────────────────────────


def test_state_slice_without_model_config_fails(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel\n"
        "class CharterStateSlice(BaseModel):\n"
        "    field: int = 0\n"
    )
    findings = _GATE.find_state_slice_issues(_write(tmp_path, src))
    assert len(findings) == 1


def test_state_slice_with_frozen_false_fails(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n"
        "class CharterStateSlice(BaseModel):\n"
        "    model_config = ConfigDict(frozen=False, extra='forbid')\n"
    )
    findings = _GATE.find_state_slice_issues(_write(tmp_path, src))
    assert len(findings) == 1


def test_state_slice_without_extra_forbid_fails(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n"
        "class CharterStateSlice(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True)\n"
    )
    findings = _GATE.find_state_slice_issues(_write(tmp_path, src))
    assert len(findings) == 1


def test_state_slice_with_extra_allow_fails(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel, ConfigDict\n"
        "class CharterStateSlice(BaseModel):\n"
        "    model_config = ConfigDict(frozen=True, extra='allow')\n"
    )
    findings = _GATE.find_state_slice_issues(_write(tmp_path, src))
    assert len(findings) == 1


# ── Non-state-slice classes: out of scope ───────────────────────


def test_class_not_named_state_slice_is_ignored(tmp_path: Path) -> None:
    src = (
        "from pydantic import BaseModel\n"
        "class SomeRandomModel(BaseModel):\n"
        "    field: int = 0\n"
    )
    findings = _GATE.find_state_slice_issues(_write(tmp_path, src))
    assert findings == []


# ── End-to-end check ────────────────────────────────────────────


def test_check_passes_empty_tree(tmp_path: Path) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    baseline = tmp_path / "scripts" / "_state_slice_immutability_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    assert _GATE.check(project_root=tmp_path, baseline_path=baseline) == []


def test_check_fails_for_bad_state_slice(tmp_path: Path) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    (tmp_path / "src" / "synthorg" / "foo.py").write_text(
        (
            "from pydantic import BaseModel\n"
            "class CharterStateSlice(BaseModel):\n"
            "    field: int = 0\n"
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "scripts" / "_state_slice_immutability_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert len(violations) == 1
