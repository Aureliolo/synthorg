"""Unit tests for the yoyo migration wrapper module.

Covers URL building, revisions discovery, and the apply / status /
baseline / rollback workflow against a file-backed SQLite scratch
database.  Postgres-arm coverage lives in
``tests/integration/persistence/postgres`` because it requires a live
container.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from synthorg.core.persistence_errors import MigrationError
from synthorg.persistence import migration_helpers, migrations
from synthorg.persistence.config import PostgresConfig

pytestmark = pytest.mark.unit


# ── URL builders ─────────────────────────────────────────────────


def test_to_sqlite_url_emits_three_slash_form(tmp_path: Path) -> None:
    """SQLite URLs must use the ``sqlite:///`` (3-slash) form."""
    db = tmp_path / "test.db"
    url = migrations.to_sqlite_url(str(db))
    assert url.startswith("sqlite:///")
    assert ":memory:" not in url


def test_to_sqlite_url_normalises_windows_separators(tmp_path: Path) -> None:
    """Backslash paths must be converted to forward-slashes for yoyo."""
    raw = str(tmp_path / "subdir" / "test.db")
    url = migrations.to_sqlite_url(raw)
    assert "\\" not in url
    assert "/" in url


def test_to_sqlite_url_rejects_in_memory() -> None:
    """``:memory:`` cannot be shared across yoyo + the calling process."""
    with pytest.raises(MigrationError, match="in-memory"):
        migrations.to_sqlite_url(":memory:")


def test_to_postgres_url_uses_psycopg3_scheme() -> None:
    """Routing to yoyo's PostgresqlPsycopgBackend requires the +psycopg suffix."""
    config = PostgresConfig(
        host="db.example.com",
        port=5432,
        database="synthorg",
        username="svc",
        password=SecretStr("hunter2"),
    )
    url = migrations.to_postgres_url(config)
    assert url.startswith("postgresql+psycopg://")
    assert "svc:hunter2@db.example.com:5432/synthorg" in url


def test_to_postgres_url_url_encodes_credentials() -> None:
    """Special characters in user / password / database must round-trip."""
    config = PostgresConfig(
        host="db",
        port=5432,
        database="my db",
        username="u@svc",
        password=SecretStr("p@ss/word"),
    )
    url = migrations.to_postgres_url(config)
    assert "u%40svc" in url
    assert "p%40ss%2Fword" in url
    assert "my%20db" in url


def test_to_postgres_url_rounds_sub_second_connect_timeout_up() -> None:
    """libpq honours integer seconds with a min of 2; sub-second rounds up."""
    config = PostgresConfig(
        host="db",
        port=5432,
        database="x",
        username="u",
        password=SecretStr("p"),
        connect_timeout_seconds=0.5,
    )
    url = migrations.to_postgres_url(config)
    assert "connect_timeout=2" in url


def test_to_postgres_url_includes_application_name_and_sslmode() -> None:
    """Both must show up in the URL query string."""
    config = PostgresConfig(
        host="db",
        port=5432,
        database="x",
        username="u",
        password=SecretStr("p"),
        application_name="custom-app",
        ssl_mode="prefer",
    )
    url = migrations.to_postgres_url(config)
    assert "application_name=custom-app" in url
    assert "sslmode=prefer" in url


# ── _redact_url ──────────────────────────────────────────────────


def test_redact_url_strips_credentials_and_path() -> None:
    """Only the scheme prefix should leak into logs."""
    redacted = migration_helpers._redact_url(
        "postgresql+psycopg://svc:hunter2@db.example.com:5432/synthorg"
    )
    assert redacted == "postgresql+psycopg://..."
    assert "hunter2" not in redacted
    assert "synthorg" not in redacted


def test_redact_url_handles_unparseable_input() -> None:
    """A URL without ``://`` is fully redacted."""
    assert migration_helpers._redact_url("not-a-url") == "REDACTED"


# ── Revisions discovery ─────────────────────────────────────────


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_revisions_dir_resolves_for_both_backends(
    backend: migrations.BackendName,
) -> None:
    """Each backend must point at a populated revisions directory."""
    path = migrations.revisions_dir(backend)
    assert path.is_dir()
    sql_files = list(path.glob("*.sql"))
    assert sql_files, f"no .sql revisions discovered under {path}"


def test_copy_revisions_clones_directory(tmp_path: Path) -> None:
    """The copy must contain every ``*.sql`` file from the source."""
    dest = tmp_path / "revisions"
    returned = migrations.copy_revisions(dest, backend="sqlite")
    assert returned == dest
    assert dest.is_dir()
    src_files = {p.name for p in migrations.revisions_dir("sqlite").glob("*.sql")}
    copied_files = {p.name for p in dest.glob("*.sql")}
    assert src_files == copied_files


def test_copy_revisions_fails_when_destination_exists(tmp_path: Path) -> None:
    """``shutil.copytree`` raises when *dest* already exists; we surface it."""
    dest = tmp_path / "revisions"
    dest.mkdir()
    with pytest.raises(MigrationError):
        migrations.copy_revisions(dest, backend="sqlite")


def test_discover_filters_package_init(tmp_path: Path) -> None:
    """``__init__.py`` must not surface as a yoyo migration."""
    rev_dir = tmp_path / "revisions"
    rev_dir.mkdir()
    (rev_dir / "__init__.py").write_text("")
    (rev_dir / "20260101000000_first.sql").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY);"
    )
    discovered = migration_helpers._discover(rev_dir)
    ids = [m.id for m in discovered]
    assert "__init__" not in ids
    assert "20260101000000_first" in ids


# ── End-to-end against a tmp SQLite DB ───────────────────────────


@pytest.fixture
def scratch_revisions(tmp_path: Path) -> Path:
    """Write a tiny two-revision set into *tmp_path/revisions*."""
    rev_dir = tmp_path / "revisions"
    rev_dir.mkdir()
    (rev_dir / "00000000000001_init.sql").write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
    )
    (rev_dir / "00000000000002_add_index.sql").write_text(
        "CREATE INDEX idx_widgets_name ON widgets(name);"
    )
    return rev_dir


async def test_migrate_apply_runs_pending_revisions(
    tmp_path: Path,
    scratch_revisions: Path,
) -> None:
    """Applying against an empty DB advances the version pointer."""
    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))

    result = await migrations.migrate_apply(url, revisions_path=scratch_revisions)

    assert result.applied_count == 2
    assert result.applied_versions == (
        "00000000000001_init",
        "00000000000002_add_index",
    )
    assert result.current_version == "00000000000002_add_index"


async def test_migrate_apply_is_idempotent(
    tmp_path: Path,
    scratch_revisions: Path,
) -> None:
    """A second apply against an up-to-date DB must be a no-op."""
    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))
    await migrations.migrate_apply(url, revisions_path=scratch_revisions)

    second = await migrations.migrate_apply(url, revisions_path=scratch_revisions)

    assert second.applied_count == 0
    assert second.current_version == "00000000000002_add_index"


async def test_migrate_status_reports_pending_count(
    tmp_path: Path,
    scratch_revisions: Path,
) -> None:
    """A fresh DB must report all revisions as pending and none applied."""
    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))

    status = await migrations.migrate_status(url, revisions_path=scratch_revisions)

    assert status.pending_count == 2
    assert status.applied_versions == ()
    assert status.current_version == ""


async def test_migrate_baseline_marks_without_executing(
    tmp_path: Path,
    scratch_revisions: Path,
) -> None:
    """Baseline records every revision as applied without running its SQL."""
    import sqlite3

    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))

    result = await migrations.migrate_baseline(url, revisions_path=scratch_revisions)

    assert result.applied_count == 2
    conn = sqlite3.connect(db)
    user_tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE '_yoyo%' "
            "AND name NOT LIKE 'yoyo%'"
        )
    ]
    conn.close()
    assert user_tables == [], "baseline must not create user tables; got " + repr(
        user_tables
    )


# ── Rollback path ────────────────────────────────────────────────


@pytest.fixture
def rollback_capable_revisions(tmp_path: Path) -> Path:
    """Write two yoyo Python revisions with explicit forward+rollback steps."""
    rev_dir = tmp_path / "rev_capable"
    rev_dir.mkdir()
    (rev_dir / "00000000000001_init.py").write_text(
        "from yoyo import step\n"
        "steps = [\n"
        "    step(\n"
        "        'CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)',\n"
        "        'DROP TABLE widgets',\n"
        "    ),\n"
        "]\n"
    )
    (rev_dir / "00000000000002_add_idx.py").write_text(
        "from yoyo import step\n"
        "steps = [\n"
        "    step(\n"
        "        'CREATE INDEX idx_widgets_name ON widgets(name)',\n"
        "        'DROP INDEX idx_widgets_name',\n"
        "    ),\n"
        "]\n"
    )
    return rev_dir


async def test_migrate_rollback_reverts_newer_than_target(
    tmp_path: Path,
    rollback_capable_revisions: Path,
) -> None:
    """Rolling back to the older revision reverts just the newer one."""
    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))
    await migrations.migrate_apply(url, revisions_path=rollback_capable_revisions)

    result = await migrations.migrate_rollback(
        url,
        target_version="00000000000001_init",
        revisions_path=rollback_capable_revisions,
    )

    assert result.applied_count == 1
    assert result.applied_versions == ("00000000000002_add_idx",)
    assert result.current_version == "00000000000001_init"


async def test_migrate_rollback_target_equal_to_current_is_noop(
    tmp_path: Path,
    rollback_capable_revisions: Path,
) -> None:
    """Asking to roll back to the current version does nothing."""
    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))
    await migrations.migrate_apply(url, revisions_path=rollback_capable_revisions)

    result = await migrations.migrate_rollback(
        url,
        target_version="00000000000002_add_idx",
        revisions_path=rollback_capable_revisions,
    )

    assert result.applied_count == 0
    assert result.applied_versions == ()


async def test_migrate_rollback_unknown_target_reverts_everything(
    tmp_path: Path,
    rollback_capable_revisions: Path,
) -> None:
    """An empty target version means "roll back every applied revision"."""
    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))
    await migrations.migrate_apply(url, revisions_path=rollback_capable_revisions)

    result = await migrations.migrate_rollback(
        url,
        target_version="",
        revisions_path=rollback_capable_revisions,
    )

    assert result.applied_count == 2
    assert set(result.applied_versions) == {
        "00000000000001_init",
        "00000000000002_add_idx",
    }


# ── Lock recovery ────────────────────────────────────────────────


async def test_break_lock_on_unlocked_db_is_safe(tmp_path: Path) -> None:
    """``break_lock`` against a fresh DB completes without error.

    Yoyo's break-lock issues an UPDATE against the lock table; on a
    fresh DB the table does not yet exist, but yoyo creates and
    truncates it on the first call.
    """
    db = tmp_path / "scratch.db"
    url = migrations.to_sqlite_url(str(db))
    await migrations.break_lock(url)


# ── Result invariants ────────────────────────────────────────────


def test_migrate_result_rejects_mismatched_count_and_versions() -> None:
    """``MigrateResult`` post-init must guard count-to-list parity."""
    with pytest.raises(ValueError, match="invariant violated"):
        migrations.MigrateResult(
            applied_count=2,
            applied_versions=("only_one",),
            current_version="only_one",
        )


def test_migrate_status_rejects_mismatched_pending_count() -> None:
    """``MigrateStatus`` post-init must guard count-to-list parity."""
    with pytest.raises(ValueError, match="invariant violated"):
        migrations.MigrateStatus(
            current_version="",
            pending_count=3,
            pending_versions=("a", "b"),
            applied_versions=(),
        )
