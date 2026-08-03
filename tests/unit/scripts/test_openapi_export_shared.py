"""Tests for the shared OpenAPI export and its freshness check.

Reusing one hook's exported schema in another hook is only safe because
the consumer can prove the artefact matches the sources on disk. Every
test here is about that proof failing in the safe direction: a missing,
stale, corrupt or version-mismatched export must read as "rebuild it",
never as "the tree is fine". A false reuse is a gate passing against a
schema the current code does not produce, which is the exact failure the
gate exists to catch.
"""

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    script = _REPO_ROOT / "scripts" / "_openapi_export_shared.py"
    spec = importlib.util.spec_from_file_location("_openapi_export_shared", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load()

_SCHEMA: dict[str, object] = {"openapi": "3.1.0", "paths": {}}


@pytest.fixture
def export_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module's paths at a throwaway tree with one source file."""
    source_root = tmp_path / "src" / "synthorg"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("x = 1\n", encoding="utf-8")
    out = tmp_path / "docs" / "openapi"
    out.mkdir(parents=True)
    monkeypatch.setattr(_MODULE, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_MODULE, "_SOURCE_ROOT", source_root)
    monkeypatch.setattr(_MODULE, "OUTPUT_DIR", out)
    monkeypatch.setattr(_MODULE, "SCHEMA_FILE", out / "openapi.json")
    monkeypatch.setattr(_MODULE, "EXPORT_STATE_FILE", out / ".export-state.json")
    return out


def _export(schema: dict[str, object] | None = None) -> str:
    """Write a schema plus its state exactly as the producer does.

    Returns:
        The schema text written, so a caller can assert against it.
    """
    text = json.dumps(schema if schema is not None else _SCHEMA, indent=2) + "\n"
    _MODULE.SCHEMA_FILE.write_text(text, encoding="utf-8")
    _MODULE.write_export_state(text)
    return text


@pytest.mark.usefixtures("export_dir")
class TestFreshnessAcceptsACurrentExport:
    """The optimisation has to actually fire, or it is dead code."""

    def test_a_just_written_export_is_reused(self) -> None:
        _export()
        assert _MODULE.load_verified_schema() == _SCHEMA

    def test_reuse_survives_rereading_without_rewriting(self) -> None:
        _export()
        assert _MODULE.load_verified_schema() is not None
        assert _MODULE.load_verified_schema() is not None


@pytest.mark.usefixtures("export_dir")
class TestFreshnessRejectsAnythingElse:
    """Every rejection path degrades to "boot the app", never to a pass."""

    def test_no_export_at_all(self) -> None:
        assert _MODULE.load_verified_schema() is None

    def test_schema_without_state(self) -> None:
        _MODULE.SCHEMA_FILE.write_text(json.dumps(_SCHEMA), encoding="utf-8")
        assert _MODULE.load_verified_schema() is None

    def test_state_without_schema(self) -> None:
        _export()
        _MODULE.SCHEMA_FILE.unlink()
        assert _MODULE.load_verified_schema() is None

    def test_a_source_edited_after_the_export(self) -> None:
        # The whole point: the schema on disk no longer describes the code.
        _export()
        source = _MODULE._SOURCE_ROOT / "app.py"
        source.write_text("x = 2\n", encoding="utf-8")
        assert _MODULE.load_verified_schema() is None

    def test_a_new_source_file_appearing(self) -> None:
        _export()
        (_MODULE._SOURCE_ROOT / "extra.py").write_text("y = 1\n", encoding="utf-8")
        assert _MODULE.load_verified_schema() is None

    def test_a_source_file_disappearing(self) -> None:
        (_MODULE._SOURCE_ROOT / "extra.py").write_text("y = 1\n", encoding="utf-8")
        _export()
        (_MODULE._SOURCE_ROOT / "extra.py").unlink()
        assert _MODULE.load_verified_schema() is None

    def test_the_schema_edited_after_the_export(self) -> None:
        # Guards a half-written or hand-patched artefact, which the source
        # fingerprint alone would happily accept.
        text = _export()
        _MODULE.SCHEMA_FILE.write_text(text + "corrupted", encoding="utf-8")
        assert _MODULE.load_verified_schema() is None

    def test_a_state_file_from_a_future_format(self) -> None:
        _export()
        state = json.loads(_MODULE.EXPORT_STATE_FILE.read_text(encoding="utf-8"))
        state["version"] = state["version"] + 1
        _MODULE.EXPORT_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        assert _MODULE.load_verified_schema() is None

    def test_an_unparseable_state_file(self) -> None:
        _export()
        _MODULE.EXPORT_STATE_FILE.write_text("{not json", encoding="utf-8")
        assert _MODULE.load_verified_schema() is None

    def test_a_state_file_that_is_not_a_mapping(self) -> None:
        _export()
        _MODULE.EXPORT_STATE_FILE.write_text("[]", encoding="utf-8")
        assert _MODULE.load_verified_schema() is None

    def test_a_schema_that_is_not_a_mapping(self) -> None:
        text = "[]\n"
        _MODULE.SCHEMA_FILE.write_text(text, encoding="utf-8")
        _MODULE.write_export_state(text)
        assert _MODULE.load_verified_schema() is None


class TestHermeticEnv:
    """The export environment is scoped to the block, never leaked.

    Owned here rather than beside either consumer: both import this one
    implementation, so a second copy of these cases would drift from it
    without either copy failing.
    """

    _KEYS = (
        "SYNTHORG_DB_PATH",
        "SYNTHORG_DATABASE_URL",
        "SYNTHORG_PAGINATION_CURSOR_SECRET",
    )

    def test_sets_then_restores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in self._KEYS:
            monkeypatch.delenv(key, raising=False)
        with _MODULE.hermetic_env():
            assert os.environ["SYNTHORG_DB_PATH"] == ":memory:"
            assert os.environ["SYNTHORG_PAGINATION_CURSOR_SECRET"]
        for key in self._KEYS:
            assert key not in os.environ

    def test_an_operator_pinned_backend_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        pinned = str(tmp_path / "operator.db")
        monkeypatch.setenv("SYNTHORG_DB_PATH", pinned)
        monkeypatch.setenv("SYNTHORG_DATABASE_URL", "postgresql://operator")
        with _MODULE.hermetic_env():
            assert os.environ["SYNTHORG_DB_PATH"] == pinned
            assert os.environ["SYNTHORG_DATABASE_URL"] == "postgresql://operator"
        assert os.environ["SYNTHORG_DB_PATH"] == pinned

    def test_restores_when_the_block_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The export boots an app inside this block; a boot failure must
        # not leave the process pinned to an in-memory backend.
        for key in self._KEYS:
            monkeypatch.delenv(key, raising=False)

        def _raise_inside() -> None:
            with _MODULE.hermetic_env():
                assert os.environ["SYNTHORG_DB_PATH"] == ":memory:"
                msg = "boom"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="boom"):
            _raise_inside()
        for key in self._KEYS:
            assert key not in os.environ
