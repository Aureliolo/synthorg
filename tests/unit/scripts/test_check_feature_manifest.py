"""Tests for the feature-manifest gate.

The gate enforces that every directory under ``src/synthorg/`` whose
``state.py`` (or peer ``*_state.py``) declares a class subclassing
``BaseFeatureStateSlice`` carries a sibling ``feature.py`` exposing a valid
``FEATURE: FeatureModule`` manifest. The substrate's own definition of a
"feature" (a directory with a typed state slice) is the contract; the gate
walks the tree, finds slice-bearing directories, and asserts each has a
matching ``feature.py``.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "check_feature_manifest.py"


class _GateModule(Protocol):
    """Subset of ``scripts/check_feature_manifest.py`` the tests drive."""

    @staticmethod
    def find_feature_directories(src_root: Path) -> list[Path]: ...
    @staticmethod
    def check(*, src_root: Path) -> list[str]: ...


def _load() -> _GateModule:
    spec = importlib.util.spec_from_file_location("check_feature_manifest", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_GATE = _load()

_SLICE_BODY = '''"""Test slice."""

from synthorg._core.features import BaseFeatureStateSlice


class FooStateSlice(BaseFeatureStateSlice):
    value: int | None = None
'''


_FEATURE_BODY = '''# module-kind: feature
"""Foo feature manifest."""

from synthorg._core.features import FeatureManifest, FeatureModule
from {pkg}.state import FooStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="foo",
    settings_namespace=None,
    state_slice=FooStateSlice,
)
'''


def _seed_slice_dir(src_root: Path, package: str = "foo") -> Path:
    """Create a synthorg/<package>/state.py that declares a slice."""
    pkg_dir = src_root / "synthorg" / package
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "state.py").write_text(_SLICE_BODY, encoding="utf-8")
    return pkg_dir


def _write_feature(pkg_dir: Path, *, body: str | None = None) -> None:
    """Write feature.py into *pkg_dir* (default: valid 'foo' manifest)."""
    package = "synthorg." + pkg_dir.name
    text = body if body is not None else _FEATURE_BODY.format(pkg=package)
    (pkg_dir / "feature.py").write_text(text, encoding="utf-8")


def test_find_feature_directories_locates_slice_bearing_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = _seed_slice_dir(src, "foo")
    found = _GATE.find_feature_directories(src)
    assert pkg in found


def test_find_feature_directories_ignores_non_slice_state_files(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    pkg = src / "synthorg" / "bar"
    pkg.mkdir(parents=True)
    (pkg / "state.py").write_text("class NotASlice:\n    pass\n", encoding="utf-8")
    found = _GATE.find_feature_directories(src)
    assert pkg not in found


def test_gate_fails_when_feature_py_missing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _seed_slice_dir(src, "foo")
    findings = _GATE.check(src_root=src)
    assert any("foo" in f and "feature.py" in f for f in findings)


def test_gate_passes_when_feature_py_present(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = _seed_slice_dir(src, "foo")
    _write_feature(pkg)
    findings = _GATE.check(src_root=src)
    assert findings == []


def test_gate_fails_when_feature_py_lacks_module_kind_header(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = _seed_slice_dir(src, "foo")
    body = _FEATURE_BODY.format(pkg="synthorg.foo").replace(
        "# module-kind: feature\n", ""
    )
    _write_feature(pkg, body=body)
    findings = _GATE.check(src_root=src)
    assert any("module-kind" in f for f in findings)


def test_gate_fails_when_loc_exceeds_feature_tier_cap(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = _seed_slice_dir(src, "foo")
    bloat = "\n".join(f"_x{i} = {i}" for i in range(200))
    body = _FEATURE_BODY.format(pkg="synthorg.foo") + "\n" + bloat + "\n"
    _write_feature(pkg, body=body)
    findings = _GATE.check(src_root=src)
    assert any("LOC" in f or "100" in f for f in findings)


def test_gate_fails_when_feature_attr_missing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    pkg = _seed_slice_dir(src, "foo")
    body = '# module-kind: feature\n"""docstring"""\n\nNOT_FEATURE = 1\n'
    _write_feature(pkg, body=body)
    findings = _GATE.check(src_root=src)
    assert any("FEATURE" in f for f in findings)


def test_gate_treats_repo_root_as_passing(tmp_path: Path) -> None:
    """An empty synthorg/ tree (no slice-bearing dirs) has zero findings."""
    src = tmp_path / "src"
    (src / "synthorg").mkdir(parents=True)
    findings = _GATE.check(src_root=src)
    assert findings == []
