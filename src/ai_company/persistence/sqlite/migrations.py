"""SQLite schema migrations using the user_version pragma.

Each migration is a function that receives a connection and applies
DDL statements.  ``run_migrations`` checks the current version and
runs only the migrations that haven't been applied yet.
"""

import sqlite3
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import aiosqlite

from ai_company.observability import get_logger
from ai_company.observability.events.persistence import (
    PERSISTENCE_MIGRATION_COMPLETED,
    PERSISTENCE_MIGRATION_FAILED,
    PERSISTENCE_MIGRATION_SKIPPED,
    PERSISTENCE_MIGRATION_STARTED,
)
from ai_company.persistence.errors import MigrationError

logger = get_logger(__name__)

# Current schema version — bump when adding new migrations.
SCHEMA_VERSION = 1

_V1_STATEMENTS: Sequence[str] = (
    # ── Tasks ─────────────────────────────────────────────
    """\
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    project TEXT NOT NULL,
    created_by TEXT NOT NULL,
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    estimated_complexity TEXT NOT NULL DEFAULT 'medium',
    budget_limit REAL NOT NULL DEFAULT 0.0,
    deadline TEXT,
    max_retries INTEGER NOT NULL DEFAULT 1,
    parent_task_id TEXT,
    task_structure TEXT,
    coordination_topology TEXT NOT NULL DEFAULT 'auto',
    reviewers TEXT NOT NULL DEFAULT '[]',
    dependencies TEXT NOT NULL DEFAULT '[]',
    artifacts_expected TEXT NOT NULL DEFAULT '[]',
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    delegation_chain TEXT NOT NULL DEFAULT '[]'
)""",
    "CREATE INDEX idx_tasks_status ON tasks(status)",
    "CREATE INDEX idx_tasks_assigned_to ON tasks(assigned_to)",
    "CREATE INDEX idx_tasks_project ON tasks(project)",
    # ── Cost records ──────────────────────────────────────
    """\
CREATE TABLE cost_records (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    timestamp TEXT NOT NULL,
    call_category TEXT
)""",
    "CREATE INDEX idx_cost_records_agent_id ON cost_records(agent_id)",
    "CREATE INDEX idx_cost_records_task_id ON cost_records(task_id)",
    # ── Messages ──────────────────────────────────────────
    """\
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    sender TEXT NOT NULL,
    "to" TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    channel TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}'
)""",
    "CREATE INDEX idx_messages_channel ON messages(channel)",
    "CREATE INDEX idx_messages_timestamp ON messages(timestamp)",
)

_MigrateFn = Callable[[aiosqlite.Connection], Coroutine[Any, Any, None]]


async def get_user_version(db: aiosqlite.Connection) -> int:
    """Read the current schema version from the SQLite user_version pragma."""
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def set_user_version(db: aiosqlite.Connection, version: int) -> None:
    """Set the schema version via the SQLite user_version pragma.

    Args:
        db: An open aiosqlite connection.
        version: Non-negative integer schema version.

    Raises:
        MigrationError: If *version* is not a valid non-negative integer.
    """
    if not isinstance(version, int) or version < 0:
        msg = f"Schema version must be a non-negative integer, got {version!r}"
        logger.error(
            PERSISTENCE_MIGRATION_FAILED,
            error=msg,
            version=version,
        )
        raise MigrationError(msg)
    # PRAGMA does not support parameterized queries; version is validated above.
    await db.execute(f"PRAGMA user_version = {version}")


async def _apply_v1(db: aiosqlite.Connection) -> None:
    """Apply schema version 1: create tasks, cost_records, messages."""
    for stmt in _V1_STATEMENTS:
        await db.execute(stmt)


# Ordered list of (target_version, migration_function) pairs. Each migration
# is applied when the current schema version is below its target version.
_MIGRATIONS: list[tuple[int, _MigrateFn]] = [
    (1, _apply_v1),
]


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending migrations up to ``SCHEMA_VERSION``.

    Migrations are executed within an implicit transaction.  On
    failure, the transaction is explicitly rolled back and
    ``MigrationError`` is raised.

    Args:
        db: An open aiosqlite connection.

    Raises:
        MigrationError: If any migration step fails.
    """
    try:
        current = await get_user_version(db)
    except (sqlite3.Error, aiosqlite.Error) as exc:
        msg = "Failed to read current schema version"
        logger.exception(PERSISTENCE_MIGRATION_FAILED, error=str(exc))
        raise MigrationError(msg) from exc

    if current >= SCHEMA_VERSION:
        logger.debug(
            PERSISTENCE_MIGRATION_SKIPPED,
            current_version=current,
            target_version=SCHEMA_VERSION,
        )
        return

    logger.info(
        PERSISTENCE_MIGRATION_STARTED,
        current_version=current,
        target_version=SCHEMA_VERSION,
    )

    try:
        for target_version, migrate_fn in _MIGRATIONS:
            if current < target_version:
                await migrate_fn(db)
                current = target_version

        await set_user_version(db, SCHEMA_VERSION)
        await db.commit()
    except (sqlite3.Error, aiosqlite.Error, MigrationError) as exc:
        try:
            await db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.error(  # noqa: TRY400
                PERSISTENCE_MIGRATION_FAILED,
                error=f"Rollback also failed: {rollback_exc}",
                original_error=str(exc),
            )
        if isinstance(exc, MigrationError):
            raise
        msg = f"Migration to version {SCHEMA_VERSION} failed"
        logger.exception(
            PERSISTENCE_MIGRATION_FAILED,
            target_version=SCHEMA_VERSION,
            error=str(exc),
        )
        raise MigrationError(msg) from exc

    logger.info(
        PERSISTENCE_MIGRATION_COMPLETED,
        version=SCHEMA_VERSION,
    )
