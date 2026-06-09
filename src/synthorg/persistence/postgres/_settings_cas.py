"""Compare-and-swap write helpers for the Postgres settings repository.

Extracted from ``settings_repo`` so the repository module stays under
its tier cap. These helpers operate on a shared
``psycopg_pool.AsyncConnectionPool``: :func:`set_if_unchanged` and
:func:`set_many` implement the optimistic-concurrency write paths, and
:func:`parse_setting_iso` is the shared ISO-timestamp boundary parser
used by every write (including the plain ``save``).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

import psycopg
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SET_FAILED,
    SETTINGS_VALUE_SET,
)
from synthorg.persistence._shared import parse_iso_utc
from synthorg.persistence.settings_protocol import SettingRow, SettingRowKey

logger = get_logger(__name__)


class _CASConflictError(
    Exception,
):  # lint-allow: domain-error-hierarchy -- internal CAS-miss sentinel
    """Internal sentinel -- raised inside transactions to signal CAS miss.

    Caught immediately by :func:`set_many` to convert the exception into
    a ``False`` return.  Never escapes the repository.
    """


def parse_setting_iso(value: str, namespace: str, key: str) -> datetime:
    """Parse an ISO timestamp, logging + raising ``QueryError`` on bad input.

    Emits a structured WARNING with ``namespace`` / ``key`` / ``value`` /
    ``error_type`` so an operator triaging a bad-timestamp incident has
    full call-site context without grepping for the raised
    :class:`QueryError`. The raw exception text is redacted via
    :func:`safe_error_description` so secret-log invariants hold even if
    the underlying ``ValueError`` carried a payload snippet.

    Returns:
        Result of type ``datetime``.

    Raises:
        QueryError: If ``value`` cannot be parsed as an ISO-8601 UTC
            timestamp.
    """
    try:
        return parse_iso_utc(value)
    except ValueError as exc:
        msg = f"Invalid timestamp for {namespace}/{key}: {value!r}"
        logger.warning(
            SETTINGS_SET_FAILED,
            namespace=namespace,
            key=key,
            value=value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


async def set_if_unchanged(
    pool: AsyncConnectionPool,
    entity: SettingRow,
    expected_updated_at: str | None = None,
) -> bool:
    """Upsert a setting with optional compare-and-swap (bespoke per D7).

    Args:
        pool: The async connection pool.
        entity: The setting to upsert.
        expected_updated_at: When provided, enforces atomic CAS -- the
            row is only updated if the current ``updated_at`` matches.
            Empty string ``""`` signals "only insert if no row exists".

    Returns:
        ``True`` if the write succeeded, ``False`` if the CAS condition
        was not met.

    Raises:
        QueryError: If the database query fails.
    """
    updated_at_dt = parse_setting_iso(entity.updated_at, entity.namespace, entity.key)
    try:
        async with pool.connection() as conn, conn.cursor() as cur:
            if expected_updated_at is not None:
                if expected_updated_at == "":
                    await cur.execute(
                        "INSERT INTO settings "
                        "(namespace, key, value, updated_at) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (namespace, key) DO NOTHING",
                        (entity.namespace, entity.key, entity.value, updated_at_dt),
                    )
                else:
                    expected_dt = parse_setting_iso(
                        expected_updated_at, entity.namespace, entity.key
                    )
                    await cur.execute(
                        "UPDATE settings "
                        "SET value = %s, updated_at = %s "
                        "WHERE namespace = %s AND key = %s "
                        "AND updated_at = %s",
                        (
                            entity.value,
                            updated_at_dt,
                            entity.namespace,
                            entity.key,
                            expected_dt,
                        ),
                    )
                if cur.rowcount == 0:
                    await conn.commit()
                    return False
            else:
                await cur.execute(
                    "INSERT INTO settings "
                    "(namespace, key, value, updated_at) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (namespace, key) DO UPDATE SET "
                    "value = EXCLUDED.value, "
                    "updated_at = EXCLUDED.updated_at",
                    (entity.namespace, entity.key, entity.value, updated_at_dt),
                )
            await conn.commit()
    except psycopg.Error as exc:
        msg = f"Failed to set setting {entity.namespace}/{entity.key}"
        logger.warning(
            SETTINGS_SET_FAILED,
            namespace=entity.namespace,
            key=entity.key,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc
    logger.debug(SETTINGS_VALUE_SET, namespace=entity.namespace, key=entity.key)
    return True


async def set_many(
    pool: AsyncConnectionPool,
    items: Sequence[SettingRow],
    *,
    expected_updated_at_map: Mapping[SettingRowKey, str] | None = None,
) -> bool:
    """Atomically upsert multiple settings.

    Returns:
        True when all rows were upserted, False when a CAS conflict
        caused the transaction to roll back.

    Raises:
        QueryError: If the database query fails.
    """  # noqa: DOC501 -- _CASConflictError is caught locally and converted to a False return
    if not items:
        return True
    cas_map: Mapping[SettingRowKey, str] = expected_updated_at_map or {}
    try:
        async with pool.connection() as conn:
            try:
                async with conn.transaction(), conn.cursor() as cur:
                    for entity in items:
                        updated_at_dt = parse_setting_iso(
                            entity.updated_at, entity.namespace, entity.key
                        )
                        expected = cas_map.get((entity.namespace, entity.key))
                        if expected is None:
                            await cur.execute(
                                "INSERT INTO settings "
                                "(namespace, key, value, updated_at) "
                                "VALUES (%s, %s, %s, %s) "
                                "ON CONFLICT (namespace, key) "
                                "DO UPDATE SET "
                                "value = EXCLUDED.value, "
                                "updated_at = EXCLUDED.updated_at",
                                (
                                    entity.namespace,
                                    entity.key,
                                    entity.value,
                                    updated_at_dt,
                                ),
                            )
                            continue
                        if expected == "":
                            await cur.execute(
                                "INSERT INTO settings "
                                "(namespace, key, value, updated_at) "
                                "VALUES (%s, %s, %s, %s) "
                                "ON CONFLICT (namespace, key) "
                                "DO NOTHING",
                                (
                                    entity.namespace,
                                    entity.key,
                                    entity.value,
                                    updated_at_dt,
                                ),
                            )
                            if cur.rowcount == 0:
                                raise _CASConflictError  # noqa: TRY301
                            continue
                        expected_dt = parse_setting_iso(
                            expected, entity.namespace, entity.key
                        )
                        await cur.execute(
                            "UPDATE settings "
                            "SET value = %s, updated_at = %s "
                            "WHERE namespace = %s AND key = %s "
                            "AND updated_at = %s",
                            (
                                entity.value,
                                updated_at_dt,
                                entity.namespace,
                                entity.key,
                                expected_dt,
                            ),
                        )
                        if cur.rowcount == 0:
                            raise _CASConflictError  # noqa: TRY301
            except _CASConflictError:
                return False
    except psycopg.Error as exc:
        msg = "Failed to set_many settings"
        logger.warning(
            SETTINGS_SET_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            item_count=len(items),
        )
        raise QueryError(msg) from exc
    for entity in items:
        logger.debug(SETTINGS_VALUE_SET, namespace=entity.namespace, key=entity.key)
    return True


__all__ = ["parse_setting_iso", "set_if_unchanged", "set_many"]
