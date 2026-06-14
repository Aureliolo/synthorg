"""Tests for the feature-index freshness gate.

The gate byte-compares the committed ``feature_index.json`` against a
fresh regeneration. ``codebase_map.json`` is no longer committed (a
gitignored navigation artefact), so the gate validates the regenerated
map's structure and its cross-consistency with the index instead of
byte-comparing a committed file.
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
    """A drifted committed index fails the gate with a stale-specific finding.

    Injects a stub generator that returns a non-empty index so the gate runs
    past the loader and into the diff branch, then asserts the finding string
    names the stale artefact rather than just being non-empty (which could
    pass on an unrelated loader or parser failure).
    """
    fake = tmp_path / "fake_repo"
    (fake / "data").mkdir(parents=True)
    (fake / "data" / "feature_index.json").write_text(
        json.dumps({"schema_version": 1, "features": [], "generated_at": "stale"}),
        encoding="utf-8",
    )
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "generate_feature_index.py").write_text(
        # Stub generator returns a feature list that does NOT match the
        # committed empty-features artefact, so the diff branch must fire.
        # The map entry's owning_feature matches the index feature so the
        # cross-consistency check stays silent and only "stale" surfaces.
        """
class _StubIndex:
    @staticmethod
    def model_dump(mode="json"):
        return {
            "schema_version": 1,
            "features": [{"name": "drift"}],
            "generated_at": "x",
        }


def build_feature_index():
    return _StubIndex()


def build_codebase_map():
    return [{
        "module": "src/synthorg/x.py",
        "kind": "code",
        "loc_cap": 500,
        "loc": 1,
        "owning_feature": "drift",
    }]
""",
        encoding="utf-8",
    )
    findings = _GATE.check(repo_root=fake)
    assert any("stale" in finding for finding in findings), findings


def test_gate_fails_when_committed_index_is_not_a_json_object(tmp_path: Path) -> None:
    """A non-object JSON body (array, string, scalar) fails with a clean message.

    Exercises the ``isinstance(committed_index_raw, dict)`` guard so a
    truncated or accidentally-rewritten artefact gets a specific
    "not a JSON object (corrupt)" finding rather than a crash later in the
    diff comparison path. Uses a stub generator that returns a valid index
    object so the gate progresses past the loader and into the diff stage.
    """
    fake = tmp_path / "fake_repo"
    (fake / "data").mkdir(parents=True)
    (fake / "data" / "feature_index.json").write_text("[]", encoding="utf-8")
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "generate_feature_index.py").write_text(
        # Minimal stand-in: matches the generator's public surface but
        # returns a dict-shaped FeatureIndex dump and an empty module map.
        """
class _StubIndex:
    @staticmethod
    def model_dump(mode="json"):
        return {"schema_version": 1, "features": [], "generated_at": "x"}


def build_feature_index():
    return _StubIndex()


def build_codebase_map():
    return []
""",
        encoding="utf-8",
    )
    findings = _GATE.check(repo_root=fake)
    assert any("corrupt" in finding for finding in findings)


def _write_stub_repo(
    fake: Path,
    *,
    features: list[dict[str, object]],
    modules: list[dict[str, object]],
) -> None:
    """Write a fake repo whose generator returns the given index + map.

    The committed ``feature_index.json`` is written to match the stub's
    index dump (modulo ``generated_at``) so the index byte-compare passes
    and the test isolates the codebase-map validation branch.
    """
    (fake / "data").mkdir(parents=True)
    (fake / "data" / "feature_index.json").write_text(
        json.dumps(
            {"schema_version": 1, "features": features, "generated_at": "committed"}
        ),
        encoding="utf-8",
    )
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "generate_feature_index.py").write_text(
        f"""
import json

_FEATURES = {features!r}
_MODULES = {modules!r}


class _StubIndex:
    @staticmethod
    def model_dump(mode="json"):
        return {{
            "schema_version": 1,
            "features": _FEATURES,
            "generated_at": "x",
        }}


def build_feature_index():
    return _StubIndex()


def build_codebase_map():
    return _MODULES
""",
        encoding="utf-8",
    )


def test_gate_flags_codebase_map_owning_feature_not_in_index(tmp_path: Path) -> None:
    """A map entry naming an unknown owning_feature is a cross-consistency fail."""
    fake = tmp_path / "fake_repo"
    _write_stub_repo(
        fake,
        features=[{"name": "known"}],
        modules=[
            {
                "module": "src/synthorg/x.py",
                "kind": "code",
                "loc_cap": 500,
                "loc": 1,
                "owning_feature": "ghost",
            }
        ],
    )
    findings = _GATE.check(repo_root=fake)
    assert any("absent from" in finding for finding in findings), findings


def test_gate_flags_malformed_codebase_map_entry(tmp_path: Path) -> None:
    """A map entry missing required keys fails the structure check."""
    fake = tmp_path / "fake_repo"
    _write_stub_repo(
        fake,
        features=[],
        modules=[{"module": "src/synthorg/x.py"}],
    )
    findings = _GATE.check(repo_root=fake)
    assert any("missing keys" in finding for finding in findings), findings


def test_gate_flags_empty_codebase_map(tmp_path: Path) -> None:
    """An empty regenerated map is treated as corrupt."""
    fake = tmp_path / "fake_repo"
    _write_stub_repo(fake, features=[], modules=[])
    findings = _GATE.check(repo_root=fake)
    assert any("empty" in finding for finding in findings), findings
