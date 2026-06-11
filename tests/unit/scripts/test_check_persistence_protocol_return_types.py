"""Unit tests for ``scripts/check_persistence_protocol_return_types.py``.

Cover the three behaviours that matter:

1. **Comparison** -- the gate flips a violation when a backend property
   declares a concrete return type while the protocol declares the
   Protocol type, allows when both match, and tolerates the module-
   prefix difference (``persistence.task_protocol.TaskRepository`` vs
   ``TaskRepository``).
2. **Generic parameter** -- ``VersionRepository[T]`` matches but
   ``PostgresVersionRepository[T]`` does not.
3. **Per-line opt-out** -- the
   ``# lint-allow: persistence-protocol-uniformity -- <reason>`` marker
   suppresses, but only with a non-empty justification.

Plus an integration test that runs ``_scan_all`` against the real
codebase to lock in the cleaned tree and prevent any future
regression.

Tests load the script via ``importlib`` (mirroring
``test_check_persistence_boundary.py``) so private helpers are callable
directly without spawning subprocesses.
"""

import importlib.util
import textwrap
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_persistence_protocol_return_types.py"


def _load_script_module() -> Any:  # type: ignore[explicit-any]  # returns dynamically loaded gate module
    """Import the script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_persistence_protocol_return_types",
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


def _make_synthetic_tree(
    tmp_path: Path,
    *,
    sqlite_body: str,
    postgres_body: str,
) -> Path:
    """Lay out a minimal protocol + two backends under *tmp_path*.

    The protocol declares two properties (``tasks``, ``users``) and
    the backends are populated from caller-supplied bodies so each
    test can assemble the violation shape it cares about.
    """
    project_root = tmp_path / "project"
    persistence_root = project_root / "src" / "synthorg" / "persistence"
    _write(
        persistence_root / "protocol.py",
        """
        from typing import Protocol


        class TaskRepository: ...

        class UserRepository: ...


        class PersistenceBackend(Protocol):
            @property
            def tasks(self) -> TaskRepository: ...

            @property
            def users(self) -> UserRepository: ...
        """,
    )
    _write(persistence_root / "sqlite" / "backend.py", sqlite_body)
    _write(persistence_root / "postgres" / "backend.py", postgres_body)
    return project_root


def _scan(project_root: Path) -> int:
    count: int = _MODULE._scan_all(project_root)
    return count


class TestComparison:
    def test_protocol_match_passes(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        sqlite_body = """
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from project.src.synthorg.persistence.protocol import (
                TaskRepository,
                UserRepository,
            )


        class SQLitePersistenceBackend:
            @property
            def tasks(self) -> TaskRepository: ...

            @property
            def users(self) -> UserRepository: ...
        """
        postgres_body = sqlite_body.replace(
            "SQLitePersistenceBackend",
            "PostgresPersistenceBackend",
        )
        project_root = _make_synthetic_tree(
            tmp_path, sqlite_body=sqlite_body, postgres_body=postgres_body
        )
        assert _scan(project_root) == 0
        assert capsys.readouterr().out == ""

    def test_concrete_return_flagged(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        sqlite_body = """
        class SQLiteTaskRepository: ...
        class UserRepository: ...


        class SQLitePersistenceBackend:
            @property
            def tasks(self) -> SQLiteTaskRepository: ...

            @property
            def users(self) -> UserRepository: ...
        """
        postgres_body = sqlite_body.replace(
            "SQLitePersistenceBackend",
            "PostgresPersistenceBackend",
        )
        project_root = _make_synthetic_tree(
            tmp_path, sqlite_body=sqlite_body, postgres_body=postgres_body
        )
        assert _scan(project_root) == 2
        captured = capsys.readouterr().out
        assert "tasks" in captured
        assert "SQLiteTaskRepository" in captured
        assert "TaskRepository" in captured

    def test_module_prefix_tolerated(self, tmp_path: Path) -> None:
        # ``persistence.task_protocol.TaskRepository`` should normalise to
        # ``TaskRepository`` and match the protocol declaration.
        sqlite_body = """
        class SQLitePersistenceBackend:
            @property
            def tasks(self) -> persistence.task_protocol.TaskRepository: ...

            @property
            def users(self) -> persistence.user_protocol.UserRepository: ...
        """
        postgres_body = sqlite_body.replace(
            "SQLitePersistenceBackend",
            "PostgresPersistenceBackend",
        )
        project_root = _make_synthetic_tree(
            tmp_path, sqlite_body=sqlite_body, postgres_body=postgres_body
        )
        assert _scan(project_root) == 0


class TestGenericParameter:
    def test_generic_concrete_subclass_flagged(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Override the synthetic protocol to use a generic so we can drive
        # the subscript-comparison path. Direct write because the helper
        # only knows the ``tasks`` / ``users`` shape.
        project_root = tmp_path / "project"
        persistence_root = project_root / "src" / "synthorg" / "persistence"
        _write(
            persistence_root / "protocol.py",
            """
            from typing import Generic, Protocol, TypeVar

            T = TypeVar("T")

            class VersionRepository(Generic[T]): ...
            class WorkflowDefinition: ...


            class PersistenceBackend(Protocol):
                @property
                def workflow_versions(
                    self,
                ) -> VersionRepository[WorkflowDefinition]: ...
            """,
        )
        good_body = """
            class WorkflowDefinition: ...
            class VersionRepository: ...

            class SQLitePersistenceBackend:
                @property
                def workflow_versions(
                    self,
                ) -> VersionRepository[WorkflowDefinition]: ...
            """
        bad_body = """
            class WorkflowDefinition: ...
            class PostgresVersionRepository: ...

            class PostgresPersistenceBackend:
                @property
                def workflow_versions(
                    self,
                ) -> PostgresVersionRepository[WorkflowDefinition]: ...
            """
        _write(persistence_root / "sqlite" / "backend.py", good_body)
        _write(persistence_root / "postgres" / "backend.py", bad_body)
        assert _scan(project_root) == 1
        captured = capsys.readouterr().out
        assert "PostgresVersionRepository[WorkflowDefinition]" in captured
        assert "VersionRepository[WorkflowDefinition]" in captured


class TestPerLineOptOut:
    def test_marker_with_justification_suppresses(self, tmp_path: Path) -> None:
        marker = "# lint-allow: persistence-protocol-uniformity -- legacy fixture"
        sqlite_body = f"""
        class SQLiteTaskRepository: ...
        class UserRepository: ...


        class SQLitePersistenceBackend:
            @property
            def tasks(self) -> SQLiteTaskRepository: ...  {marker}

            @property
            def users(self) -> UserRepository: ...
        """
        postgres_body = sqlite_body.replace(
            "SQLitePersistenceBackend",
            "PostgresPersistenceBackend",
        )
        project_root = _make_synthetic_tree(
            tmp_path, sqlite_body=sqlite_body, postgres_body=postgres_body
        )
        # Both backends carry the marker (postgres_body inherits it from
        # sqlite_body via replace()), so neither violation is reported.
        assert _scan(project_root) == 0

    def test_marker_without_justification_still_reports(
        self,
        tmp_path: Path,
    ) -> None:
        marker_no_reason = "# lint-allow: persistence-protocol-uniformity --"
        sqlite_body = f"""
        class SQLiteTaskRepository: ...
        class UserRepository: ...


        class SQLitePersistenceBackend:
            @property
            def tasks(self) -> SQLiteTaskRepository: ...  {marker_no_reason}

            @property
            def users(self) -> UserRepository: ...
        """
        postgres_body = sqlite_body.replace(
            "SQLitePersistenceBackend",
            "PostgresPersistenceBackend",
        )
        project_root = _make_synthetic_tree(
            tmp_path, sqlite_body=sqlite_body, postgres_body=postgres_body
        )
        # Empty justification fails the check, so both backends still report.
        assert _scan(project_root) == 2


class TestLiveCodebase:
    """Anchor test: the real tree must pass with zero violations."""

    def test_real_codebase_is_clean(self) -> None:
        # ``_scan_all`` prints to stdout; we only care about the count.
        assert _MODULE._scan_all(_REPO_ROOT) == 0
