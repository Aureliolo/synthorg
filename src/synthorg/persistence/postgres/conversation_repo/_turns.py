"""Postgres conversation-turn repository.

Append + filtered query + retention purge; turns are immutable once
written. Satisfies ``ConversationTurnRepository`` structurally.
"""

from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import (
    QueryError,
    TurnSequenceConflictError,
)
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.conversation_turn import (
    PERSISTENCE_CONVERSATION_TURN_APPENDED,
    PERSISTENCE_CONVERSATION_TURN_FAILED,
    PERSISTENCE_CONVERSATION_TURN_QUERIED,
)
from synthorg.persistence._conversation_marshalling import row_to_turn
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    TURN_APPEND_MAX_RETRIES,
    validate_pagination_args,
)
from synthorg.persistence.conversation_protocol import (
    ConversationTurnFilterSpec,
)
from synthorg.persistence.postgres._integrity import (
    constraint_name,
    raise_constraint_violation,
    shared_sqlstate,
)

logger = get_logger(__name__)

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

# Postgres exposes the named constraint via diag.constraint_name.
_TURN_SEQUENCE_UNIQUE_CONSTRAINT: str = "uq_ct_conversation_sequence"


def _log_append_failure(
    conversation_id: str, exc: BaseException, *, phase: str | None = None
) -> None:
    """Log an append-turn failure with the shared redacted-error shape."""
    extra = {"phase": phase} if phase is not None else {}
    logger.warning(
        PERSISTENCE_CONVERSATION_TURN_FAILED,
        operation="append",
        conversation_id=conversation_id,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
        **extra,
    )


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
        ``TURN_APPEND_MAX_RETRIES`` times before surfacing the
        violation. Other constraint failures (FK miss, CHECK on
        content/role) are not retried and translate directly to
        ``ConstraintViolationError``.

        Raises:
            TurnSequenceConflictError: On a sequence collision that still
                conflicts after the retry budget (retryable 409).
            ConstraintViolationError: On non-sequence constraint
                violations (FK / CHECK).
            QueryError: On other database errors.
        """
        # See docs/reference/retry-patterns.md: Pattern C/CAS. This is a
        # constraint-branch resequence on the (conversation_id, sequence)
        # uniqueness race, not a transient-I/O backoff; it stays in the
        # repository and must not move to GeneralRetryHandler.
        #
        # Unlike the SQLite sibling, which holds one serialising write
        # lock (``_write_context``) across the whole read-then-insert,
        # each Postgres attempt below takes a fresh pool connection for
        # the insert AND another for the re-sequence read, so the
        # re-sequence and the retry insert are NOT serialised: a
        # concurrent appender can claim the just-read sequence again
        # between the two. Correctness rests on the unique constraint
        # (which rejects the collision) plus the bounded retry (which
        # resolves it), not on serialisation; the retry narrows the race
        # window rather than closing it.
        current = event
        for attempt in range(TURN_APPEND_MAX_RETRIES + 1):
            params = (
                str(current.id),
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
                constraint = constraint_name(exc)
                sequence_race = (
                    constraint == _TURN_SEQUENCE_UNIQUE_CONSTRAINT
                    and attempt < TURN_APPEND_MAX_RETRIES
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
                        _log_append_failure(
                            current.conversation_id,
                            resequence_exc,
                            phase="resequence",
                        )
                        raise QueryError(msg) from resequence_exc
                    current = current.model_copy(
                        update={"sequence": next_sequence},
                    )
                    continue
                if constraint == _TURN_SEQUENCE_UNIQUE_CONSTRAINT:
                    # Retry budget exhausted on a genuine sequence race
                    # (a cross-process append past the in-process lock):
                    # transient and retryable, so surface a 409 rather
                    # than a mislabelled non-retryable 400.
                    msg = (
                        "Turn sequence conflict appending turn "
                        f"{current.id!r} (conversation {current.conversation_id!r})"
                    )
                    _log_append_failure(current.conversation_id, exc)
                    raise TurnSequenceConflictError(
                        msg, constraint=constraint, sqlstate=shared_sqlstate(exc)
                    ) from exc
                msg = (
                    "Constraint violation appending turn "
                    f"{current.id!r} (conversation {current.conversation_id!r})"
                )
                _log_append_failure(current.conversation_id, exc)
                raise_constraint_violation(exc, msg)
            except psycopg.Error as exc:
                msg = f"Failed to append turn {current.id!r}"
                _log_append_failure(current.conversation_id, exc)
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
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.conversation_id is not None:
            clauses.append("conversation_id = %s")
            params.append(filter_spec.conversation_id)
        if filter_spec.conversation_ids is not None:
            # An empty set is written as a false predicate: ``IN ()`` is a
            # syntax error, and dropping the clause would return every turn
            # in the table for a caller that asked about no conversation.
            if filter_spec.conversation_ids:
                clauses.append("conversation_id = ANY(%s)")
                params.append(list(filter_spec.conversation_ids))
            else:
                clauses.append("FALSE")
        if filter_spec.sequence is not None:
            clauses.append("sequence = %s")
            params.append(filter_spec.sequence)
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
                items = tuple(row_to_turn(r) for r in rows)
        except QueryError:
            raise
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

        ``threshold`` must be timezone-aware; a naive value would be bound
        against a ``TIMESTAMPTZ`` column in the session timezone and
        silently shift the cut-off, so it is rejected up front rather than
        coerced.

        Raises:
            QueryError: If ``threshold`` is naive, or on database errors.

        Returns:
            Numeric result of the operation.
        """
        if threshold.tzinfo is None:
            msg = "purge_before requires a timezone-aware threshold"
            raise QueryError(msg)
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


__all__ = ["PostgresConversationTurnRepository"]
