"""Postgres repositories for conversational clarify-and-propose state.

Siblings of the SQLite implementations, backed by
``psycopg_pool.AsyncConnectionPool``. ``TIMESTAMPTZ`` columns return
native ``datetime`` objects; the shared timestamp coercer normalises
them (and any legacy ISO strings) to UTC-aware values. Satisfy the
``conversation_protocol`` protocols structurally.
"""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from synthorg.core.enums import ConversationRole, ConversationStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.meta.chief_of_staff.enums import ConversationKind
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONVERSATION_FAILED,
    PERSISTENCE_CONVERSATION_FETCHED,
    PERSISTENCE_CONVERSATION_LISTED,
    PERSISTENCE_CONVERSATION_TURN_APPENDED,
    PERSISTENCE_CONVERSATION_TURN_FAILED,
    PERSISTENCE_CONVERSATION_TURN_QUERIED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    validate_pagination_args,
)
from synthorg.persistence.conversation_protocol import (
    ConversationTurnFilterSpec,
)

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Any

    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000
_ALLOWED_TRANSITION_KEYS: frozenset[str] = frozenset({"updated_at"})

_CONVERSATIONS_UPSERT_SQL = """
    INSERT INTO conversations
        (id, created_by, created_at, updated_at, status, kind)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        created_by = EXCLUDED.created_by,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        status = EXCLUDED.status,
        kind = EXCLUDED.kind
"""

_CONVERSATION_COLUMNS = "id, created_by, created_at, updated_at, status, kind"

_TURN_INSERT_SQL = """
    INSERT INTO conversation_turns
        (id, conversation_id, sequence, role, content,
         author_agent_id, author_name, routed_topic, routing_confidence,
         created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_TURN_COLUMNS = (
    "id, conversation_id, sequence, role, content, "
    "author_agent_id, author_name, routed_topic, routing_confidence, created_at"
)

_TURN_NEXT_SEQUENCE_SQL = """
    SELECT COALESCE(MAX(sequence), -1) + 1 FROM conversation_turns
    WHERE conversation_id = %s
"""

# Bounded retry on the (conversation_id, sequence) uniqueness race.
# Two concurrent ``converse()`` calls can both compute the same
# sequence from a stale read and the second insert will collide. We
# re-query the live max sequence and retry the insert; with a small
# bound any caller losing repeatedly is a sign of write-side
# contention worth surfacing as a constraint violation.
_TURN_APPEND_MAX_RETRIES: int = 3
# Postgres exposes the named constraint via diag.constraint_name.
_TURN_SEQUENCE_UNIQUE_CONSTRAINT: str = "uq_ct_conversation_sequence"


def _row_to_conversation(row: dict[str, Any]) -> Conversation:
    """Convert a Postgres dict row into a :class:`Conversation`.

    Returns:
        Result of type ``Conversation``.

    Raises:
        QueryError: If row deserialization or validation fails.
    """
    try:
        return Conversation(
            id=str(row["id"]),
            created_by=str(row["created_by"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
            status=ConversationStatus(str(row["status"])),
            kind=ConversationKind(str(row["kind"])),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversation row"
        logger.warning(
            PERSISTENCE_CONVERSATION_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _row_to_turn(row: dict[str, Any]) -> ConversationTurn:
    """Convert a Postgres dict row into a :class:`ConversationTurn`.

    Returns:
        Result of type ``ConversationTurn``.

    Raises:
        QueryError: If the database query fails.
    """
    try:
        author_agent_id = row["author_agent_id"]
        author_name = row["author_name"]
        routed_topic = row["routed_topic"]
        routing_confidence = row["routing_confidence"]
        return ConversationTurn(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            sequence=int(row["sequence"]),
            role=ConversationRole(str(row["role"])),
            content=str(row["content"]),
            author_agent_id=(None if author_agent_id is None else str(author_agent_id)),
            author_name=None if author_name is None else str(author_name),
            routed_topic=None if routed_topic is None else str(routed_topic),
            routing_confidence=(
                None if routing_confidence is None else float(routing_confidence)
            ),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse conversation turn row"
        logger.warning(
            PERSISTENCE_CONVERSATION_TURN_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class PostgresConversationRepository:
    """Postgres-backed conversation header repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: Conversation) -> None:
        """Upsert a conversation header.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = (
            entity.id,
            entity.created_by,
            entity.created_at,
            entity.updated_at,
            entity.status.value,
            entity.kind.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_CONVERSATIONS_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            msg = f"Constraint violation saving conversation {entity.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="save",
                conversation_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save conversation {entity.id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="save",
                conversation_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> Conversation | None:
        """Get a conversation by id, or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        sql = f"SELECT {_CONVERSATION_COLUMNS} FROM conversations WHERE id = %s"  # noqa: S608 -- fixed column list
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch conversation {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="get",
                conversation_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        conv = _row_to_conversation(row)
        logger.debug(PERSISTENCE_CONVERSATION_FETCHED, conversation_id=entity_id)
        return conv

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        """List conversations newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATION_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_CONVERSATION_COLUMNS} "  # noqa: S608 -- fixed column list
                    "FROM conversations ORDER BY created_at DESC, id DESC "
                    "LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
                items = tuple(_row_to_conversation(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to list conversations"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CONVERSATION_LISTED, count=len(items))
        return items

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the conversation status.

        ``**updates`` accepts only ``updated_at``; any other key raises
        ``QueryError``.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on mismatch or missing row.

        Raises:
            QueryError: On database errors or an unsupported update key.
        """
        unknown = set(updates) - _ALLOWED_TRANSITION_KEYS
        if unknown:
            msg = (
                "transition_if accepts only 'updated_at'; "
                f"got unsupported keys {sorted(unknown)!r}"
            )
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="transition_if",
                conversation_id=entity_id,
                error=msg,
            )
            raise QueryError(msg)
        if "updated_at" in updates:
            sql = (
                "UPDATE conversations SET status = %s, updated_at = %s "
                "WHERE id = %s AND status = %s"
            )
            params: tuple[object, ...] = (
                to_state.value,
                updates["updated_at"],
                entity_id,
                from_state.value,
            )
        else:
            sql = "UPDATE conversations SET status = %s WHERE id = %s AND status = %s"
            params = (to_state.value, entity_id, from_state.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                updated = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition conversation {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="transition_if",
                conversation_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return updated

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a conversation by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database operation fails.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversations WHERE id = %s", (entity_id,)
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete conversation {entity_id!r}"
            logger.warning(
                PERSISTENCE_CONVERSATION_FAILED,
                operation="delete",
                conversation_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


class PostgresConversationTurnRepository:
    """Postgres-backed append-only conversation turn repository.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: ConversationTurn) -> None:
        """Append one turn (immutable once written).

        Sequence collisions on ``(conversation_id, sequence)`` are a
        natural TOCTOU race when two concurrent callers compute the
        next sequence from a stale snapshot; this method re-queries
        the live max sequence and retries the insert up to
        ``_TURN_APPEND_MAX_RETRIES`` times before surfacing the
        violation. Other constraint failures (FK miss, CHECK on
        content/role) are not retried and translate directly to
        ``ConstraintViolationError``.

        Raises:
            ConstraintViolationError: On non-sequence constraint
                violations, or a sequence collision that still
                conflicts after the retry budget.
            QueryError: On other database errors.
        """
        current = event
        for attempt in range(_TURN_APPEND_MAX_RETRIES + 1):
            params = (
                current.id,
                current.conversation_id,
                current.sequence,
                current.role.value,
                current.content,
                current.author_agent_id,
                current.author_name,
                current.routed_topic,
                current.routing_confidence,
                current.created_at,
            )
            try:
                async with self._pool.connection() as conn, conn.cursor() as cur:
                    await cur.execute(_TURN_INSERT_SQL, params)
                    await conn.commit()
                break
            except psycopg.errors.IntegrityError as exc:
                constraint = (
                    getattr(getattr(exc, "diag", None), "constraint_name", None)
                    or "<unknown>"
                )
                sequence_race = (
                    constraint == _TURN_SEQUENCE_UNIQUE_CONSTRAINT
                    and attempt < _TURN_APPEND_MAX_RETRIES
                )
                if sequence_race:
                    try:
                        async with (
                            self._pool.connection() as conn,
                            conn.cursor() as cur,
                        ):
                            await cur.execute(
                                _TURN_NEXT_SEQUENCE_SQL,
                                (current.conversation_id,),
                            )
                            row = await cur.fetchone()
                            next_sequence = int(row[0]) if row is not None else 0
                    except psycopg.Error as resequence_exc:
                        msg = (
                            "Failed to resolve next sequence while appending "
                            f"turn {current.id!r} "
                            f"(conversation {current.conversation_id!r})"
                        )
                        logger.warning(
                            PERSISTENCE_CONVERSATION_TURN_FAILED,
                            operation="append",
                            phase="resequence",
                            conversation_id=current.conversation_id,
                            error_type=type(resequence_exc).__name__,
                            error=safe_error_description(resequence_exc),
                        )
                        raise QueryError(msg) from resequence_exc
                    current = current.model_copy(
                        update={"sequence": next_sequence},
                    )
                    continue
                msg = (
                    "Constraint violation appending turn "
                    f"{current.id!r} (conversation {current.conversation_id!r})"
                )
                logger.warning(
                    PERSISTENCE_CONVERSATION_TURN_FAILED,
                    operation="append",
                    conversation_id=current.conversation_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=constraint) from exc
            except psycopg.Error as exc:
                msg = f"Failed to append turn {current.id!r}"
                logger.warning(
                    PERSISTENCE_CONVERSATION_TURN_FAILED,
                    operation="append",
                    conversation_id=current.conversation_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_CONVERSATION_TURN_APPENDED,
            conversation_id=current.conversation_id,
            sequence=current.sequence,
        )

    async def query(
        self,
        filter_spec: ConversationTurnFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        """Return turns matching the spec, newest-first (paginated).

        Order is ``(sequence DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.

        Returns:
            The matching entities.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONVERSATION_TURN_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.conversation_id is not None:
            clauses.append("conversation_id = %s")
            params.append(filter_spec.conversation_id)
        where = " AND ".join(clauses) if clauses else "TRUE"
        params.extend([effective_limit, offset])
        sql = (
            f"SELECT {_TURN_COLUMNS} "  # noqa: S608 -- fixed columns + closed predicate set
            f"FROM conversation_turns WHERE {where} "
            "ORDER BY sequence DESC, id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                items = tuple(_row_to_turn(r) for r in rows)
        except psycopg.Error as exc:
            msg = "Failed to query conversation turns"
            logger.warning(
                PERSISTENCE_CONVERSATION_TURN_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CONVERSATION_TURN_QUERIED, count=len(items))
        return items

    async def purge_before(self, threshold: datetime) -> int:
        """Delete turns created before ``threshold``. Returns rows removed.

        Raises:
            QueryError: On database errors.

        Returns:
            Numeric result of the operation.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversation_turns WHERE created_at < %s",
                    (threshold,),
                )
                removed = max(0, cur.rowcount)
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge conversation turns"
            logger.warning(
                PERSISTENCE_CONVERSATION_TURN_FAILED,
                operation="purge_before",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return removed


__all__ = [
    "PostgresConversationRepository",
    "PostgresConversationTurnRepository",
]
