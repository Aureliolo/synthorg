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
SCHEMA_VERSION = 7

_V1_STATEMENTS: Sequence[str] = (
    # ── Tasks ─────────────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS tasks (
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
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON tasks(assigned_to)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project)",
    # ── Cost records ──────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS cost_records (
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
    "CREATE INDEX IF NOT EXISTS idx_cost_records_agent_id ON cost_records(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_cost_records_task_id ON cost_records(task_id)",
    # ── Messages ──────────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS messages (
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
    "CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel)",
    "CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)",
)

_V2_STATEMENTS: Sequence[str] = (
    # ── Lifecycle events ───────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS lifecycle_events (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    initiated_by TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}'
)""",
    "CREATE INDEX IF NOT EXISTS idx_le_agent_id ON lifecycle_events(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_le_event_type ON lifecycle_events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_le_timestamp ON lifecycle_events(timestamp)",
    # ── Task metrics ───────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS task_metrics (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    is_success INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    cost_usd REAL NOT NULL,
    turns_used INTEGER NOT NULL,
    tokens_used INTEGER NOT NULL,
    quality_score REAL,
    complexity TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_tm_agent_id ON task_metrics(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_tm_completed_at ON task_metrics(completed_at)",
    # ── Collaboration metrics ──────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS collaboration_metrics (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    delegation_success INTEGER,
    delegation_response_seconds REAL,
    conflict_constructiveness REAL,
    meeting_contribution REAL,
    loop_triggered INTEGER NOT NULL DEFAULT 0,
    handoff_completeness REAL
)""",
    "CREATE INDEX IF NOT EXISTS idx_cm_agent_id ON collaboration_metrics(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_cm_recorded_at"
    " ON collaboration_metrics(recorded_at)",
    # ── Composite indexes for query performance ───────────
    "CREATE INDEX IF NOT EXISTS idx_tm_agent_completed"
    " ON task_metrics(agent_id, completed_at)",
    "CREATE INDEX IF NOT EXISTS idx_cm_agent_recorded"
    " ON collaboration_metrics(agent_id, recorded_at)",
)

_V3_STATEMENTS: Sequence[str] = (
    # ── Parked contexts ────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS parked_contexts (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    parked_at TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)""",
    "CREATE INDEX IF NOT EXISTS idx_pc_agent_id ON parked_contexts(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_pc_approval_id ON parked_contexts(approval_id)",
)

_V4_STATEMENTS: Sequence[str] = (
    # ── Audit entries ──────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS audit_entries (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    agent_id TEXT,
    task_id TEXT,
    tool_name TEXT NOT NULL,
    tool_category TEXT NOT NULL,
    action_type TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    verdict TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    matched_rules TEXT NOT NULL DEFAULT '[]',
    evaluation_duration_ms REAL NOT NULL,
    approval_id TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_ae_timestamp ON audit_entries(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_ae_agent_id ON audit_entries(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_ae_action_type ON audit_entries(action_type)",
    "CREATE INDEX IF NOT EXISTS idx_ae_verdict ON audit_entries(verdict)",
    "CREATE INDEX IF NOT EXISTS idx_ae_risk_level ON audit_entries(risk_level)",
)

_V5_STATEMENTS: Sequence[str] = (
    # ── Settings (key-value store) ─────────────────────────
    """\
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)""",
    # ── Users ──────────────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)""",
    # ── API keys ───────────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
)""",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id)",
)

_V6_STATEMENTS: Sequence[str] = (
    # ── Checkpoints ────────────────────────────────────────
    """\
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL CHECK (turn_number >= 0),
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_cp_execution_id ON checkpoints(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_cp_task_id ON checkpoints(task_id)",
    # Ascending index — SQLite can reverse-scan efficiently for
    # ORDER BY turn_number DESC LIMIT 1.  DESC modifier silently
    # ignored on SQLite < 3.47 so we use ascending for portability.
    "CREATE INDEX IF NOT EXISTS idx_cp_exec_turn"
    " ON checkpoints(execution_id, turn_number)",
    "CREATE INDEX IF NOT EXISTS idx_cp_task_turn ON checkpoints(task_id, turn_number)",
    # ── Heartbeats ─────────────────────────────────────────
    # No FK to tasks — checkpoints/heartbeats are ephemeral recovery
    # data that may outlive their tasks.  Cleanup is the engine's
    # responsibility (delete_by_execution after completion).
    """\
CREATE TABLE IF NOT EXISTS heartbeats (
    execution_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_hb_last_heartbeat ON heartbeats(last_heartbeat_at)",
)

_V7_NEW_TABLE_DDL: str = """\
CREATE TABLE IF NOT EXISTS parked_contexts_new (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT,
    approval_id TEXT NOT NULL,
    parked_at TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)"""

_V7_COPY_ROWS: str = """\
INSERT OR IGNORE INTO parked_contexts_new (
    id, execution_id, agent_id, task_id, approval_id,
    parked_at, context_json, metadata
)
SELECT
    id, execution_id, agent_id, task_id, approval_id,
    parked_at, context_json, metadata
FROM {source}"""

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


async def _apply_v2(db: aiosqlite.Connection) -> None:
    """Apply schema v2: lifecycle_events, task_metrics, collaboration_metrics."""
    for stmt in _V2_STATEMENTS:
        await db.execute(stmt)


async def _apply_v3(db: aiosqlite.Connection) -> None:
    """Apply schema v3: parked_contexts."""
    for stmt in _V3_STATEMENTS:
        await db.execute(stmt)


async def _apply_v4(db: aiosqlite.Connection) -> None:
    """Apply schema v4: audit_entries."""
    for stmt in _V4_STATEMENTS:
        await db.execute(stmt)


async def _apply_v5(db: aiosqlite.Connection) -> None:
    """Apply schema v5: settings, users, api_keys."""
    for stmt in _V5_STATEMENTS:
        await db.execute(stmt)


async def _apply_v6(db: aiosqlite.Connection) -> None:
    """Apply schema v6: checkpoints, heartbeats."""
    for stmt in _V6_STATEMENTS:
        await db.execute(stmt)


async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    """Check whether a table exists in the database."""
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return await cursor.fetchone() is not None


async def _apply_v7(db: aiosqlite.Connection) -> None:
    """Apply schema v7: make parked_contexts.task_id nullable.

    Crash-safe: handles three intermediate states:
    1. Normal (parked_contexts exists) — create new, copy, rename, drop.
    2. Mid-crash (parked_contexts_old exists, parked_contexts gone) —
       skip copy, just rename new → parked_contexts and drop old.
    3. Already done (parked_contexts exists, no _new or _old) — no-op
       via IF NOT EXISTS + OR IGNORE guards.
    """
    has_original = await _table_exists(db, "parked_contexts")
    has_old = await _table_exists(db, "parked_contexts_old")

    # Step 1: create the new table (idempotent).
    await db.execute(_V7_NEW_TABLE_DDL)

    # Step 2: copy rows from the surviving source table.
    # Always run when a source exists — INSERT OR IGNORE makes it idempotent.
    if has_original:
        await db.execute(_V7_COPY_ROWS.format(source="parked_contexts"))
    elif has_old:
        await db.execute(_V7_COPY_ROWS.format(source="parked_contexts_old"))

    # Step 3: rename original → _old (skip if already gone).
    if has_original and not has_old:
        await db.execute(
            "ALTER TABLE parked_contexts RENAME TO parked_contexts_old",
        )

    # Step 4: ensure parked_contexts exists, handling crash states.
    has_current = await _table_exists(db, "parked_contexts")
    if await _table_exists(db, "parked_contexts_new"):
        if has_current:
            # Crash after a previous step 4 — keep existing, drop redundant.
            await db.execute("DROP TABLE parked_contexts_new")
        else:
            await db.execute(
                "ALTER TABLE parked_contexts_new RENAME TO parked_contexts",
            )

    # Step 5: clean up.
    await db.execute("DROP TABLE IF EXISTS parked_contexts_old")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pc_agent_id ON parked_contexts(agent_id)",
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pc_approval_id ON parked_contexts(approval_id)",
    )


# Ordered list of (target_version, migration_function) pairs. Each migration
# is applied when the current schema version is below its target version.
_MIGRATIONS: list[tuple[int, _MigrateFn]] = [
    (1, _apply_v1),
    (2, _apply_v2),
    (3, _apply_v3),
    (4, _apply_v4),
    (5, _apply_v5),
    (6, _apply_v6),
    (7, _apply_v7),
]


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending migrations up to ``SCHEMA_VERSION``.

    .. note::

       SQLite implicitly commits before each DDL statement, so
       multi-statement migrations are **not** fully atomic.  All DDL
       uses ``IF NOT EXISTS`` guards so that a partial failure
       (e.g. disk full after creating some tables) can be recovered
       by re-running the migration.

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
