"""Unit tests for ``scripts/check_module_depth.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_module_depth.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_module_depth", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


# ── compute_depth ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("src/synthorg/foo.py", 0),
        ("src/synthorg/a/b.py", 1),
        ("src/synthorg/a/b/c.py", 2),
        ("src/synthorg/a/b/c/d.py", 3),
        ("src/synthorg/a/b/c/d/e.py", 4),
        ("src/synthorg/__init__.py", 0),
        ("src/synthorg/a/__init__.py", 1),
    ],
)
def test_compute_depth(rel: str, expected: int) -> None:
    assert _GATE.compute_depth(rel) == expected


# ── End-to-end check ────────────────────────────────────────────


def _materialise(project: Path, rels: list[str]) -> None:
    for rel in rels:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def test_check_passes_at_cap(tmp_path: Path) -> None:
    _materialise(tmp_path, ["src/synthorg/a/b/c/d.py"])  # depth 3
    baseline = tmp_path / "scripts" / "_module_depth_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline, cap=3)
    assert violations == []


def test_check_fails_beyond_cap(tmp_path: Path) -> None:
    _materialise(tmp_path, ["src/synthorg/a/b/c/d/e.py"])  # depth 4
    baseline = tmp_path / "scripts" / "_module_depth_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline, cap=3)
    assert len(violations) == 1


def test_baselined_violation_passes(tmp_path: Path) -> None:
    _materialise(tmp_path, ["src/synthorg/a/b/c/d/e.py"])
    baseline = tmp_path / "scripts" / "_module_depth_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("src/synthorg/a/b/c/d/e.py:4\n", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline, cap=3)
    assert violations == []


def test_baselined_path_growing_deeper_fails(tmp_path: Path) -> None:
    """If a baselined file moves deeper, the gate fires."""
    _materialise(tmp_path, ["src/synthorg/a/b/c/d/e.py"])  # depth 4
    baseline = tmp_path / "scripts" / "_module_depth_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("src/synthorg/a/b/c/d/e.py:3\n", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline, cap=3)
    assert len(violations) == 1


# ── write_baseline idempotence ─────────────────────────────────


def test_write_baseline_is_idempotent(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        [
            "src/synthorg/a/b/c/d/deep1.py",
            "src/synthorg/a/b/c/d/deep2.py",
            "src/synthorg/a.py",
        ],
    )
    baseline = tmp_path / "scripts" / "_module_depth_baseline.txt"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline, cap=3)
    first = baseline.read_text(encoding="utf-8")
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline, cap=3)
    second = baseline.read_text(encoding="utf-8")
    assert first == second


def test_write_baseline_includes_only_violators(tmp_path: Path) -> None:
    _materialise(
        tmp_path,
        [
            "src/synthorg/a/b/c/d/deep.py",  # depth 4
            "src/synthorg/a/b/c.py",  # depth 2
        ],
    )
    baseline = tmp_path / "scripts" / "_module_depth_baseline.txt"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline, cap=3)
    text = baseline.read_text(encoding="utf-8")
    assert "src/synthorg/a/b/c/d/deep.py" in text
    assert "src/synthorg/a/b/c.py" not in text
