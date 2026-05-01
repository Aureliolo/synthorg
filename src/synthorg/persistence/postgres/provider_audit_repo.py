"""Postgres repository for the Provider mutation audit log.

Append-only writer + keyset-paginated reader for
``provider_audit_events``.  Mirrors the SQLite implementation; the
only material differences are the JSONB payload column (vs JSON-text
on SQLite) and TIMESTAMPTZ for ``occurred_at`` (vs ISO 8601 TEXT).
"""

import json
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from synthorg.api.dto_provider_capabilities import (
    ProviderAuditActor,
    ProviderAuditEvent,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_AUDIT_ENTRY_QUERIED,
    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
)
from synthorg.persistence._shared import normalize_utc

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_INSERT_SQL = """
INSERT INTO provider_audit_events (
    provider_name, event_type, actor_id, actor_label,
    payload, occurred_at
) VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id
"""

_LIST_BASE_SQL = """
SELECT id, provider_name, event_type, actor_id, actor_label,
       payload, occurred_at
FROM provider_audit_events
WHERE provider_name = %s
"""


class PostgresProviderAuditRepo:
    """Postgres implementation of :class:`ProviderAuditRepo`.

    Append-only.  Reads use keyset pagination on ``id`` (descending);
    JSONB payloads round-trip through psycopg's ``Jsonb`` adapter.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        """Insert one audit event and return the saved row with id populated."""
        # ``event.payload`` is recursively frozen by the DTO
        # (``MappingProxyType`` / ``tuple`` / ``frozenset``) so the
        # audit row stays append-only at the Python level.
        # ``psycopg.types.json.Jsonb`` calls ``json.dumps`` internally,
        # which cannot encode any of those directly; route through
        # ``model_dump(mode="json")`` so the field-serializer recursively
        # thaws nested containers back to plain builtins before insertion.
        serialized = event.model_dump(mode="json")
        params: tuple[Any, ...] = (
            event.provider_name,
            event.event_type,
            event.actor.id,
            event.actor.label,
            Jsonb(serialized["payload"]),
            normalize_utc(event.occurred_at),
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, params)
                row = await cur.fetchone()
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to record provider audit event"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                provider_name=event.provider_name,
                event_type=event.event_type,
            )
            raise QueryError(msg) from exc
        if row is None:  # pragma: no cover -- RETURNING always yields a row
            msg = "INSERT ... RETURNING produced no row"
            raise QueryError(msg)
        new_id = int(row[0])
        return event.model_copy(update={"id": new_id})

    async def list(
        self,
        *,
        provider_name: NotBlankStr,
        after_id: int | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        """List events for one provider, newest first, with ``has_more``."""
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise QueryError(msg)

        sql = _LIST_BASE_SQL
        params: list[Any] = [provider_name]
        if after_id is not None:
            sql += " AND id < %s"
            params.append(after_id)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limit + 1)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query provider audit events"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                provider_name=provider_name,
                after_id=after_id,
                limit=limit,
            )
            raise QueryError(msg) from exc

        has_more = len(rows) > limit
        page = rows[:limit]
        try:
            events = tuple(self._row_to_event(r) for r in page)
        except QueryError:
            raise
        except Exception as exc:
            # Fail closed on a corrupt audit row instead of letting
            # raw Pydantic / datetime / enum errors escape as 500.
            msg = f"corrupt provider_audit_events row(s) for provider {provider_name!r}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                provider_name=provider_name,
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_AUDIT_ENTRY_QUERIED,
            count=len(events),
            has_more=has_more,
            provider_name=provider_name,
        )
        return events, has_more

    async def purge_before_id(self, *, before_id: int) -> int:
        """Delete events with ``id < before_id``."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM provider_audit_events WHERE id < %s",
                    (before_id,),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge provider audit events"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                before_id=before_id,
            )
            raise QueryError(msg) from exc
        return rowcount

    def _row_to_event(self, row: dict[str, Any]) -> ProviderAuditEvent:
        """Deserialise a Postgres row dict into a ``ProviderAuditEvent``."""
        payload = row["payload"]
        # ``Jsonb`` round-trips natively, but if a row was inserted via
        # raw psycopg adapters we fall back to JSON-text decoding so
        # the repo never crashes on legacy data.
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                msg = f"corrupt payload on row id={row.get('id')!r}"
                raise QueryError(msg) from exc
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            # Audit payloads must be JSON objects.  A persisted scalar /
            # array is corruption -- silently coercing would hide the
            # schema violation.
            msg = (
                f"provider_audit_events.payload on row "
                f"id={row.get('id')!r} is not a JSON object "
                f"(got {type(payload).__name__})"
            )
            raise QueryError(msg)
        # Type-check scalar columns: stringifying a corrupt NULL would
        # produce ``"None"`` and let a bad audit row look valid.
        for col in ("provider_name", "event_type", "actor_id", "actor_label"):
            value = row[col]
            if not isinstance(value, str) or value == "":
                msg = (
                    f"provider_audit_events.{col} on row "
                    f"id={row.get('id')!r} is not a non-empty string: "
                    f"{value!r}"
                )
                raise QueryError(msg)
        id_raw = row["id"]
        if not isinstance(id_raw, int):
            msg = f"provider_audit_events.id is not int: {id_raw!r}"
            raise QueryError(msg)
        actor = ProviderAuditActor(
            id=str(row["actor_id"]),
            label=str(row["actor_label"]),
        )
        return ProviderAuditEvent(
            id=id_raw,
            provider_name=str(row["provider_name"]),
            event_type=str(row["event_type"]),  # type: ignore[arg-type]
            actor=actor,
            payload=payload,
            occurred_at=normalize_utc(row["occurred_at"]),
        )
