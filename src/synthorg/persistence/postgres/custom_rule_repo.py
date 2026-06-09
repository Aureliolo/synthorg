"""Postgres-backed custom signal rule repository.

Persists :class:`CustomRuleDefinition` rows in the ``custom_rules``
table using the shared ``AsyncConnectionPool``.  Each operation
checks out a connection via ``async with pool.connection() as conn``;
the context manager auto-commits on clean exit.

Read paths use ``psycopg.rows.dict_row`` so row access is by column
name -- robust to accidental SELECT re-ordering.
"""

from datetime import UTC

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.rules.custom import CustomRuleDefinition
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CUSTOM_RULE_DELETE_FAILED,
    META_CUSTOM_RULE_FETCH_FAILED,
    META_CUSTOM_RULE_FETCHED,
    META_CUSTOM_RULE_LIST_FAILED,
    META_CUSTOM_RULE_LISTED,
    META_CUSTOM_RULE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.custom_rule import (
    row_to_custom_rule,
    serialize_altitudes,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.custom_rule_protocol import (
    CustomRuleFilterSpec,
)

logger = get_logger(__name__)


def _row_to_definition(row: DictRow) -> CustomRuleDefinition:
    """Deserialize a dict row into a :class:`CustomRuleDefinition`.

    Delegates to :func:`row_to_custom_rule` from the shared helper so
    SQLite and Postgres use identical deserialisation logic. Postgres
    JSONB returns altitudes as a Python list and TIMESTAMPTZ columns
    as ``datetime`` objects; the helper handles both that and the
    SQLite TEXT shape.

    Raises:
        MalformedRowError: If the row contains corrupt or unparseable
            data. Non-retryable -- malformed rows are deterministic
            data-integrity issues, not transient query failures.

    Returns:
        Result of type ``CustomRuleDefinition``.
    """
    return row_to_custom_rule(row)


class PostgresCustomRuleRepository:
    """Postgres-backed custom signal rule repository.

    Provides CRUD operations for user-defined declarative rules
    against the shared ``AsyncConnectionPool``.

    Args:
        pool: The shared async Postgres connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, rule: CustomRuleDefinition) -> None:
        """Persist a custom rule via upsert.

        Args:
            rule: The rule definition to persist.

        Raises:
            ConstraintViolationError: If the rule name conflicts
                with a different existing rule.
            QueryError: If the database operation fails.
        """
        try:
            # Serialize inside the guarded path so any helper failure
            # is wrapped in QueryError / ConstraintViolationError like
            # the rest of the repository, instead of leaking a raw
            # exception that bypasses the structured save-failed log.
            altitudes_json = serialize_altitudes(rule)
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO custom_rules (
                        id, name, description, metric_path,
                        comparator, threshold, severity,
                        target_altitudes, enabled,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        metric_path = EXCLUDED.metric_path,
                        comparator = EXCLUDED.comparator,
                        threshold = EXCLUDED.threshold,
                        severity = EXCLUDED.severity,
                        target_altitudes = EXCLUDED.target_altitudes,
                        enabled = EXCLUDED.enabled,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        str(rule.id),
                        rule.name,
                        rule.description,
                        rule.metric_path,
                        rule.comparator.value,
                        rule.threshold,
                        rule.severity.value,
                        Jsonb(altitudes_json),
                        rule.enabled,
                        rule.created_at.astimezone(UTC),
                        rule.updated_at.astimezone(UTC),
                    ),
                )
        except psycopg.errors.UniqueViolation as exc:
            constraint_name = getattr(exc.diag, "constraint_name", "") or ""
            if constraint_name == "custom_rules_name":
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
                constraint=constraint_name or "unknown",
            )
            raise ConstraintViolationError(
                msg,
                constraint=constraint_name or "custom_rules_unknown",
            ) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save custom rule {rule.name!r}"
            logger.warning(
                META_CUSTOM_RULE_SAVE_FAILED,
                rule_name=rule.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            # Catch-all for non-psycopg helper failures (serialize_altitudes,
            # JSON encoding, datetime coercion). Without this, those raw
            # exceptions would skip the structured save-failed log + the
            # canonical QueryError translation, leaking driver internals
            # to the API layer.
            msg = f"Failed to save custom rule {rule.name!r} (helper error)"
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """
                    SELECT id, name, description, metric_path,
                           comparator, threshold, severity,
                           target_altitudes, enabled,
                           created_at, updated_at
                    FROM custom_rules WHERE id = %s
                    """,
                    (rule_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """
                    SELECT id, name, description, metric_path,
                           comparator, threshold, severity,
                           target_altitudes, enabled,
                           created_at, updated_at
                    FROM custom_rules WHERE name = %s
                    """,
                    (name,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CustomRuleDefinition, ...]:
        """List custom rules ordered by name.

        Returns:
            The matching entities.
        """
        return await self.query(
            CustomRuleFilterSpec(),
            limit=limit,
            offset=offset,
        )

    async def query(
        self,
        filter_spec: CustomRuleFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CustomRuleDefinition, ...]:
        """List custom rules matching ``filter_spec`` ordered by name.

        Raises:
            QueryError: If the query or pagination validation fails.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=META_CUSTOM_RULE_LIST_FAILED
        )
        base = (
            "SELECT id, name, description, metric_path, "
            "comparator, threshold, severity, target_altitudes, "
            "enabled, created_at, updated_at FROM custom_rules"
        )
        where = " WHERE enabled = true" if filter_spec.enabled_only else ""
        sql = f"{base}{where} ORDER BY name LIMIT %s OFFSET %s"
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (limit, offset))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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

    async def count(self, filter_spec: CustomRuleFilterSpec) -> int:
        """Count custom rules matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where = " WHERE enabled = true" if filter_spec.enabled_only else ""
        sql = f"SELECT COUNT(*) FROM custom_rules{where}"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(sql)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count custom rules"
            logger.warning(
                META_CUSTOM_RULE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row else 0

    async def delete(self, rule_id: NotBlankStr) -> bool:
        """Delete a custom rule by id.

        Args:
            rule_id: UUID string of the rule.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM custom_rules WHERE id = %s",
                    (rule_id,),
                )
                deleted = cur.rowcount > 0
        except psycopg.Error as exc:
            msg = f"Failed to delete custom rule {rule_id!r}"
            logger.warning(
                META_CUSTOM_RULE_DELETE_FAILED,
                rule_id=rule_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
