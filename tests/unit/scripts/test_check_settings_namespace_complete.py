# mypy: disable-error-code="explicit-any"
"""Unit tests for ``scripts/check_settings_namespace_complete.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_settings_namespace_complete.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_settings_namespace_complete", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE: Any = cast("Any", _load_gate())


def _make_enum(tmp_path: Path, namespaces: list[tuple[str, str]]) -> Path:
    """Create a synthetic SettingNamespace enum file under tmp_path."""
    body = "\n".join(f'    {name} = "{value}"' for name, value in namespaces)
    src = f"from enum import StrEnum\n\nclass SettingNamespace(StrEnum):\n{body}\n"
    path = tmp_path / "src" / "synthorg" / "settings" / "enums.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")
    return path


def _make_definitions(tmp_path: Path, names: list[str]) -> None:
    defs = tmp_path / "src" / "synthorg" / "settings" / "definitions"
    defs.mkdir(parents=True, exist_ok=True)
    for name in names:
        (defs / f"{name}.py").write_text("# definitions\n", encoding="utf-8")


# ── extract_namespaces ──────────────────────────────────────────


def test_extract_namespaces_basic(tmp_path: Path) -> None:
    _make_enum(tmp_path, [("FOO", "foo"), ("BAR", "bar")])
    namespaces = _GATE.extract_namespaces(tmp_path)
    assert namespaces == {"foo", "bar"}


def test_extract_namespaces_handles_missing_enum_file(tmp_path: Path) -> None:
    assert _GATE.extract_namespaces(tmp_path) == set()


# ── extract_definitions ─────────────────────────────────────────


def test_extract_definitions_lists_definition_files(tmp_path: Path) -> None:
    _make_definitions(tmp_path, ["foo", "bar", "__init__"])
    definitions = _GATE.extract_definitions(tmp_path)
    assert definitions == {"foo", "bar"}


def test_extract_definitions_skips_settings_ns_module(tmp_path: Path) -> None:
    """``settings_ns.py`` is the registry root, not a per-feature definition."""
    _make_definitions(tmp_path, ["foo", "settings_ns"])
    definitions = _GATE.extract_definitions(tmp_path)
    assert definitions == {"foo"}


# ── End-to-end check ────────────────────────────────────────────


def test_check_passes_when_all_namespaces_have_definitions(tmp_path: Path) -> None:
    _make_enum(tmp_path, [("FOO", "foo"), ("BAR", "bar")])
    _make_definitions(tmp_path, ["foo", "bar"])
    baseline = tmp_path / "scripts" / "_settings_namespace_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    assert _GATE.check(project_root=tmp_path, baseline_path=baseline) == []


def test_check_fails_for_namespace_without_definition(tmp_path: Path) -> None:
    _make_enum(tmp_path, [("FOO", "foo"), ("BAR", "bar")])
    _make_definitions(tmp_path, ["foo"])  # bar missing
    baseline = tmp_path / "scripts" / "_settings_namespace_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("", encoding="utf-8")
    violations = _GATE.check(project_root=tmp_path, baseline_path=baseline)
    assert len(violations) == 1
    assert "bar" in violations[0].render()


def test_baselined_missing_definition_passes(tmp_path: Path) -> None:
    _make_enum(tmp_path, [("FOO", "foo"), ("BAR", "bar")])
    _make_definitions(tmp_path, ["foo"])
    baseline = tmp_path / "scripts" / "_settings_namespace_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("bar\n", encoding="utf-8")
    assert _GATE.check(project_root=tmp_path, baseline_path=baseline) == []


# ── write_baseline ──────────────────────────────────────────────


def test_write_baseline_is_idempotent(tmp_path: Path) -> None:
    _make_enum(tmp_path, [("FOO", "foo"), ("BAR", "bar")])
    _make_definitions(tmp_path, ["foo"])
    baseline = tmp_path / "scripts" / "_settings_namespace_baseline.txt"
    baseline.parent.mkdir(parents=True)
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    first = baseline.read_text(encoding="utf-8")
    _GATE.write_baseline(project_root=tmp_path, baseline_path=baseline)
    second = baseline.read_text(encoding="utf-8")
    assert first == second
