# mypy: disable-error-code="explicit-any"
"""Tests for scripts/check_doc_drift_counts.py."""

import importlib.util
import re
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest


def _import_script() -> ModuleType:
    """Import check_doc_drift_counts.py as a module."""
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "check_doc_drift_counts.py"
    )
    spec = importlib.util.spec_from_file_location("check_doc_drift_counts", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _import_script()


# Fixtures


@pytest.fixture
def fake_events_dir(tmp_path: Path) -> Generator[Path]:
    """Fake events package with three real modules + __init__.py."""
    events = tmp_path / "events"
    events.mkdir()
    (events / "__init__.py").write_text("", encoding="utf-8")
    (events / "api.py").write_text("API = 'api'\n", encoding="utf-8")
    (events / "budget.py").write_text("BUDGET = 'budget'\n", encoding="utf-8")
    (events / "tool.py").write_text("TOOL = 'tool'\n", encoding="utf-8")
    with patch.object(gate, "EVENT_MODULES_DIR", events):
        yield events


def _claim_with_text(
    tmp_path: Path,
    text: str,
    pattern: str = r"(\d+)\+ event constant modules",
) -> Any:
    """Write a doc fixture with the provided text and return a Claim for it."""
    doc = tmp_path / "fixture.md"
    doc.write_text(text, encoding="utf-8")
    # Override REPO_ROOT so claim.path resolves against tmp_path.
    return gate.Claim(
        path=doc.name,
        pattern=re.compile(pattern),
        label="fixture",
    )


# count_event_modules


@pytest.mark.unit
class TestCountEventModules:
    """count_event_modules returns the number of *.py files excluding __init__."""

    def test_excludes_init_module(self, fake_events_dir: Path) -> None:
        assert gate.count_event_modules() == 3

    def test_returns_zero_when_only_init(self, tmp_path: Path) -> None:
        empty = tmp_path / "events"
        empty.mkdir()
        (empty / "__init__.py").write_text("", encoding="utf-8")
        with patch.object(gate, "EVENT_MODULES_DIR", empty):
            assert gate.count_event_modules() == 0

    def test_returns_zero_when_dir_missing(self, tmp_path: Path) -> None:
        with patch.object(gate, "EVENT_MODULES_DIR", tmp_path / "missing"):
            assert gate.count_event_modules() == 0


# parse_claim


@pytest.mark.unit
class TestParseClaim:
    """parse_claim extracts the floor int from a doc, with helpful errors."""

    def test_extracts_simple_floor(self, tmp_path: Path) -> None:
        claim = _claim_with_text(tmp_path, "We have 100+ event constant modules.")
        with patch.object(gate, "REPO_ROOT", tmp_path):
            assert gate.parse_claim(claim) == 100

    def test_strips_thousands_separator(self, tmp_path: Path) -> None:
        claim = _claim_with_text(
            tmp_path,
            "We have 12,345+ event constant modules.",
            pattern=r"([\d,]+)\+ event constant modules",
        )
        with patch.object(gate, "REPO_ROOT", tmp_path):
            assert gate.parse_claim(claim) == 12345

    def test_raises_runtime_error_on_pattern_miss(self, tmp_path: Path) -> None:
        claim = _claim_with_text(tmp_path, "No matching claim here.")
        with (
            patch.object(gate, "REPO_ROOT", tmp_path),
            pytest.raises(RuntimeError, match="Could not find claim pattern"),
        ):
            gate.parse_claim(claim)

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        evil = gate.Claim(
            path="../../etc/passwd",
            pattern=re.compile(r"(\d+)"),
            label="evil",
        )
        with (
            patch.object(gate, "REPO_ROOT", tmp_path),
            pytest.raises(RuntimeError, match="escapes REPO_ROOT"),
        ):
            gate.parse_claim(evil)

    def test_unreadable_file_becomes_runtime_error(self, tmp_path: Path) -> None:
        missing = gate.Claim(
            path="absent.md",
            pattern=re.compile(r"(\d+)"),
            label="missing",
        )
        with (
            patch.object(gate, "REPO_ROOT", tmp_path),
            pytest.raises(RuntimeError, match="Could not read claim file"),
        ):
            gate.parse_claim(missing)


# main()


@pytest.mark.unit
class TestMain:
    """main() returns 0 on satisfied claims, 1 on drift or missing dir."""

    def test_ok_path(
        self,
        capsys: pytest.CaptureFixture[str],
        fake_events_dir: Path,
        tmp_path: Path,
    ) -> None:
        # Floor (3) equals actual (3 fake modules in fake_events_dir).
        claim = _claim_with_text(tmp_path, "3+ event constant modules in code.")
        ok = gate.Claim(
            path=claim.path,
            pattern=re.compile(r"^(\d+)\+"),
            label="ok",
        )
        with (
            patch.object(gate, "REPO_ROOT", tmp_path),
            patch.object(gate, "CLAIMS", (ok,)),
        ):
            assert gate.main() == 0
        out = capsys.readouterr().out
        assert "OK: all 1 claims satisfy floor <= actual" in out

    def test_failure_path_lists_failures(
        self,
        capsys: pytest.CaptureFixture[str],
        fake_events_dir: Path,
        tmp_path: Path,
    ) -> None:
        # Floor 999 but actual is 3 -> drift.
        claim = _claim_with_text(tmp_path, "We have 999+ event constant modules.")
        with (
            patch.object(gate, "REPO_ROOT", tmp_path),
            patch.object(gate, "CLAIMS", (claim,)),
        ):
            assert gate.main() == 1
        err = capsys.readouterr().err
        assert "Doc count drift detected" in err
        assert "999+" in err
        assert "actual is 3" in err

    def test_missing_events_dir_exits_one(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        with patch.object(gate, "EVENT_MODULES_DIR", tmp_path / "nope"):
            assert gate.main() == 1
        err = capsys.readouterr().err
        assert "event modules directory not found" in err
