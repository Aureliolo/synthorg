"""Unit tests for scripts/check_persistence_boundary.py.

Exercises the three things that matter: the driver-import matcher, the
raw-SQL literal matcher, and the ``# lint-allow: persistence-boundary
-- <reason>`` suppression marker (including its non-empty-justification
requirement).  Also covers the path-based boundary prefix logic and the
allowlist, since both carve out exceptions that regressions could
easily invalidate.

Tests load the script as a module and call its private helpers
directly rather than spawning subprocesses -- the script discovers its
project root from ``__file__``, so a subprocess invocation from tests
would scan the real source tree.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_persistence_boundary.py"


def _load_script_module() -> object:
    """Import the script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_persistence_boundary",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


# ── driver-import matcher ───────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import aiosqlite\n",
        "import sqlite3\n",
        "import psycopg\n",
        "import psycopg_pool\n",
        "from aiosqlite import Connection\n",
        "from sqlite3 import Row\n",
        "from psycopg.rows import dict_row\n",
        "from psycopg_pool import AsyncConnectionPool\n",
        "    import aiosqlite\n",  # indented inside a TYPE_CHECKING block
        "    from psycopg import Connection\n",
    ],
)
def test_driver_import_flagged_outside_boundary(source: str, tmp_path: Path) -> None:
    """Every forbidden driver import form produces a violation."""
    target = tmp_path / "leak.py"
    target.write_text(source, encoding="utf-8")
    issues = _MODULE._scan_file(target, "src/synthorg/leak.py")  # type: ignore[attr-defined]
    assert len(issues) == 1
    assert "imports" in issues[0]


def test_non_forbidden_import_is_silent(tmp_path: Path) -> None:
    """Unrelated imports must not trip the matcher."""
    target = tmp_path / "clean.py"
    target.write_text("import asyncio\nfrom dataclasses import dataclass\n")
    assert (
        _MODULE._scan_file(target, "src/synthorg/clean.py")  # type: ignore[attr-defined]
        == []
    )


def test_comment_lines_are_not_scanned(tmp_path: Path) -> None:
    """A comment that mentions a driver name must not trip the matcher."""
    target = tmp_path / "commented.py"
    target.write_text("# This uses aiosqlite internally, documented above.\n")
    assert (
        _MODULE._scan_file(target, "src/synthorg/commented.py")  # type: ignore[attr-defined]
        == []
    )


# ── raw SQL literal matcher ─────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        'query = "CREATE TABLE foo (id INT)"\n',
        'sql = "INSERT INTO foo VALUES (1)"\n',
        'stmt = "ALTER TABLE foo ADD COLUMN bar TEXT"\n',
        'cleanup = "DROP TABLE foo"\n',
        'upd = "UPDATE foo SET bar = 1"\n',
        'rm = "DELETE FROM foo"\n',
        # Case-insensitive
        'lc = "create table foo (id int)"\n',
    ],
)
def test_raw_sql_in_literal_flagged(source: str, tmp_path: Path) -> None:
    """Each DDL/DML keyword pattern inside a string literal is a violation."""
    target = tmp_path / "raw.py"
    target.write_text(source, encoding="utf-8")
    issues = _MODULE._scan_file(target, "src/synthorg/raw.py")  # type: ignore[attr-defined]
    assert len(issues) == 1
    assert "raw SQL" in issues[0]


def test_raw_sql_keywords_in_identifiers_not_flagged(tmp_path: Path) -> None:
    """``CREATE_TABLE_PATTERN`` must not look like ``CREATE TABLE ...``."""
    target = tmp_path / "idents.py"
    target.write_text(
        "CREATE_TABLE_PATTERN = compile('x')\n"
        "INSERTION_COUNT = 0\n"
        "DROP_ALL_LABEL = 'drop_all'\n",
    )
    assert (
        _MODULE._scan_file(target, "src/synthorg/idents.py")  # type: ignore[attr-defined]
        == []
    )


# ── suppression marker ──────────────────────────────────────────


def test_marker_with_justification_suppresses(tmp_path: Path) -> None:
    """Valid marker + non-empty justification silences the line."""
    target = tmp_path / "allowed.py"
    target.write_text(
        "import aiosqlite  "
        "# lint-allow: persistence-boundary -- legacy shim scheduled for removal\n",
    )
    assert (
        _MODULE._scan_file(target, "src/synthorg/allowed.py")  # type: ignore[attr-defined]
        == []
    )


def test_marker_without_justification_still_flags(tmp_path: Path) -> None:
    """Marker requires ``-- <reason>`` with non-empty reason."""
    target = tmp_path / "bad_marker.py"
    target.write_text(
        "import aiosqlite  # lint-allow: persistence-boundary\n",
    )
    assert (
        _MODULE._scan_file(target, "src/synthorg/bad.py")  # type: ignore[attr-defined]
        != []
    )


def test_marker_with_empty_justification_still_flags(tmp_path: Path) -> None:
    """``-- `` with only whitespace is treated as missing justification."""
    target = tmp_path / "empty_just.py"
    target.write_text(
        "import aiosqlite  # lint-allow: persistence-boundary --   \n",
    )
    assert (
        _MODULE._scan_file(target, "src/synthorg/empty.py")  # type: ignore[attr-defined]
        != []
    )


def test_marker_without_double_dash_still_flags(tmp_path: Path) -> None:
    """Marker alone (no ``--``) is not a valid suppression."""
    target = tmp_path / "no_dash.py"
    target.write_text(
        "import aiosqlite  # lint-allow: persistence-boundary legacy shim\n",
    )
    assert (
        _MODULE._scan_file(target, "src/synthorg/no_dash.py")  # type: ignore[attr-defined]
        != []
    )


# ── boundary prefix logic ───────────────────────────────────────


@pytest.mark.parametrize(
    "rel",
    [
        "src/synthorg/persistence/sqlite/session_repo.py",
        "src/synthorg/persistence/postgres/backend.py",
        "tests/conformance/persistence/test_user_repository.py",
        "tests/unit/persistence/test_config.py",
        "tests/integration/persistence/test_fresh_install_postgres_cli.py",
        "tests/unit/api/auth/test_session_store.py",
        "tests/unit/backup/test_handlers/test_persistence_handler.py",
    ],
)
def test_persistence_prefixes_are_inside_boundary(rel: str) -> None:
    """Paths under the persistence tree are never flagged."""
    assert _MODULE._is_inside_boundary(rel)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "rel",
    [
        "src/synthorg/api/auth/controller.py",
        "src/synthorg/memory/org/hybrid_backend.py",
        "src/synthorg/ontology/versioning.py",
        "tests/unit/api/test_app.py",
    ],
)
def test_non_persistence_paths_are_outside_boundary(rel: str) -> None:
    """Ordinary application modules and tests are inside scope."""
    assert not _MODULE._is_inside_boundary(rel)  # type: ignore[attr-defined]


# ── allowlist covers documented exceptions ──────────────────────


def test_tools_database_modules_not_allowlisted() -> None:
    """The agent-facing SQL tools route through the persistence adapter.

    They no longer import a raw driver directly, so they are NOT on the
    boundary allowlist: the ``persistence.external_sql`` adapter owns the
    sanctioned driver access and the tools stay inside the boundary like
    any other application module.
    """
    allowlist = _MODULE._ALLOWLIST  # type: ignore[attr-defined]
    assert "src/synthorg/tools/database/schema_inspect.py" not in allowlist
    assert "src/synthorg/tools/database/sql_query.py" not in allowlist


def test_self_is_allowlisted() -> None:
    """The checker itself names drivers in patterns/messages."""
    assert (
        "scripts/check_persistence_boundary.py" in _MODULE._ALLOWLIST  # type: ignore[attr-defined]
    )


# ── mutation-log gate ───────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "logger.info(PERSISTENCE_USER_SAVED, user_id=user.id)\n",
        "logger.info(PERSISTENCE_USER_DELETED, user_id=uid)\n",
        "logger.debug(PERSISTENCE_TASK_SAVED, task_id=task.id)\n",
        "logger.warning(PERSISTENCE_USER_UPDATED, user_id=uid)\n",
        "logger.info(HR_TRAINING_PLAN_PERSISTED, plan_id=str(plan.id))\n",
        "logger.info(API_PROJECT_CREATED, project_id=p.id)\n",
        # Multi-line shape (canonical in the codebase).
        "logger.info(\n    PERSISTENCE_ARTIFACT_DELETED,\n    artifact_id=aid,\n)\n",
        "logger.debug(\n    PERSISTENCE_COST_RECORD_SAVED,\n    agent_id=a,\n)\n",
    ],
)
def test_mutation_log_inside_persistence_flagged(
    source: str,
    tmp_path: Path,
) -> None:
    """Every mutation-log shape inside persistence/ is a violation."""
    target = tmp_path / "repo.py"
    target.write_text(source, encoding="utf-8")
    issues = _MODULE._scan_persistence_mutation_logs(  # type: ignore[attr-defined]
        target,
        "src/synthorg/persistence/sqlite/example_repo.py",
    )
    assert len(issues) == 1
    assert "mutation audit log" in issues[0]
    assert "service layer" in issues[0]


@pytest.mark.parametrize(
    "source",
    [
        # Fetch / list / count telemetry is allowed at repo layer.
        "logger.debug(PERSISTENCE_USER_FETCHED, user_id=uid)\n",
        "logger.debug(PERSISTENCE_USER_LISTED, count=10)\n",
        "logger.debug(PERSISTENCE_TASK_COUNTED, count=5)\n",
        # Failure paths must stay (with WARNING).
        "logger.warning(PERSISTENCE_USER_SAVE_FAILED, user_id=uid)\n",
        "logger.warning(PERSISTENCE_TASK_DELETE_FAILED, task_id=tid)\n",
        "logger.warning(PERSISTENCE_MESSAGE_DUPLICATE, msg_id=mid)\n",
        # exception paths inside repos (event has no mutation suffix).
        "logger.exception(PERSISTENCE_USER_SAVE_FAILED, user_id=uid)\n",
        # Non-mutation event names that happen to mention SAVED in
        # context but not as a constant suffix.
        "logger.info('user saved successfully')\n",
        # AST-only safety: comment-only mentions of mutation-suffix
        # constants must not fire (regex scanner could false-positive).
        "# logger.info(PERSISTENCE_USER_SAVED) -- removed by #1234\n",
        # AST-only safety: docstring mentions of mutation constants
        # also must not fire.
        '"""Repo that used to call logger.info(PERSISTENCE_USER_SAVED)."""\n',
    ],
)
def test_non_mutation_logs_inside_persistence_not_flagged(
    source: str,
    tmp_path: Path,
) -> None:
    """Fetch / list / failure / exception logs at repo layer are fine."""
    target = tmp_path / "repo.py"
    target.write_text(source, encoding="utf-8")
    issues = _MODULE._scan_persistence_mutation_logs(  # type: ignore[attr-defined]
        target,
        "src/synthorg/persistence/sqlite/example_repo.py",
    )
    assert issues == []


@pytest.mark.parametrize(
    "source",
    [
        # Renamed module-level logger.
        "audit_log.info(PERSISTENCE_USER_SAVED, user_id=u.id)\n",
        # Instance-attribute logger (catches the ``self._logger`` shape
        # that the previous regex-based scanner missed).
        "self._logger.info(PERSISTENCE_USER_SAVED, user_id=u.id)\n",
        # Class-attribute logger.
        "cls.log.warning(PERSISTENCE_USER_DELETED, user_id=u.id)\n",
        # error / exception / critical levels (not in the original
        # info|debug|warning regex).
        "logger.error(PERSISTENCE_USER_DELETED, user_id=u.id)\n",
        "logger.critical(PERSISTENCE_USER_PERSISTED, user_id=u.id)\n",
        # Event passed via the ``event`` keyword.
        "logger.info(event=PERSISTENCE_USER_SAVED, user_id=u.id)\n",
        # Aliased import: ``from ... import EVENT as EVT`` then
        # ``logger.info(EVT, ...)``.  The alias resolver in
        # ``_build_alias_map`` rewrites ``EVT`` back to the original
        # constant name before the suffix check.
        (
            "from synthorg.observability.events.persistence import "
            "PERSISTENCE_USER_SAVED as EVT\n"
            "logger.info(EVT, user_id=u.id)\n"
        ),
        # Module-attribute access: ``logger.info(events.PERSISTENCE_FOO_SAVED, ...)``.
        # The Attribute resolver picks the rightmost identifier.
        "logger.info(events.PERSISTENCE_FOO_SAVED, user_id=u.id)\n",
        # Raw string literal (the most obvious escape hatch).  The
        # value-suffix check matches ``.saved`` / ``.created`` /
        # ``.updated`` / ``.deleted`` / ``.persisted`` regardless of
        # case so this fires.
        'logger.info("persistence.user.saved", user_id=u.id)\n',
        'logger.info("persistence.user.deleted", user_id=u.id)\n',
        # Mixed-case literal: matched case-insensitively.
        'logger.info("Persistence.User.Updated", user_id=u.id)\n',
        # Module-level reassignment: ``AUDIT_EVENT = PERSISTENCE_USER_SAVED``
        # then ``logger.info(AUDIT_EVENT, ...)``.  The assignment
        # resolver follows one level of indirection and recovers the
        # original constant name.
        (
            "from synthorg.observability.events.persistence import "
            "PERSISTENCE_USER_SAVED\n"
            "AUDIT_EVENT = PERSISTENCE_USER_SAVED\n"
            "logger.info(AUDIT_EVENT, user_id=u.id)\n"
        ),
        # Module-level reassignment with annotation
        # (``AUDIT_EVENT: Final = "persistence.user.saved"``).
        (
            "from typing import Final\n"
            'AUDIT_EVENT: Final = "persistence.user.saved"\n'
            "logger.info(AUDIT_EVENT, user_id=u.id)\n"
        ),
    ],
)
def test_ast_scanner_catches_logger_shapes_regex_missed(
    source: str,
    tmp_path: Path,
) -> None:
    """AST scan catches non-trivial logger shapes the old regex missed."""
    target = tmp_path / "repo.py"
    target.write_text(source, encoding="utf-8")
    issues = _MODULE._scan_persistence_mutation_logs(  # type: ignore[attr-defined]
        target,
        "src/synthorg/persistence/sqlite/example_repo.py",
    )
    assert len(issues) == 1, f"AST scan should flag {source!r}, got {issues}"
    assert "mutation audit log" in issues[0]


def test_ast_scanner_recovers_from_syntax_error(tmp_path: Path) -> None:
    """A file that fails to parse yields a single diagnostic, not a crash."""
    target = tmp_path / "broken.py"
    target.write_text("logger.info(PERSISTENCE_USER_SAVED  # missing close\n")
    issues = _MODULE._scan_persistence_mutation_logs(  # type: ignore[attr-defined]
        target,
        "src/synthorg/persistence/sqlite/broken_repo.py",
    )
    assert len(issues) == 1
    assert "unable to parse file" in issues[0]


def test_mutation_log_marker_suppresses_on_logger_line(tmp_path: Path) -> None:
    """The lint-allow marker on the ``logger.<level>(`` line silences.

    Uses a constant NOT in the allowlist so we exercise the marker
    path (the allowlist short-circuit already covers the lifecycle
    constants and is tested separately).
    """
    target = tmp_path / "repo.py"
    target.write_text(
        "logger.info(PERSISTENCE_USER_SAVED, user_id=u.id)  "
        "# lint-allow: persistence-boundary -- legacy shim, removed in #99\n",
        encoding="utf-8",
    )
    issues = _MODULE._scan_persistence_mutation_logs(  # type: ignore[attr-defined]
        target,
        "src/synthorg/persistence/sqlite/example_repo.py",
    )
    assert issues == []


def test_mutation_log_marker_suppresses_on_constant_line(tmp_path: Path) -> None:
    """For multi-line calls, marker on the constant line also silences."""
    target = tmp_path / "repo.py"
    target.write_text(
        "logger.info(\n"
        "    PERSISTENCE_USER_SAVED,  "
        "# lint-allow: persistence-boundary -- legacy shim removed in #99\n"
        "    user_id=user.id,\n"
        ")\n",
        encoding="utf-8",
    )
    issues = _MODULE._scan_persistence_mutation_logs(  # type: ignore[attr-defined]
        target,
        "src/synthorg/persistence/sqlite/example_repo.py",
    )
    assert issues == []


def test_mutation_log_allowlist_constants_silenced(tmp_path: Path) -> None:
    """Lifecycle/infra constants on the allowlist do not trip the gate.

    Imports the constants from the ``events.persistence`` sub-modules so a
    future rename in the events package is caught here -- the gate's in-script
    allowlist must stay in sync with the canonical names.
    """
    from synthorg.observability.events.persistence.artifact_storage import (
        PERSISTENCE_ARTIFACT_STORAGE_DELETED,
    )
    from synthorg.observability.events.persistence.backend import (
        PERSISTENCE_BACKEND_CREATED,
    )
    from synthorg.observability.events.persistence.timescaledb import (
        PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED,
    )

    # The allowlist is keyed on the constant *name* (the identifier used
    # in source), not the event-string value, so iterate names directly.
    for constant_name in [
        "PERSISTENCE_BACKEND_CREATED",
        "PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED",
        "PERSISTENCE_ARTIFACT_STORAGE_DELETED",
    ]:
        target = tmp_path / f"{constant_name}.py"
        target.write_text(
            f"logger.info({constant_name}, name='test')\n",
            encoding="utf-8",
        )
        issues = _MODULE._scan_persistence_mutation_logs(  # type: ignore[attr-defined]
            target,
            "src/synthorg/persistence/factory.py",
        )
        assert issues == [], f"constant {constant_name} should be allowed"

    # Pin the runtime values so a rename in events/persistence.py is
    # caught: if these fail, update the gate's allowlist constant names
    # in ``scripts/check_persistence_boundary.py`` to match.
    assert PERSISTENCE_BACKEND_CREATED == "persistence.backend.created"
    assert (
        PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED
        == "persistence.timescaledb.hypertable_created"
    )
    assert (
        PERSISTENCE_ARTIFACT_STORAGE_DELETED == "persistence.artifact_storage.deleted"
    )


def test_iter_persistence_targets_returns_only_persistence() -> None:
    """The persistence-target enumerator selects only persistence files."""
    # The function relies on git ls-files; testing it requires a real
    # git tree.  Verify the prefix constant instead -- any path under
    # ``src/synthorg/persistence/`` is in scope, anything else is not.
    prefix = _MODULE._PERSISTENCE_SRC_PREFIX  # type: ignore[attr-defined]
    assert prefix == "src/synthorg/persistence/"
    assert "src/synthorg/persistence/sqlite/user_repo.py".startswith(prefix)
    assert not "src/synthorg/api/controllers/projects.py".startswith(prefix)


# ── _resolve_project_root ───────────────────────────────────────


def test_resolve_project_root_returns_default_when_repo_root_none() -> None:
    """``--repo-root`` defaults to the script's parent directory."""
    resolved = _MODULE._resolve_project_root(None)  # type: ignore[attr-defined]
    assert isinstance(resolved, Path)
    assert resolved.is_dir()


def test_resolve_project_root_returns_resolved_path_for_valid_dir(
    tmp_path: Path,
) -> None:
    """A valid directory passed via ``--repo-root`` is returned resolved."""
    resolved = _MODULE._resolve_project_root(tmp_path)  # type: ignore[attr-defined]
    assert isinstance(resolved, Path)
    assert resolved == tmp_path.resolve(strict=True)


def test_resolve_project_root_raises_when_path_inaccessible(
    tmp_path: Path,
) -> None:
    """A non-existent ``--repo-root`` raises ``ProjectRootError``."""
    project_root_error = _MODULE.ProjectRootError  # type: ignore[attr-defined]
    missing = tmp_path / "does-not-exist"
    with pytest.raises(project_root_error, match="not accessible"):
        _MODULE._resolve_project_root(missing)  # type: ignore[attr-defined]


def test_resolve_project_root_raises_when_path_is_file(tmp_path: Path) -> None:
    """A regular file passed via ``--repo-root`` raises ``ProjectRootError``."""
    project_root_error = _MODULE.ProjectRootError  # type: ignore[attr-defined]
    file_path = tmp_path / "regular.txt"
    file_path.write_text("hello", encoding="utf-8")
    with pytest.raises(project_root_error, match="must be a directory"):
        _MODULE._resolve_project_root(file_path)  # type: ignore[attr-defined]


# ── _resolve_root containment ───────────────────────────────────


def test_resolve_root_returns_none_for_path_outside_project_root(
    tmp_path: Path,
) -> None:
    """Paths outside ``project_root`` return ``None`` (CodeQL sanitizer)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()

    resolved = _MODULE._resolve_root(outside, project_root)  # type: ignore[attr-defined]
    assert resolved is None


def test_resolve_root_accepts_path_inside_project_root(tmp_path: Path) -> None:
    """Paths anchored under ``project_root`` resolve cleanly."""
    project_root = tmp_path / "project"
    inside = project_root / "src"
    inside.mkdir(parents=True)

    resolved = _MODULE._resolve_root(inside, project_root)  # type: ignore[attr-defined]
    assert resolved == inside.resolve(strict=False)
