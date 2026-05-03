"""Tests for ``scripts/check_boundary_typed.py``.

Phase 3 of RFC #1711. Verifies that the AST gate accepts a boundary
function that calls ``parse_typed`` and rejects one that does not, and
honours the ``# lint-allow: boundary-typed`` per-line marker.
"""

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_boundary_typed.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "_check_boundary_typed",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _plant_fixture(content: str) -> Path:
    """Write a synthetic Python file under the repo for the gate to scan.

    The gate resolves paths relative to the repo root so a real file
    under ``src/synthorg/`` is required; per-test uniqueness avoids
    xdist worker collisions.
    """
    fixture_dir = _REPO_ROOT / "src" / "synthorg" / "_lint_boundary_fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    sample = fixture_dir / f"{uuid.uuid4().hex}.py"
    sample.write_text(content, encoding="utf-8")
    return sample


@pytest.mark.unit
class TestBoundaryTypedGate:
    def test_in_repo_status_is_clean(self) -> None:
        mod = _load_script_module()
        rc = mod.main()
        assert rc == 0, "registered boundaries no longer call parse_typed"

    def test_function_without_parse_typed_violates(self) -> None:
        sample = _plant_fixture(
            "def emit(payload):\n    return payload\n",
        )
        try:
            mod = _load_script_module()
            violations = mod._check_boundary(
                str(sample.relative_to(_REPO_ROOT)),
                "emit",
                "test",
            )
            assert len(violations) == 1
            assert "no longer calls parse_typed" in violations[0]
        finally:
            sample.unlink(missing_ok=True)

    def test_function_with_parse_typed_passes(self) -> None:
        sample = _plant_fixture(
            "def emit(payload):\n    return parse_typed('test', payload, object)\n",
        )
        try:
            mod = _load_script_module()
            violations = mod._check_boundary(
                str(sample.relative_to(_REPO_ROOT)),
                "emit",
                "test",
            )
            assert violations == []
        finally:
            sample.unlink(missing_ok=True)

    def test_opt_out_marker_silences_violation(self) -> None:
        sample = _plant_fixture(
            "def emit(payload):  # lint-allow: boundary-typed -- test fixture\n"
            "    return payload\n",
        )
        try:
            mod = _load_script_module()
            violations = mod._check_boundary(
                str(sample.relative_to(_REPO_ROOT)),
                "emit",
                "test",
            )
            assert violations == []
        finally:
            sample.unlink(missing_ok=True)

    def test_missing_function_reports_violation(self) -> None:
        sample = _plant_fixture(
            "def some_other_function():\n    return None\n",
        )
        try:
            mod = _load_script_module()
            violations = mod._check_boundary(
                str(sample.relative_to(_REPO_ROOT)),
                "expected_function",
                "test",
            )
            assert len(violations) == 1
            assert "not found" in violations[0]
        finally:
            sample.unlink(missing_ok=True)
