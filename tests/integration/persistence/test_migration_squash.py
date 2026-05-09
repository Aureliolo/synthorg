"""Integration tests for migration squash upgrade paths.

Verifies that databases at various migration points can upgrade
through an Atlas checkpoint created by the partial squash workflow.

The tests generate a small self-contained Atlas project with 6
migrations, squash the first 4 into a checkpoint, then verify that:

- A fresh database applies the checkpoint + remaining 2 files
- A database at the squash boundary (migration 4) applies the
  remaining 2 files, skipping the checkpoint
- A database past the squash point (migration 5) applies the last
  file, skipping the checkpoint
- A database before the squash point (migration 2) fails with a
  clear error (expected -- the individual files it needs are gone)
"""

import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# Incremental schema states for generating 6 migrations.
_SCHEMA_STEPS: tuple[str, ...] = (
    "CREATE TABLE t1 (id INTEGER PRIMARY KEY);",
    "CREATE TABLE t1 (id INTEGER PRIMARY KEY, name TEXT);",
    (
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);"
    ),
    (
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY, ref INTEGER);"
    ),
    (
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY, ref INTEGER);\n"
        "CREATE INDEX idx_t2_ref ON t2(ref);"
    ),
    (
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY, ref INTEGER);\n"
        "CREATE INDEX idx_t2_ref ON t2(ref);\n"
        "CREATE TABLE t3 (id INTEGER PRIMARY KEY);"
    ),
)

_SQUASH_POINT = 4  # Squash first 4, keep last 2


def _to_url(path: Path, scheme: str = "file") -> str:
    """Convert a path to an Atlas-compatible URL with forward slashes."""
    return f"{scheme}://{PurePosixPath(PureWindowsPath(str(path)))}"


def _atlas(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run an Atlas CLI command.

    Retries up to 5 times on lock contention (Atlas uses a global
    lock for some operations, causing failures when multiple xdist
    workers invoke atlas concurrently).
    """
    result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr=""
    )
    for attempt in range(5):
        result = subprocess.run(  # noqa: S603
            ["atlas", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result
        if "lock" not in result.stderr.lower() or attempt == 4:
            break
        # Genuine wall-clock backoff between subprocess retries: the
        # atlas binary holds a real OS-level lock that another xdist
        # worker is releasing. There is no clock seam to inject here.
        time.sleep(1 + attempt)
    if check and result.returncode != 0:
        msg = f"atlas {' '.join(args[:4])}: {result.stderr.strip()}"
        raise RuntimeError(msg)
    return result


def _generate_migrations(project_dir: Path) -> list[str]:
    """Generate 6 SQLite migrations from incremental schema changes.

    Atlas writes filenames with wall-clock-second-precision timestamps,
    so back-to-back ``migrate diff`` calls would land within the same
    second. After generating all migrations, the function rewrites
    every filename to a deterministic monotonic schedule -- one second
    per step except for a two-second jump at the squash boundary, so
    the checkpoint timestamp computed in :func:`_build_squashed_dir`
    (``m4_dt + 1s``) has room before ``m5_dt`` without depending on
    real elapsed time between Atlas calls. ``atlas migrate hash``
    regenerates ``atlas.sum`` after the renames.

    Uses a file-backed dev database to avoid lock contention when
    multiple xdist workers generate migrations concurrently.

    Returns the sorted list of .sql filenames.
    """
    revisions = project_dir / "revisions"
    revisions.mkdir(exist_ok=True)
    schema = project_dir / "schema.sql"
    dev_db = project_dir / "dev.db"
    dev_url = _to_url(dev_db, "sqlite")

    for i, ddl in enumerate(_SCHEMA_STEPS):
        schema.write_text(ddl)
        _atlas(
            "migrate",
            "diff",
            "--dev-url",
            dev_url,
            "--dir",
            _to_url(revisions),
            "--to",
            _to_url(schema),
            "--lock-timeout",
            "60s",
            f"m{i + 1}",
        )

    _ts_fmt = "%Y%m%d%H%M%S"
    base = datetime(2026, 1, 1, 0, 0, 0)  # noqa: DTZ001 -- arbitrary fixed base
    files = sorted(f.name for f in revisions.glob("*.sql"))
    new_names: list[str] = []
    for idx, name in enumerate(files):
        # +1s per step, but +2s at the squash boundary so m5 is at
        # least two seconds after m4 (the constraint asserted in
        # _build_squashed_dir for cp_dt = m4 + 1s < m5).
        offset = idx if idx < _SQUASH_POINT else idx + 1
        new_prefix = (base + timedelta(seconds=offset)).strftime(_ts_fmt)
        new_name = new_prefix + name[14:]
        if new_name != name:
            (revisions / name).rename(revisions / new_name)
        new_names.append(new_name)
    _atlas("migrate", "hash", "--dir", _to_url(revisions))

    return sorted(new_names)


def _build_squashed_dir(
    original_rev: Path,
    files: list[str],
    dest: Path,
) -> str:
    """Build a squashed revisions directory.

    Creates a checkpoint from the first ``_SQUASH_POINT`` files,
    renames it to sit between the last squashed and first kept
    file, then assembles the final directory.

    Returns the checkpoint filename.
    """
    dest.mkdir(exist_ok=True)

    # Copy first N files to a temp dir for checkpoint creation.
    partial = dest.parent / "partial_checkpoint"
    partial.mkdir(exist_ok=True)
    for name in files[:_SQUASH_POINT]:
        (partial / name).write_bytes((original_rev / name).read_bytes())
    _atlas("migrate", "hash", "--dir", _to_url(partial))

    # Create checkpoint using file-backed dev DB to avoid lock contention.
    cp_dev = dest.parent / "cp_dev.db"
    pre = {f.name for f in partial.glob("*.sql")}
    _atlas(
        "migrate",
        "checkpoint",
        "--dev-url",
        _to_url(cp_dev, "sqlite"),
        "--dir",
        _to_url(partial),
    )
    post = {f.name for f in partial.glob("*.sql")}
    cp_orig = sorted(post - pre)[0]

    # Compute checkpoint timestamp between m4 and m5 using datetime
    # arithmetic to handle minute/hour rollover (e.g. :59 + 1 = :00).
    _ts_fmt = "%Y%m%d%H%M%S"
    m4_dt = datetime.strptime(files[_SQUASH_POINT - 1][:14], _ts_fmt)  # noqa: DTZ007
    m5_dt = datetime.strptime(files[_SQUASH_POINT][:14], _ts_fmt)  # noqa: DTZ007
    cp_dt = m4_dt + timedelta(seconds=1)
    assert cp_dt < m5_dt, f"No room for checkpoint between {m4_dt} and {m5_dt}"
    cp_name = f"{cp_dt.strftime(_ts_fmt)}_checkpoint.sql"

    # Assemble squashed dir: checkpoint + remaining files.
    (dest / cp_name).write_bytes((partial / cp_orig).read_bytes())
    for name in files[_SQUASH_POINT:]:
        (dest / name).write_bytes((original_rev / name).read_bytes())
    _atlas("migrate", "hash", "--dir", _to_url(dest))

    return cp_name


def _apply(revisions: Path, db_path: Path, count: int | None = None) -> bool:
    """Apply migrations. Returns True on success."""
    cmd = [
        "migrate",
        "apply",
        "--dir",
        _to_url(revisions),
        "--url",
        _to_url(db_path, "sqlite"),
    ]
    if count is not None:
        cmd.append(str(count))
    result = _atlas(*cmd, check=False)
    return result.returncode == 0


def _tables(db_path: Path) -> list[str]:
    """Return user table names from a SQLite database."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'atlas%' "
            "ORDER BY name"
        ).fetchall()
    return [r[0] for r in rows]


def _indexes(db_path: Path) -> list[str]:
    """Return user index names from a SQLite database."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' "
            "AND name NOT LIKE 'sqlite%' "
            "AND name NOT LIKE 'atlas%' "
            "ORDER BY name"
        ).fetchall()
    return [r[0] for r in rows]


@pytest.fixture(scope="session")
def squash_project(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, list[str]]:
    """Create a project with 6 migrations and a squashed variant.

    Session-scoped to avoid repeating the expensive migration
    generation + checkpoint creation within a single pytest process.
    Under pytest-xdist, this fixture still runs once per worker.

    Returns (original_revisions, squashed_revisions, migration_files).
    """
    base = tmp_path_factory.mktemp("squash_project")
    project = base / "project"
    project.mkdir()
    files = _generate_migrations(project)
    original = project / "revisions"

    squashed = base / "squashed"
    try:
        _build_squashed_dir(original, files, squashed)
    except RuntimeError as exc:
        if "atlas pro" in str(exc).lower():
            pytest.skip("atlas migrate checkpoint requires Atlas Pro")
        raise

    return original, squashed, files


class TestMigrationSquashUpgradePaths:
    """Verify databases at various migration points upgrade through squash."""

    def test_fresh_db_applies_checkpoint_and_remaining(
        self,
        squash_project: tuple[Path, Path, list[str]],
        tmp_path: Path,
    ) -> None:
        """Fresh DB applies checkpoint + remaining 2 migrations."""
        _, squashed, _ = squash_project
        db = tmp_path / "fresh.db"

        assert _apply(squashed, db)
        assert _tables(db) == ["t1", "t2", "t3"]
        assert _indexes(db) == ["idx_t2_ref"]

    @pytest.mark.parametrize(
        ("case", "pre_count", "pre_tables", "pre_indexes", "squashed_succeeds"),
        [
            # DB at squash boundary (count=4) applies remaining 2.
            ("boundary", _SQUASH_POINT, ["t1", "t2"], [], True),
            # DB past squash point (count=5) applies remaining 1.
            (
                "past",
                _SQUASH_POINT + 1,
                ["t1", "t2"],
                ["idx_t2_ref"],
                True,
            ),
            # DB before squash point (count=2) fails -- files are gone.
            ("before", 2, ["t1"], [], False),
        ],
    )
    def test_db_upgrade_matrix(  # noqa: PLR0913
        self,
        squash_project: tuple[Path, Path, list[str]],
        tmp_path: Path,
        case: str,
        pre_count: int,
        pre_tables: list[str],
        pre_indexes: list[str],
        squashed_succeeds: bool,
    ) -> None:
        """Upgrade matrix: DBs at various migration points vs squashed dir."""
        original, squashed, _ = squash_project
        db = tmp_path / f"{case}.db"

        assert _apply(original, db, count=pre_count)
        assert _tables(db) == pre_tables
        assert _indexes(db) == pre_indexes

        if squashed_succeeds:
            assert _apply(squashed, db)
            assert _tables(db) == ["t1", "t2", "t3"]
            assert _indexes(db) == ["idx_t2_ref"]
        else:
            assert not _apply(squashed, db)

    def test_squash_script_below_threshold(self) -> None:
        """Squash script reports 'below threshold' for both backends."""
        # Locate bash via shutil.which to avoid FileNotFoundError on
        # systems where bash is not on PATH (pure Windows cmd).
        bash_path = shutil.which("bash")
        if bash_path is None:
            pytest.skip("bash not available in PATH")

        # Atlas may not be on the bash subprocess's PATH even when it's
        # available from the test runner (Windows Git Bash vs. system).
        probe = subprocess.run(  # noqa: S603
            [bash_path, "-c", "command -v atlas"],
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            pytest.skip("atlas not available in bash PATH")

        # Set threshold absurdly high so the script never triggers an
        # actual squash against the real repo's migration directory.
        env = {**os.environ, "SQUASH_THRESHOLD": "999999"}
        result = subprocess.run(  # noqa: S603
            [bash_path, "scripts/squash_migrations.sh"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        diag = f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        assert result.returncode == 0, diag
        assert "Below threshold" in result.stdout, diag
        assert "sqlite" in result.stdout.lower(), diag
        assert "postgres" in result.stdout.lower(), diag
