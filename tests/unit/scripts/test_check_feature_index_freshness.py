"""Tests for the feature-index freshness gate.

The gate regenerates the AI-navigation artefacts to a scratch path and
asserts the committed files match byte-for-byte. Missing or stale
artefacts fail the gate, so the commit must include both files.
"""

import importlib.util
import json
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "check_feature_index_freshness.py"


class _GateModule(Protocol):
    """Subset of ``scripts/check_feature_index_freshness.py``."""

    FEATURE_INDEX_REL: Path
    CODEBASE_MAP_REL: Path

    @staticmethod
    def check(*, repo_root: Path) -> list[str]: ...


def _load() -> _GateModule:
    spec = importlib.util.spec_from_file_location(
        "check_feature_index_freshness", _SCRIPT
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_GATE = _load()


def test_gate_passes_on_current_repo() -> None:
    """Committed artefacts must round-trip cleanly."""
    findings = _GATE.check(repo_root=_REPO_ROOT)
    assert findings == [], "\n".join(findings)


def test_gate_fails_when_feature_index_missing(tmp_path: Path) -> None:
    """A repo with no committed index fails fail-closed."""
    fake = tmp_path / "fake_repo"
    fake.mkdir()
    findings = _GATE.check(repo_root=fake)
    assert findings, "missing files must be fail-closed"


def test_gate_fails_on_stale_feature_index(tmp_path: Path) -> None:
    """A drifted committed index fails the gate."""
    fake = tmp_path / "fake_repo"
    (fake / "data").mkdir(parents=True)
    (fake / "data" / "feature_index.json").write_text(
        json.dumps({"schema_version": 1, "features": [], "generated_at": "stale"}),
        encoding="utf-8",
    )
    (fake / "data" / "codebase_map.json").write_text(
        json.dumps({"modules": []}), encoding="utf-8"
    )
    findings = _GATE.check(repo_root=fake)
    assert findings, "stale committed index must fail"
