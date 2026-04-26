"""SQLite repository implementation for custom signal rules."""

import asyncio
import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite  # noqa: TC002

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CUSTOM_RULE_DELETE_FAILED,
    META_CUSTOM_RULE_FETCH_FAILED,
    META_CUSTOM_RULE_FETCHED,
    META_CUSTOM_RULE_LIST_FAILED,
    META_CUSTOM_RULE_LISTED,
    META_CUSTOM_RULE_SAVE_FAILED,
)
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.custom_rule import (
    row_to_custom_rule,
    serialize_altitudes,
)
from synthorg.persistence.errors import (
    ConstraintViolationError,
    MalformedRowError,
    QueryError,
)

if TYPE_CHECKING:
    from aiosqlite import Row

    from synthorg.meta.rules.custom import CustomRuleDefinition

logger = get_logger(__name__)

_COLUMNS = (
    "id",
    "name",
    "description",
    "metric_path",
    "comparator",
    "threshold",
    "severity",
    "target_altitudes",
    "enabled",
    "created_at",
    "updated_at",
)


def _row_to_dict(row: Row) -> dict[str, object]:
    """Convert a positional ``aiosqlite.Row`` into a column-keyed dict.

    The shared ``row_to_custom_rule`` helper takes a dict so both
    backends share one deserializer. SQLite's positional row factory
    is mapped here at the boundary so the helper sees a uniform shape.
    """
    return dict(zip(_COLUMNS, row, strict=True))


def _row_to_definition(row: Row) -> CustomRuleDefinition:
    """Convert a database row to a :class:`CustomRuleDefinition`.

    Delegates to :func:`row_to_custom_rule` from the shared helper so
    SQLite and Postgres use identical deserialisation logic.

    Raises:
        MalformedRowError: If the row contains corrupt or unparseable
            data, or if the row width drifts from ``_COLUMNS`` (a
            schema-vs-query mismatch surfaces here as a deterministic
            non-retryable error).
    """
    try:
        row_dict = _row_to_dict(row)
    except ValueError as exc:
        msg = (
            f"Custom rule row width mismatch: expected {len(_COLUMNS)} "
            f"columns, got {len(row)}"
        )
        raise MalformedRowError(msg) from exc
    return row_to_custom_rule(row_dict)


class SQLiteCustomRuleRepository:
    """SQLite-backed custom signal rule repository.

    Provides CRUD operations for user-defined declarative rules
    using a shared ``aiosqlite.Connection``.

    Args:
        db: An open aiosqlite connection.
        write_lock: Optional shared lock used to serialize writes on
            the shared connection.  Inject the
            :class:`SQLitePersistenceBackend._shared_write_lock` so
            writes from this repo coordinate with sibling repos that
            share the same connection.  Defaults to ``None``, in
            which case the repo creates a private :class:`asyncio.Lock`
            (suitable for standalone test construction).
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def save(self, rule: CustomRuleDefinition) -> None:
        """Persist a custom rule via upsert.

        Args:
            rule: The rule definition to persist.

        Raises:
            ConstraintViolationError: If the rule name conflicts
                with a different existing rule.
            QueryError: If the database operation fails.
        """
        altitudes_json = json.dumps(serialize_altitudes(rule))
        async with self._write_lock:
            try:
                await self._db.execute(
                    """\
INSERT INTO custom_rules (id, name, description, metric_path,
                         comparator, threshold, severity,
                         target_altitudes, enabled,
                         created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name,
    description=excluded.description,
    metric_path=excluded.metric_path,
    comparator=excluded.comparator,
    threshold=excluded.threshold,
    severity=excluded.severity,
    target_altitudes=excluded.target_altitudes,
    enabled=excluded.enabled,
    updated_at=excluded.updated_at""",
                    (
                        str(rule.id),
                        rule.name,
                        rule.description,
                        rule.metric_path,
                        rule.comparator.value,
                        rule.threshold,
                        rule.severity.value,
                        altitudes_json,
                        int(rule.enabled),
                        normalize_utc(rule.created_at).isoformat(),
                        normalize_utc(rule.updated_at).isoformat(),
                    ),
                )
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._db.rollback()
                err_msg = str(exc).lower()
                if "unique" in err_msg and "name" in err_msg:
                    msg = f"Custom rule name '{rule.name}' already exists"
                    logger.warning(
                        META_CUSTOM_RULE_SAVE_FAILED,
                        rule_name=rule.name,
                        error=msg,
                    )
                    raise ConstraintViolationError(
                        msg,
                        constraint="custom_rules_name",
                    ) from exc
                msg = f"Constraint violation saving custom rule {rule.name!r}"
                logger.warning(
                    META_CUSTOM_RULE_SAVE_FAILED,
                    rule_name=rule.name,
                    error=msg,
                )
                raise ConstraintViolationError(
                    msg,
                    constraint="custom_rules_unknown",
                ) from exc
            except sqlite3.Error as exc:
                await self._db.rollback()
                msg = f"Failed to save custom rule {rule.name!r}"
                logger.warning(
                    META_CUSTOM_RULE_SAVE_FAILED,
                    rule_name=rule.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(
        self,
        rule_id: NotBlankStr,
    ) -> CustomRuleDefinition | None:
        """Retrieve a custom rule by id.

        Args:
            rule_id: UUID string of the rule.

        Returns:
            The rule definition, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT id, name, description, metric_path, "
                "comparator, threshold, severity, target_altitudes, "
                "enabled, created_at, updated_at "
                "FROM custom_rules WHERE id = ?",
                (rule_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except sqlite3.Error as exc:
            msg = f"Failed to fetch custom rule {rule_id!r}"
            logger.warning(
                META_CUSTOM_RULE_FETCH_FAILED,
                rule_id=rule_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                META_CUSTOM_RULE_FETCHED,
                rule_id=rule_id,
                found=False,
            )
            return None
        logger.debug(
            META_CUSTOM_RULE_FETCHED,
            rule_id=rule_id,
            found=True,
        )
        return _row_to_definition(row)

    async def get_by_name(
        self,
        name: NotBlankStr,
    ) -> CustomRuleDefinition | None:
        """Retrieve a custom rule by name.

        Args:
            name: Unique rule name.

        Returns:
            The rule definition, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT id, name, description, metric_path, "
                "comparator, threshold, severity, target_altitudes, "
                "enabled, created_at, updated_at "
                "FROM custom_rules WHERE name = ?",
                (name,),
            ) as cursor:
                row = await cursor.fetchone()
        except sqlite3.Error as exc:
            msg = f"Failed to fetch custom rule by name {name!r}"
            logger.warning(
                META_CUSTOM_RULE_FETCH_FAILED,
                rule_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_definition(row)

    async def list_rules(
        self,
        *,
        enabled_only: bool = False,
    ) -> tuple[CustomRuleDefinition, ...]:
        """List custom rules ordered by name.

        Args:
            enabled_only: If ``True``, return only enabled rules.

        Returns:
            Tuple of rule definitions.

        Raises:
            QueryError: If the query fails.
        """
        query = (
            "SELECT id, name, description, metric_path, "
            "comparator, threshold, severity, target_altitudes, "
            "enabled, created_at, updated_at "
            "FROM custom_rules"
        )
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name"
        try:
            async with self._db.execute(query) as cursor:
                rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to list custom rules"
            logger.warning(
                META_CUSTOM_RULE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        result = tuple(_row_to_definition(row) for row in rows)
        logger.debug(META_CUSTOM_RULE_LISTED, count=len(result))
        return result

    async def delete(self, rule_id: NotBlankStr) -> bool:
        """Delete a custom rule by id.

        Args:
            rule_id: UUID string of the rule.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        async with self._write_lock:
            try:
                async with self._db.execute(
                    "DELETE FROM custom_rules WHERE id = ?",
                    (rule_id,),
                ) as cursor:
                    deleted = cursor.rowcount > 0
                await self._db.commit()
            except sqlite3.Error as exc:
                await self._db.rollback()
                msg = f"Failed to delete custom rule {rule_id!r}"
                logger.warning(
                    META_CUSTOM_RULE_DELETE_FAILED,
                    rule_id=rule_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
