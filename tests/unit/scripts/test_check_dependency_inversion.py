"""Unit tests for ``scripts/check_dependency_inversion.py``.

Cover:

1. Imports of forbidden concrete persistence classes from
   ``api/`` / ``engine/`` / ``communication/`` are flagged.
2. The same import inside an allowlisted factory module is silent.
3. Per-line marker with non-empty justification suppresses; empty
   justification still reports.
4. ``persistence/`` itself is exempt (the gate only scans the three
   high-level prefixes).
5. Live-codebase anchor: ``_scan_all`` against the real tree returns
   zero violations after Phase 1-6.

The synthetic-tree tests deliberately do NOT initialise a git repo;
the gate's ``_git_tracked_python_files`` helper falls back to a
filesystem ``rglob`` when ``git ls-files`` fails, and that fallback
is the path exercised here. Keeping ``subprocess`` out of the test
seam avoids xdist-under-load races that show up only on the
isolation-replay gate (Windows + many parallel workers + ``git init``
running in nested git trees).
"""

import importlib.util
import textwrap
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_dependency_inversion.py"


def _load_script_module() -> Any:
    """Import the script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_dependency_inversion",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")


def _make_project(tmp_path: Path, *, files: dict[str, str]) -> Path:
    project_root = tmp_path / "project"
    for rel, source in files.items():
        _write(project_root / rel, source)
    return project_root


def _scan(project_root: Path, *, paths: list[str] | None = None) -> int:
    return _MODULE._scan_all(
        [Path(p) for p in (paths or ["src/synthorg"])],
        project_root,
    )


class TestForbiddenImports:
    def test_sqlite_config_in_api_is_flagged(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        project_root = _make_project(
            tmp_path,
            files={
                "src/synthorg/api/app.py": (
                    "from synthorg.persistence.config import SQLiteConfig\n"
                    "_ = SQLiteConfig\n"
                ),
            },
        )
        assert _scan(project_root) == 1
        captured = capsys.readouterr().out
        assert "SQLiteConfig" in captured
        assert "PersistenceConfig" in captured

    def test_postgres_persistence_backend_in_engine_is_flagged(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        project_root = _make_project(
            tmp_path,
            files={
                "src/synthorg/engine/foo.py": (
                    "from synthorg.persistence.postgres.backend "
                    "import PostgresPersistenceBackend\n"
                    "_ = PostgresPersistenceBackend\n"
                ),
            },
        )
        assert _scan(project_root) == 1
        assert "PersistenceBackend" in capsys.readouterr().out

    def test_filesystem_artifact_storage_in_communication_is_flagged(
        self,
        tmp_path: Path,
    ) -> None:
        project_root = _make_project(
            tmp_path,
            files={
                "src/synthorg/communication/foo.py": (
                    "from synthorg.persistence.filesystem_artifact_storage "
                    "import FileSystemArtifactStorage\n"
                    "_ = FileSystemArtifactStorage\n"
                ),
            },
        )
        assert _scan(project_root) == 1


class TestAllowlist:
    def test_factory_module_is_exempt(self, tmp_path: Path) -> None:
        # ``persistence/factory.py`` is on the allowlist; it legitimately
        # imports SQLitePersistenceBackend to construct the backend.
        project_root = _make_project(
            tmp_path,
            files={
                "src/synthorg/persistence/factory.py": (
                    "from synthorg.persistence.sqlite.backend "
                    "import SQLitePersistenceBackend\n"
                    "_ = SQLitePersistenceBackend\n"
                ),
                "src/synthorg/api/app.py": "x = 1\n",
            },
        )
        assert _scan(project_root) == 0

    def test_persistence_package_itself_is_out_of_scope(
        self,
        tmp_path: Path,
    ) -> None:
        # The gate only looks at api/ engine/ communication/. Anything
        # under persistence/ is its own concern.
        project_root = _make_project(
            tmp_path,
            files={
                "src/synthorg/persistence/foo.py": (
                    "from synthorg.persistence.config import SQLiteConfig\n"
                    "_ = SQLiteConfig\n"
                ),
            },
        )
        assert _scan(project_root) == 0


class TestPerLineOptOut:
    def test_marker_with_justification_suppresses(self, tmp_path: Path) -> None:
        project_root = _make_project(
            tmp_path,
            files={
                "src/synthorg/api/app.py": (
                    "from synthorg.persistence.config "
                    "import SQLiteConfig  "
                    "# lint-allow: dependency-inversion -- legacy fixture\n"
                    "_ = SQLiteConfig\n"
                ),
            },
        )
        assert _scan(project_root) == 0

    def test_marker_without_justification_still_reports(
        self,
        tmp_path: Path,
    ) -> None:
        project_root = _make_project(
            tmp_path,
            files={
                "src/synthorg/api/app.py": (
                    "from synthorg.persistence.config "
                    "import SQLiteConfig  "
                    "# lint-allow: dependency-inversion --\n"
                    "_ = SQLiteConfig\n"
                ),
            },
        )
        assert _scan(project_root) == 1


class TestLiveCodebase:
    """Anchor test: the real tree must pass after Phase 1-6."""

    def test_real_codebase_is_clean(self) -> None:
        violations = _MODULE._scan_all(
            [Path("src/synthorg")],
            _REPO_ROOT,
        )
        assert violations == 0
