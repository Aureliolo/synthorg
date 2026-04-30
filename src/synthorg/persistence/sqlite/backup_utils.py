"""SQLite file-level backup primitives.

Wraps the stdlib ``sqlite3`` operations (``VACUUM INTO``,
``PRAGMA integrity_check``) that sit outside the repository pattern
because they operate on the database *file* rather than on a
repository's logical rows.  Kept inside ``persistence/`` so the
boundary linter's 'only persistence/ may import sqlite3' rule holds.
"""

import contextlib
import sqlite3
from pathlib import Path

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import BACKUP_COMPONENT_FAILED

logger = get_logger(__name__)


def vacuum_into(source_path: str, target_path: str) -> int:
    """Execute ``VACUUM INTO`` to produce a consistent DB copy.

    Args:
        source_path: Path to the live SQLite database.
        target_path: Path for the backup copy.

    Returns:
        Size of the resulting backup file in bytes.

    Raises:
        FileNotFoundError: If ``source_path`` does not exist; guards
            against ``sqlite3.connect`` silently creating an empty DB
            and VACUUMing that into the backup.
    """
    if not Path(source_path).is_file():
        logger.warning(
            BACKUP_COMPONENT_FAILED,
            component="sqlite_vacuum_into",
            db_path=source_path,
            error_type="FileNotFoundError",
            error=f"source database not found: {source_path!r}",
        )
        msg = f"source database not found: {source_path!r}"
        raise FileNotFoundError(msg)
    with contextlib.closing(sqlite3.connect(source_path)) as conn:
        conn.execute("VACUUM INTO ?", (target_path,))
    return Path(target_path).stat().st_size


class IntegrityCheckError(RuntimeError):
    """Raised when ``PRAGMA integrity_check`` cannot run.

    Distinct from the check itself reporting corruption (which returns
    ``False``).  This is the "could not determine" case: the database
    file is unreadable, the sqlite driver itself raised, or similar.
    Callers should treat this as a system failure, not a corrupt backup.
    """


def integrity_check(db_path: str) -> bool:
    """Run ``PRAGMA integrity_check`` on a SQLite database file.

    Returns:
        ``True`` if SQLite reports the file is ``ok``; ``False`` when
        the check itself runs but reports corruption.

    Raises:
        IntegrityCheckError: When the check could not be executed
            (I/O error, not a SQLite database, driver error, etc.).
    """
    try:
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
    except (sqlite3.Error, OSError) as exc:
        msg = f"integrity_check could not run on {db_path!r}: {exc}"
        logger.warning(
            BACKUP_COMPONENT_FAILED,
            component="sqlite_integrity_check",
            db_path=db_path,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise IntegrityCheckError(msg) from exc
    return result is not None and result[0] == "ok"
