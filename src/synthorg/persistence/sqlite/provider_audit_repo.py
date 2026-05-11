"""SQLite repository for the Provider mutation audit log.

Append-only writer + keyset-paginated reader for
``provider_audit_events``.  Mirrors the canonical patterns from
``audit_repository.py`` (security-eval audit) but with a different
schema and a per-provider read API tailored to the dashboard's audit
drawer.
"""

import asyncio
import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

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
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence.provider_audit_protocol import _DEFAULT_LIST_LIMIT_50

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_INSERT_SQL = """
INSERT INTO provider_audit_events (
    provider_name, event_type, actor_id, actor_label,
    payload, occurred_at
) VALUES (?, ?, ?, ?, ?, ?)
"""

_LIST_BASE_SQL = """
SELECT id, provider_name, event_type, actor_id, actor_label,
       payload, occurred_at
FROM provider_audit_events
WHERE provider_name = ?
"""


class SQLiteProviderAuditRepo:
    """SQLite implementation of :class:`ProviderAuditRepo`.

    Append-only.  Reads use keyset pagination on ``id`` (descending)
    so the page boundary stays stable under concurrent inserts.

    Args:
        db: An open ``aiosqlite.Connection``.
        write_lock: Shared backend write lock so writes serialise
            with sibling repos that share the same connection.  Falls
            back to a private lock for standalone test construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        """Insert one audit event and return the saved row with id populated."""
        # ``event.payload`` is recursively frozen by
        # ``ProviderAuditEvent._freeze_payload`` (``MappingProxyType`` /
        # ``tuple`` / ``frozenset`` for dicts / lists / sets respectively)
        # to make the audit row append-only at the Python level.
        # ``json.dumps`` cannot encode any of those directly, so route
        # through ``model_dump(mode="json")`` which calls the
        # ``_serialize_payload`` field-serializer to recursively thaw
        # nested containers back to plain builtins.
        serialized = event.model_dump(mode="json")
        params = (
            event.provider_name,
            event.event_type,
            event.actor.id,
            event.actor.label,
            json.dumps(serialized["payload"], sort_keys=True),
            format_iso_utc(event.occurred_at),
        )
        async with self._write_lock:
            try:
                cursor = await self._db.execute(_INSERT_SQL, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to record provider audit event"
                logger.warning(
                    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    provider_name=event.provider_name,
                    event_type=event.event_type,
                )
                raise QueryError(msg) from exc
            new_id = cursor.lastrowid
        return event.model_copy(update={"id": new_id})

    async def list(
        self,
        *,
        provider_name: NotBlankStr,
        after_id: int | None = None,
        limit: int = _DEFAULT_LIST_LIMIT_50,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        """List events for one provider, newest first, with ``has_more`` overflow."""
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise QueryError(msg)

        sql = _LIST_BASE_SQL
        params: list[object] = [provider_name]
        if after_id is not None:
            sql += " AND id < ?"
            params.append(after_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit + 1)

        try:
            cursor = await self._db.execute(sql, params)
            rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            events = tuple(self._row_to_event(dict(r)) for r in page)
        except QueryError:
            # Already logged + classified by ``_row_to_event``;
            # propagate so callers see the repo's exception type.
            raise
        except Exception as exc:
            # A bad row would otherwise escape as raw Pydantic /
            # enum / datetime errors, bypassing the warning log and
            # turning one corrupt row into an unexpected 500.
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
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM provider_audit_events WHERE id < ?",
                    (before_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to purge provider audit events"
                logger.warning(
                    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    before_id=before_id,
                )
                raise QueryError(msg) from exc
        return cursor.rowcount

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )

    def _row_to_event(self, row: dict[str, object]) -> ProviderAuditEvent:
        """Deserialise a row dict into a ``ProviderAuditEvent``."""
        raw_payload = row["payload"]
        try:
            payload = json.loads(str(raw_payload)) if raw_payload else {}
        except json.JSONDecodeError as exc:
            msg = f"corrupt payload on row id={row.get('id')!r}"
            raise QueryError(msg) from exc
        if not isinstance(payload, dict):
            # Audit payloads must be JSON objects.  A persisted scalar /
            # array / null is corruption -- silently coercing would hide
            # the schema violation and let bad rows slip downstream.
            msg = (
                f"provider_audit_events.payload on row "
                f"id={row.get('id')!r} is not a JSON object "
                f"(got {type(payload).__name__})"
            )
            raise QueryError(msg)
        # Type-check scalar columns: ``str(...)`` of a corrupt NULL
        # would produce ``"None"`` and let a malformed audit row
        # masquerade as valid.  Fail closed instead.
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
        occurred_at_raw = row["occurred_at"]
        if not isinstance(occurred_at_raw, str):
            msg = (
                f"provider_audit_events.occurred_at on row "
                f"id={row.get('id')!r} is not a string: "
                f"{occurred_at_raw!r}"
            )
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
            occurred_at=parse_iso_utc(occurred_at_raw),
        )
