"""Postgres repository implementation for decision records.

Append-only: records can be appended and queried but never updated
or deleted, preserving audit integrity.

Version numbers for ``(task_id, version)`` are computed via a
``SELECT COALESCE(MAX(version), 0) + 1`` subquery inside the INSERT.
psycopg uses Postgres' default READ COMMITTED isolation level (not
SERIALIZABLE), so two concurrent writers can race and compute the
same next version.  The ``UNIQUE(task_id, version)`` constraint
guarantees only one writer wins -- the other gets a
``UniqueViolation`` which this repository catches and retries with a
newly computed version.  After a small bounded number of retries the
write is treated as a duplicate ``record_id`` and surfaced as
``DuplicateRecordError``.

The asyncio.Lock serialization used by the SQLite sibling is
therefore replaced by the retry loop below -- no in-process locks
are needed because Postgres enforces the serialization itself.
"""

import copy
import json
from datetime import UTC
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, ValidationError

from synthorg.core.enums import DecisionOutcome
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.engine.decisions import DecisionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED,
    PERSISTENCE_DECISION_RECORD_QUERIED,
    PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence.decision_protocol import (
    DecisionFilterSpec,
    DecisionRole,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000


_COLS = (
    "id, task_id, approval_id, executing_agent_id, reviewer_agent_id, "
    "decision, reason, criteria_snapshot, recorded_at, version, metadata"
)

# Maps ``DecisionRole`` Literal values to their corresponding column
# name.  Keeps the dynamic-column SQL in ``list_by_agent`` bounded to a
# closed set of identifiers that are never user-supplied.
_ROLE_TO_COLUMN: Final[dict[str, str]] = {
    "executor": "executing_agent_id",
    "reviewer": "reviewer_agent_id",
}

_INSERT_SQL: Final[str] = """\
INSERT INTO decision_records (
    id, task_id, approval_id, executing_agent_id, reviewer_agent_id,
    decision, reason, criteria_snapshot, recorded_at, version, metadata
) VALUES (
    %(id)s, %(task_id)s, %(approval_id)s, %(executing_agent_id)s,
    %(reviewer_agent_id)s, %(decision)s, %(reason)s,
    %(criteria_snapshot)s, %(recorded_at)s,
    (SELECT COALESCE(MAX(version), 0) + 1
       FROM decision_records WHERE task_id = %(task_id)s),
    %(metadata)s
)"""


def _build_insert_params(  # noqa: PLR0913
    *,
    record_id: NotBlankStr,
    task_id: NotBlankStr,
    approval_id: NotBlankStr | None,
    executing_agent_id: NotBlankStr,
    reviewer_agent_id: NotBlankStr,
    decision: DecisionOutcome,
    reason: str | None,
    criteria_snapshot: tuple[NotBlankStr, ...],
    recorded_at: AwareDatetime,
    metadata: dict[str, object],
) -> dict[str, object]:
    """Shape the bound-parameter dict for the INSERT statement.

    Normalizes ``recorded_at`` to UTC so ordering of the ``recorded_at``
    column is equivalent to chronological ordering across
    mixed-timezone callers.

    In Postgres, JSONB columns accept Python dicts/lists directly
    (psycopg converts them). We wrap with ``Jsonb()`` for clarity.

    Returns:
        Result of type ``dict[str, object]``.
    """
    return {
        "id": record_id,
        "task_id": task_id,
        "approval_id": approval_id,
        "executing_agent_id": executing_agent_id,
        "reviewer_agent_id": reviewer_agent_id,
        "decision": decision.value,
        "reason": reason,
        "criteria_snapshot": Jsonb(list(criteria_snapshot)),
        "recorded_at": recorded_at.astimezone(UTC),
        # ``metadata`` may contain ``MappingProxyType`` (from the draft
        # record's frozen view) at arbitrary nesting depth; unwrap
        # recursively so only plain dicts and lists are stored.
        "metadata": Jsonb(_unfreeze_for_json(metadata)),
    }


def _unfreeze_for_json(value: object) -> object:
    """Recursively convert MappingProxyType/tuple/frozenset to JSON primitives.

    Returns:
        Result of type ``object``.
    """
    if isinstance(value, MappingProxyType):
        return {k: _unfreeze_for_json(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {k: _unfreeze_for_json(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_unfreeze_for_json(item) for item in value]
    if isinstance(value, frozenset | set):
        return [_unfreeze_for_json(item) for item in value]
    return value


class PostgresDecisionRepository:
    """Postgres implementation of the ``DecisionRepository`` protocol.

    Append-only: decision records are immutable audit entries of
    review gate decisions. Timestamps are normalized to UTC before
    storage for consistent ordering.

    Unlike the SQLite sibling, the ``UNIQUE(task_id, version)``
    constraint combined with a bounded retry loop eliminates the
    need for an explicit asyncio.Lock to serialize concurrent
    writers. psycopg uses Postgres' default READ COMMITTED
    isolation, so two concurrent writers may compute the same next
    version; the constraint guarantees only one wins and the loser
    retries with a freshly computed version (see
    ``append_with_next_version``).

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append_with_next_version(  # noqa: PLR0913
        self,
        *,
        record_id: NotBlankStr,
        task_id: NotBlankStr,
        approval_id: NotBlankStr | None,
        executing_agent_id: NotBlankStr,
        reviewer_agent_id: NotBlankStr,
        decision: DecisionOutcome,
        reason: str | None,
        criteria_snapshot: tuple[NotBlankStr, ...],
        recorded_at: AwareDatetime,
        metadata: dict[str, object] | None = None,
    ) -> DecisionRecord:
        """Atomically insert a decision record with server-computed version.

        Version is derived via ``COALESCE(MAX(version), 0) + 1`` inside
        the ``INSERT`` statement itself.  Under READ COMMITTED (the
        psycopg default) two concurrent writers may compute the same
        next version, so the ``UNIQUE(task_id, version)`` constraint
        breaks the tie and the loser retries up to
        ``_MAX_VERSION_RACE_ATTEMPTS`` times with a freshly computed
        version.  After exhausting retries the write is surfaced as
        ``DuplicateRecordError``.

        See the ``DecisionRepository`` protocol for the full argument
        descriptions. ``recorded_at`` is normalized to UTC before
        storage; records read back via ``get`` / ``list_by_task`` /
        ``list_by_agent`` will therefore always have UTC timestamps.
        ``metadata`` defaults to ``{}`` so callers that do not attach
        metadata do not have to pass an empty dict.

        Raises:
            DuplicateRecordError: If a record with ``record_id`` exists
                OR a concurrent write won the ``UNIQUE(task_id, version)``
                race.
            ValueError: If ``recorded_at`` is a naive datetime (no
                tzinfo).
            ValidationError: If the model-level normalization rejects
                the input. We deliberately do NOT wrap as QueryError;
                malformed inputs are programming errors that must
                surface loudly.
            QueryError: If the SQL operation fails.
            TypeError: If an argument has the wrong type.

        Returns:
            Result of type ``DecisionRecord``.
        """
        # Deep-copy metadata so nested dicts/lists the caller retains
        # are never aliased by the stored record.
        metadata_view: MappingProxyType[str, object] = MappingProxyType(
            copy.deepcopy(dict(metadata or {}))
        )
        # Reject naive datetimes explicitly.
        if recorded_at.tzinfo is None:
            msg = (
                f"recorded_at must be timezone-aware, got a naive "
                f"datetime for decision record {record_id!r}"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=record_id,
                task_id=task_id,
                error_type="NaiveDatetimeRejected",
                error=msg,
                recorded_at=recorded_at.isoformat(),
            )
            raise ValueError(msg)
        # Normalize recorded_at to UTC up-front.
        recorded_at_utc = recorded_at.astimezone(UTC)
        try:
            draft_record = DecisionRecord(
                id=record_id,
                task_id=task_id,
                approval_id=approval_id,
                executing_agent_id=executing_agent_id,
                reviewer_agent_id=reviewer_agent_id,
                decision=decision,
                reason=reason,
                criteria_snapshot=criteria_snapshot,
                recorded_at=recorded_at_utc,
                version=1,  # placeholder; overwritten after insert
                metadata=metadata_view,
            )
        except ValidationError:
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=record_id,
                task_id=task_id,
                error_type="ValidationError",
            )
            raise

        try:
            params = _build_insert_params(
                record_id=record_id,
                task_id=task_id,
                approval_id=approval_id,
                executing_agent_id=executing_agent_id,
                reviewer_agent_id=reviewer_agent_id,
                decision=decision,
                reason=draft_record.reason,
                criteria_snapshot=draft_record.criteria_snapshot,
                recorded_at=draft_record.recorded_at,
                metadata=dict(draft_record.metadata),
            )
        except TypeError:
            # Raised by json serialization of non-JSON-serializable values
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=record_id,
                task_id=task_id,
                approval_id=approval_id,
                executing_agent_id=executing_agent_id,
                reviewer_agent_id=reviewer_agent_id,
                error_type="TypeError",
            )
            raise

        assigned_version = await self._execute_insert(record_id, params)
        return draft_record.model_copy(update={"version": assigned_version})

    #: Maximum attempts to retry a version-race UniqueViolation before
    #: giving up and treating the failure as a genuine duplicate record
    #: id.  Picked to comfortably exceed contention between concurrent
    #: review gates on the same task without allowing runaway retries
    #: under pathological load.
    _MAX_VERSION_RACE_ATTEMPTS: Final[int] = 5

    async def _execute_insert(
        self,
        record_id: NotBlankStr,
        params: dict[str, object],
    ) -> int:
        """Insert the record and return the server-assigned version.

        psycopg uses Postgres' default READ COMMITTED isolation, so the
        ``SELECT MAX(version) + 1`` subquery inside ``_INSERT_SQL`` is
        NOT atomic against concurrent writers on the same ``task_id``.
        Two concurrent writers can compute the same next version; the
        ``UNIQUE(task_id, version)`` constraint forces exactly one to
        succeed and the loser gets a ``UniqueViolation``.

        We distinguish the two unique-constraint paths by inspecting
        ``exc.diag.constraint_name``:
        - The ``id`` primary key: a genuine duplicate record id; raise
          ``DuplicateRecordError`` immediately.
        - The ``(task_id, version)`` unique constraint: a version race;
          retry up to ``_MAX_VERSION_RACE_ATTEMPTS`` times with a fresh
          subquery result.  If retries are exhausted, fall through to a
          final ``DuplicateRecordError``.

        Keeps ``append_with_next_version`` under the 50-line budget and
        centralizes the error-mapping logic for the write path.

        Returns:
            Numeric result of the operation.

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
            CheckViolation: If a CHECK constraint is violated.
            ForeignKeyViolation: If a foreign-key constraint is violated.
            NotNullViolation: If a NOT NULL column receives ``NULL``.
        """
        last_exc: psycopg.errors.UniqueViolation | None = None
        # See docs/reference/retry-patterns.md: Pattern C/CAS -- version-
        # race retry that branches on the constraint name to distinguish
        # the (task_id, version) race from a real duplicate insert.
        for attempt in range(self._MAX_VERSION_RACE_ATTEMPTS):
            try:
                async with (
                    self._pool.connection() as conn,
                    conn.cursor() as cur,
                ):
                    await cur.execute(_INSERT_SQL, params)
                    await cur.execute(
                        "SELECT version FROM decision_records WHERE id = %s",
                        (record_id,),
                    )
                    row = await cur.fetchone()
            except psycopg.errors.UniqueViolation as exc:
                last_exc = exc
                constraint = getattr(exc.diag, "constraint_name", "") or ""
                is_version_race = "version" in constraint
                if not is_version_race:
                    # Genuine duplicate record id -- surface immediately.
                    msg = f"Duplicate decision record {record_id!r}"
                    logger.warning(
                        PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                        record_id=record_id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        sqlstate=exc.sqlstate,
                        constraint=constraint,
                    )
                    raise DuplicateRecordError(msg) from exc
                # Version race -- log at DEBUG and retry.
                logger.debug(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=record_id,
                    attempt=attempt + 1,
                    max_attempts=self._MAX_VERSION_RACE_ATTEMPTS,
                    sqlstate=exc.sqlstate,
                    constraint=constraint,
                    error_type="VersionRace",
                )
                continue
            except (
                psycopg.errors.CheckViolation,
                psycopg.errors.ForeignKeyViolation,
                psycopg.errors.NotNullViolation,
            ) as exc:
                # CHECK / FOREIGN KEY / NOT NULL violations are
                # schema-level programming errors -- re-raise the
                # original error so callers see the structural failure.
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=record_id,
                    error_type=type(exc).__name__,
                    violation_category="StructuralConstraintViolation",
                    error=safe_error_description(exc),
                    sqlstate=exc.sqlstate,
                )
                raise
            except psycopg.Error as exc:
                msg = f"Failed to save decision record {record_id!r}"
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=record_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            if row is None:
                # Defensive: SELECT immediately after INSERT should
                # always find the row.  Surface the anomaly loudly.
                msg = (
                    f"Failed to read back decision record {record_id!r} "
                    "immediately after insert"
                )
                task_id_value = params.get("task_id")
                logger.error(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=record_id,
                    task_id=task_id_value,
                    error=msg,
                )
                raise QueryError(msg)
            return int(row[0])

        # All retries exhausted on version-race path.
        msg = (
            f"Decision record {record_id!r} lost the version race "
            f"after {self._MAX_VERSION_RACE_ATTEMPTS} attempts"
        )
        logger.warning(
            PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
            record_id=record_id,
            error=msg,
            max_attempts=self._MAX_VERSION_RACE_ATTEMPTS,
            error_type="VersionRaceExhausted",
        )
        raise DuplicateRecordError(msg) from last_exc

    async def append(self, event: DecisionRecord) -> None:
        """Append a decision record with a precomputed version.

        This method is the append interface from
        :class:`AppendOnlyRepository`; most callers use
        ``append_with_next_version`` instead. Version must be set
        by the caller.

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
            TypeError: If an argument has the wrong type.
        """
        # Deep-copy metadata so nested dicts/lists the caller retains
        # are never aliased by the stored record.
        metadata_view: MappingProxyType[str, object] = MappingProxyType(
            copy.deepcopy(dict(event.metadata or {}))
        )
        try:
            params = _build_insert_params(
                record_id=event.id,
                task_id=event.task_id,
                approval_id=event.approval_id,
                executing_agent_id=event.executing_agent_id,
                reviewer_agent_id=event.reviewer_agent_id,
                decision=event.decision,
                reason=event.reason,
                criteria_snapshot=event.criteria_snapshot,
                recorded_at=event.recorded_at,
                metadata=dict(metadata_view),
            )
        except TypeError:
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=event.id,
                task_id=event.task_id,
                error_type="TypeError",
            )
            raise
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                insert_sql = """\
                INSERT INTO decision_records (
                    id, task_id, approval_id, executing_agent_id,
                    reviewer_agent_id, decision, reason,
                    criteria_snapshot, recorded_at, version, metadata
                ) VALUES (
                    %(id)s, %(task_id)s, %(approval_id)s,
                    %(executing_agent_id)s, %(reviewer_agent_id)s,
                    %(decision)s, %(reason)s, %(criteria_snapshot)s,
                    %(recorded_at)s, %(version)s, %(metadata)s
                )"""
                await cur.execute(
                    insert_sql,
                    {**params, "version": event.version},
                )
        except psycopg.errors.UniqueViolation as exc:
            msg = f"Duplicate decision record {event.id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=event.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to append decision record {event.id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=event.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: DecisionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """Query decision records with optional filters and pagination.

        When only task_id is specified, results are oldest-first
        (ascending recorded_at). When agent_id and role are specified
        without task_id, results are newest-first. Mixed filters default
        to task-oriented (oldest-first) ordering.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)

        # Determine ordering and WHERE clause based on filter spec.
        task_id_filter = filter_spec.task_id
        agent_id_filter = filter_spec.agent_id
        role_filter = filter_spec.role

        where_clauses: list[str] = []
        params_list: list[object] = []

        if task_id_filter is not None:
            where_clauses.append("task_id = %s")
            params_list.append(task_id_filter)

        if agent_id_filter is not None and role_filter is not None:
            # Both agent_id and role specified: filter by the role column.
            if role_filter == "executor":
                where_clauses.append("executing_agent_id = %s")
            else:  # "reviewer"
                where_clauses.append("reviewer_agent_id = %s")
            params_list.append(agent_id_filter)
        elif agent_id_filter is not None:
            # Only agent_id: match either role.
            where_clauses.append("(executing_agent_id = %s OR reviewer_agent_id = %s)")
            params_list.extend([agent_id_filter, agent_id_filter])

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Determine sort order: task-oriented (oldest-first) if task_id
        # is present, otherwise newest-first.
        if task_id_filter is not None:
            order_by = "recorded_at ASC, id ASC"
        else:
            order_by = "recorded_at DESC, id DESC"

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                query_sql = f"""\
                SELECT {_COLS} FROM decision_records
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s"""  # noqa: S608
                params_list.extend([effective_limit, offset])
                await cur.execute(query_sql, params_list)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query decision records"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results = tuple(self._row_to_record(dict(row)) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            count=len(results),
        )
        return results

    async def get(self, record_id: NotBlankStr) -> DecisionRecord | None:
        """Retrieve a decision record by ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLS} FROM decision_records WHERE id = %s",  # noqa: S608
                    (record_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch decision record {record_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                record_id=record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_by_task(
        self,
        task_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records for a task, oldest first.

        Args:
            task_id: Identifier of the task whose decisions are being
                listed.
            limit: Maximum number of records to return on this page;
                must be ``>= 1``. The repo additionally clamps the
                returned slice to ``_MAX_PAGE_LIMIT`` to prevent a
                runaway caller from materialising the full table.
            offset: Number of records to skip before the page; must
                be ``>= 0``.

        Returns:
            ``tuple[DecisionRecord, ...]`` ordered ascending by
            ``(recorded_at, id)`` so paginated results are stable
            and a backfilled decision (low ``recorded_at``, high
            ``version``) still sorts to the correct chronological
            position. The ``id`` tiebreaker matches the deterministic
            ordering used by ``list_by_agent``.

        Raises:
            QueryError: If ``limit`` / ``offset`` fail the type or
                bounds check, or if the underlying ``psycopg`` query
                raises. The structured ``WARNING`` is emitted before
                the raise so operators can correlate the failure.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            task_id=task_id,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                # ``recorded_at ASC, id ASC`` matches the protocol's
                # "oldest first" contract via the same wall-clock
                # ordering ``list_by_agent`` uses (DESC there). A
                # backfill that lands an old decision with a fresh
                # high-numbered ``version`` would otherwise sort to
                # the end under ``version ASC``.
                await cur.execute(
                    f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                    "WHERE task_id = %s "
                    "ORDER BY recorded_at ASC, id ASC LIMIT %s OFFSET %s",
                    (task_id, effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list decision records for task {task_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(row) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            task_id=task_id,
            count=len(results),
        )
        return results

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        role: DecisionRole,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records where the agent acted in the given role.

        Args:
            agent_id: Identifier of the agent whose decisions are
                being listed.
            role: Either ``"executor"`` or ``"reviewer"``; selects
                which side of the decision the agent participated on.
                Anything outside that set raises ``QueryError`` so
                callers don't accidentally widen the closed set.
            limit: Maximum number of records to return on this page;
                must be ``>= 1``. Clamped to ``_MAX_PAGE_LIMIT`` to
                prevent unbounded queries.
            offset: Number of records to skip before the page; must
                be ``>= 0``.

        Returns:
            ``tuple[DecisionRecord, ...]`` ordered by
            ``(recorded_at DESC, id DESC)`` so newest decisions come
            first. The ``id`` tiebreaker keeps the page boundary
            stable under concurrent inserts.

        Raises:
            QueryError: If ``role`` is outside the closed set, if
                ``limit`` / ``offset`` fail the type or bounds check,
                or if the underlying ``psycopg`` query raises. The
                structured ``WARNING`` is emitted before the raise.
        """
        # Runtime defense: validate role is in the closed set
        role_obj: object = role
        if not isinstance(role_obj, str):
            msg = (
                f"role must be 'executor' or 'reviewer', got {type(role_obj).__name__}"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                agent_id=agent_id,
                role_type=type(role_obj).__name__,
                error=msg,
            )
            raise QueryError(msg)
        role_str: str = role_obj
        try:
            column = _ROLE_TO_COLUMN[role_str]
        except KeyError as exc:
            msg = f"role must be 'executor' or 'reviewer', got {role_str!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                agent_id=agent_id,
                role=role_str,
                error=msg,
            )
            raise QueryError(msg) from exc
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            agent_id=agent_id,
            role=role_str,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)
        try:
            # column is a closed-set value from _ROLE_TO_COLUMN.
            # ``id DESC`` tiebreaker keeps cursor pagination stable
            # when records share a recorded_at timestamp.
            query = (
                f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                f"WHERE {column} = %s ORDER BY recorded_at DESC, id DESC "
                f"LIMIT %s OFFSET %s"
            )
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, (agent_id, effective_limit, offset))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = (
                f"Failed to list decision records for agent {agent_id!r} (role={role})"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                agent_id=agent_id,
                role=role,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(row) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            agent_id=agent_id,
            role=role,
            count=len(results),
        )
        return results

    _REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
        "id",
        "task_id",
        "approval_id",
        "executing_agent_id",
        "reviewer_agent_id",
        "decision",
        "reason",
        "recorded_at",
        "version",
    )

    @staticmethod
    def _coerce_criteria(raw_criteria: object, record_id: object) -> tuple[object, ...]:
        """Normalize a ``criteria_snapshot`` JSONB value to a tuple.

        Postgres JSONB comes back as ``list``/``dict``; the "string"
        branch is defensive against callers that migrate data from
        the SQLite backend (which stored criteria as a JSON string).

        Returns:
            The matching collection.

        Raises:
            TypeError: If an argument has the wrong type.
        """
        if isinstance(raw_criteria, str):
            decoded = json.loads(raw_criteria)
            if not isinstance(decoded, list):
                msg = (
                    f"criteria_snapshot for decision record "
                    f"{record_id!r} is not a JSON array "
                    f"(got {type(decoded).__name__})"
                )
                raise TypeError(msg)
            return tuple(decoded)
        if not isinstance(raw_criteria, list):
            msg = (
                f"criteria_snapshot for decision record {record_id!r} "
                f"is not a list (got {type(raw_criteria).__name__})"
            )
            raise TypeError(msg)
        return tuple(raw_criteria)

    def _row_to_record(self, row: dict[str, object]) -> DecisionRecord:
        """Convert a database row to a ``DecisionRecord`` model.

        JSONB columns in Postgres come back as dicts/lists, not strings.
        The ``criteria_snapshot`` is shape-checked to ensure it's a list.
        All failure modes (missing columns, malformed JSON, shape
        mismatches, Pydantic validation errors) are normalized into
        ``QueryError`` with a consistent event payload so callers get
        the same exception type regardless of the root cause.

        Returns:
            Result of type ``DecisionRecord``.

        Raises:
            QueryError: If the database query fails.
        """
        record_id = row.get("id")
        try:
            parsed: dict[str, object] = {
                col: row[col] for col in self._REQUIRED_COLUMNS
            }
            raw_criteria = row["criteria_snapshot"]
            raw_metadata = row["metadata"]
            parsed["criteria_snapshot"] = self._coerce_criteria(raw_criteria, record_id)
            parsed["metadata"] = (
                json.loads(raw_metadata)
                if isinstance(raw_metadata, str)
                else raw_metadata
            )
            return DecisionRecord.model_validate(parsed)
        except (KeyError, ValidationError, TypeError, json.JSONDecodeError) as exc:
            missing = exc.args[0] if isinstance(exc, KeyError) and exc.args else None
            msg = (
                f"Failed to deserialize decision record {record_id!r}: "
                f"{type(exc).__name__}"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED,
                record_id=record_id,
                missing_column=missing,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def purge_before(self, threshold: AwareDatetime) -> int:
        """Delete decision records older than threshold (retention).

        Args:
            threshold: Datetime; records strictly older than this are
                deleted.

        Returns:
            Number of rows removed.

        Raises:
            ValueError: If ``threshold`` is a naive datetime. Rejected
                explicitly (mirroring ``append_with_next_version``) so
                the backend never silently reinterprets it in the
                session timezone.
            QueryError: If the operation fails.
        """
        if threshold.tzinfo is None:
            msg = (
                f"threshold must be timezone-aware, got a naive datetime {threshold!r}"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                error_type="NaiveDatetimeRejected",
                error=msg,
            )
            raise ValueError(msg)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "DELETE FROM decision_records WHERE recorded_at < %s",
                    (normalize_utc(threshold),),
                )
                return cur.rowcount
        except psycopg.Error as exc:
            msg = "Failed to purge decision records"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
