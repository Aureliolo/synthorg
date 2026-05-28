"""SQLite repository implementation for decision records.

Append-only: records can be appended and queried but never updated or
deleted, preserving audit integrity.  Version numbers for
``(task_id, version)`` are computed atomically in SQL via a subquery
to eliminate the TOCTOU race that a read-then-write pattern would
create under concurrent review gate decisions.
"""

import copy
import json
import sqlite3
from datetime import UTC
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import aiosqlite
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
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence.decision_protocol import (
    DecisionFilterSpec,
    DecisionRole,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

if TYPE_CHECKING:
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
    :id, :task_id, :approval_id, :executing_agent_id, :reviewer_agent_id,
    :decision, :reason, :criteria_snapshot, :recorded_at,
    (SELECT COALESCE(MAX(version), 0) + 1
       FROM decision_records WHERE task_id = :task_id),
    :metadata
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

    Normalizes ``recorded_at`` to UTC (ISO 8601 with ``+00:00`` offset)
    so lexicographic ordering of the ``recorded_at`` column is
    equivalent to chronological ordering across mixed-timezone callers.

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
        "criteria_snapshot": json.dumps(list(criteria_snapshot)),
        "recorded_at": recorded_at.astimezone(UTC).isoformat(),
        # ``metadata`` may contain ``MappingProxyType`` (from the draft
        # record's frozen view) at arbitrary nesting depth; unwrap
        # recursively so ``json.dumps`` only sees plain dicts and
        # lists.
        "metadata": json.dumps(_unfreeze_for_json(metadata)),
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


def _is_structural_constraint_error(exc: sqlite3.IntegrityError) -> bool:
    """Return True for CHECK / FOREIGN KEY / NOT NULL constraint violations.

    These represent schema-level invariants that the application
    relies on (e.g. ``reviewer_agent_id != executing_agent_id``).
    Masking them as generic ``QueryError`` would hide programming
    errors or schema drift; letting the original
    ``sqlite3.IntegrityError`` propagate keeps the structural
    failure visible to operators and to the review-gate service's
    narrowed ``except (QueryError, DuplicateRecordError)`` catch.

    Returns:
        ``True`` for CHECK / FOREIGN KEY / NOT NULL violations, ``False`` otherwise.
    """
    return exc.sqlite_errorname in {
        "SQLITE_CONSTRAINT_CHECK",
        "SQLITE_CONSTRAINT_FOREIGNKEY",
        "SQLITE_CONSTRAINT_NOTNULL",
        "SQLITE_CONSTRAINT_TRIGGER",
    }


class SQLiteDecisionRepository:
    """SQLite implementation of the ``DecisionRepository`` protocol.

    Append-only: decision records are immutable audit entries of
    review gate decisions.  Timestamps are normalized to UTC before
    storage for consistent lexicographic ordering.

    The backend's ``write_context`` serializes the multi-statement
    INSERT -> SELECT -> commit/rollback sequence in
    ``append_with_next_version`` so concurrent coroutines cannot
    interleave their statements or have one coroutine's rollback
    wipe another's in-flight INSERT.  Production callers receive the
    shared backend write context so this repository coordinates with
    OTHER repositories that mutate the same underlying
    ``aiosqlite.Connection``; tests can pass
    ``tests._shared.persistence.make_private_write_context()`` for
    standalone construction.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes
            multi-statement transactions on ``db``.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

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
        the ``INSERT`` statement itself.  That single statement is
        atomic under aiosqlite's per-statement serialization, and the
        ``UNIQUE(task_id, version)`` constraint rejects any race that
        somehow produces a duplicate -- surfaced as
        ``DuplicateRecordError``.  This matches the connection-level
        implicit transaction semantics used by every other SQLite repo
        in this backend (no explicit ``BEGIN``).

        See the ``DecisionRepository`` protocol for the full argument
        descriptions.  ``recorded_at`` is normalized to UTC before
        storage; records read back via ``get`` / ``list_by_task`` /
        ``list_by_agent`` will therefore always have UTC timestamps.
        ``metadata`` defaults to ``{}`` so callers that do not attach
        metadata do not have to pass an empty dict.

        Raises:
            DuplicateRecordError: If a record with ``record_id`` exists
                OR a concurrent write won the ``UNIQUE(task_id, version)``
                race.
            ValueError: If ``recorded_at`` is a naive datetime (no
                tzinfo).  Rejected before any SQL runs; the
                parameter is typed as ``AwareDatetime`` but Python
                does not enforce type hints at the function
                boundary, so we guard explicitly to prevent silent
                wall-clock drift from ``astimezone(UTC)``'s
                assume-local behavior.
            ValidationError: If the model-level normalization (blank
                ``reason`` -> ``None``, duplicate ``criteria_snapshot``,
                blank ``NotBlankStr`` inputs, non-UTC ``recorded_at``)
                rejects the input.  Validation runs BEFORE the insert
                so invalid data never reaches the durable log.  We
                deliberately do NOT wrap ``ValidationError`` as
                ``QueryError`` -- malformed inputs are programming
                errors / schema drift that must surface loudly rather
                than being masked as a transient persistence failure
                the review-gate service's narrowed except would
                silently swallow.
            QueryError: If the SQL operation fails (connection dropped,
                schema mismatch, rollback failure, etc.).
            TypeError: If an argument has the wrong type.

        Returns:
            Result of type ``DecisionRecord``.
        """
        # Deep-copy the metadata up-front so nested dicts/lists the
        # caller retains are never aliased by the stored record.  The
        # Pydantic field validator on ``DecisionRecord.metadata``
        # already runs ``deep_copy_mapping`` + ``_freeze_recursive``,
        # so this is belt-and-suspenders -- but making the deep copy
        # explicit at the repository boundary keeps the intent
        # visible at the call site for future maintainers.
        metadata_view: MappingProxyType[str, object] = MappingProxyType(
            copy.deepcopy(dict(metadata or {}))
        )
        # Reject naive datetimes explicitly.  The parameter type is
        # ``AwareDatetime``, which Pydantic validates at model
        # boundaries -- but this function accepts it as a raw
        # argument, so there's no runtime enforcement until the
        # draft ``DecisionRecord`` is constructed.  A naive datetime
        # passed through ``astimezone(UTC)`` would silently convert
        # assuming local time, producing a timestamp that disagrees
        # with the caller's actual wall clock.  Fail fast instead.
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
        # Normalize recorded_at to UTC up-front so the draft record,
        # the INSERT parameters, and any subsequent read-back through
        # ``get``/``list_by_task``/``list_by_agent`` all carry the same
        # timestamp.
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
            # Log contextual detail for operators, then re-raise the
            # original ValidationError.  Wrapping as QueryError would
            # let the review-gate service's narrowed
            # ``except (QueryError, DuplicateRecordError)`` catch
            # schema drift and treat it as silent audit loss.
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
            # ``_build_insert_params`` calls ``json.dumps`` on metadata;
            # non-JSON-serializable values (datetime objects, custom
            # classes, etc.) surface as ``TypeError`` before any SQL
            # runs.  Re-raise so the programming error propagates
            # loudly instead of being masked as a silent persistence
            # failure by callers that only catch ``QueryError``.
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
        async with self._write_context():
            assigned_version = await self._execute_insert(record_id, params)
        return draft_record.model_copy(update={"version": assigned_version})

    async def _execute_insert(
        self,
        record_id: NotBlankStr,
        params: dict[str, object],
    ) -> int:
        """Insert the record and return the server-assigned version.

        Keeps ``append_with_next_version`` under the 50-line budget and
        centralizes the error-mapping / rollback logic for the write
        path.  Commit is delayed until AFTER the read-back guard
        succeeds so a defective fetchone() result never leaves a
        durable "ghost" row behind.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
            DuplicateRecordError: If a row with the same key already exists.
            IntegrityError: If a database integrity constraint is violated.
        """
        try:
            await self._db.execute(_INSERT_SQL, params)
            cursor = await self._db.execute(
                "SELECT version FROM decision_records WHERE id = :id",
                {"id": record_id},
            )
            row = await cursor.fetchone()
        except sqlite3.IntegrityError as exc:
            await self._rollback_quietly()
            if is_unique_constraint_error(exc):
                msg = f"Duplicate decision record {record_id!r}"
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=record_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    sqlite_errorname=exc.sqlite_errorname,
                )
                raise DuplicateRecordError(msg) from exc
            if _is_structural_constraint_error(exc):
                # CHECK / FOREIGN KEY / NOT NULL / trigger violations
                # are schema-level programming errors -- log with full
                # context and re-raise the original IntegrityError so
                # callers see the structural failure rather than a
                # generic QueryError that could be mistaken for a
                # transient persistence hiccup.
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=record_id,
                    error_type=type(exc).__name__,
                    violation_category="StructuralConstraintViolation",
                    error=safe_error_description(exc),
                    sqlite_errorname=exc.sqlite_errorname,
                )
                raise
            msg = f"Failed to save decision record {record_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                sqlite_errorname=exc.sqlite_errorname,
            )
            raise QueryError(msg) from exc
        except (sqlite3.Error, aiosqlite.Error) as exc:
            await self._rollback_quietly()
            msg = f"Failed to save decision record {record_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            # Defensive: fetchone can return None under aiosqlite's
            # type signature even though a successful INSERT + SELECT
            # of the same id should always find the row.  Roll back
            # the uncommitted INSERT so no ghost row survives, then
            # surface the anomaly loudly rather than silently
            # swallowing it.
            await self._rollback_quietly()
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
        # Only commit once the read-back guard succeeds; a failed
        # guard would otherwise leave a durable record with no
        # corresponding service-layer caller signal.
        try:
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            await self._rollback_quietly()
            msg = f"Failed to commit decision record {record_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row["version"])

    async def _rollback_quietly(self) -> None:
        """Roll back the current transaction, swallowing rollback errors.

        If the rollback itself fails (e.g. connection dropped), we log
        the secondary failure but do not shadow the caller's original
        exception -- that's the one the caller needs to see.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                stage="rollback",
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
            )

    async def append(self, event: DecisionRecord) -> None:
        """Append a decision record with a precomputed version.

        This method is the append interface from
        :class:`AppendOnlyRepository`; most callers use
        ``append_with_next_version`` instead. Version must be set
        by the caller.

        Raises:
            QueryError: If the database query fails.
            DuplicateRecordError: If a row with the same key already exists.
            TypeError: If an argument has the wrong type.
            IntegrityError: If a database integrity constraint is violated.
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
            async with self._write_context():
                await self._db.execute(
                    """\
                    INSERT INTO decision_records (
                        id, task_id, approval_id, executing_agent_id,
                        reviewer_agent_id, decision, reason,
                        criteria_snapshot, recorded_at, version, metadata
                    ) VALUES (
                        :id, :task_id, :approval_id, :executing_agent_id,
                        :reviewer_agent_id, :decision, :reason,
                        :criteria_snapshot, :recorded_at, :version, :metadata
                    )""",
                    {**params, "version": event.version},
                )
                await self._db.commit()
        except sqlite3.IntegrityError as exc:
            await self._rollback_quietly()
            if is_unique_constraint_error(exc):
                msg = f"Duplicate decision record {event.id!r}"
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=event.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    sqlite_errorname=exc.sqlite_errorname,
                )
                raise DuplicateRecordError(msg) from exc
            if _is_structural_constraint_error(exc):
                # CHECK / FOREIGN KEY / NOT NULL / trigger violations
                # are schema-level programming errors -- log with full
                # context and re-raise the original IntegrityError so
                # callers see the structural failure rather than a
                # generic QueryError that could be mistaken for a
                # transient persistence hiccup.
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=event.id,
                    error_type=type(exc).__name__,
                    violation_category="StructuralConstraintViolation",
                    error=safe_error_description(exc),
                    sqlite_errorname=exc.sqlite_errorname,
                )
                raise
            msg = f"Failed to append decision record {event.id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=event.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                sqlite_errorname=getattr(exc, "sqlite_errorname", None),
            )
            raise QueryError(msg) from exc
        except (sqlite3.Error, aiosqlite.Error) as exc:
            await self._rollback_quietly()
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
        params: list[object] = []

        if task_id_filter is not None:
            where_clauses.append("task_id = ?")
            params.append(task_id_filter)

        if agent_id_filter is not None and role_filter is not None:
            if role_filter == "executor":
                where_clauses.append("executing_agent_id = ?")
            else:
                where_clauses.append("reviewer_agent_id = ?")
            params.append(agent_id_filter)
        elif agent_id_filter is not None:
            where_clauses.append("(executing_agent_id = ? OR reviewer_agent_id = ?)")
            params.extend((agent_id_filter, agent_id_filter))

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        if task_id_filter is not None:
            order_by = "recorded_at ASC, id ASC"
        else:
            order_by = "recorded_at DESC, id DESC"

        try:
            async with self._write_context():
                cursor = await self._db.execute(
                    f"""\
                    SELECT {_COLS} FROM decision_records
                    WHERE {where_clause}
                    ORDER BY {order_by}
                    LIMIT ? OFFSET ?""",  # noqa: S608
                    (*params, effective_limit, offset),
                )
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        Serialized against concurrent writers via ``write_context`` so
        reads never observe rows from an in-flight ``INSERT -> SELECT
        -> commit`` sequence that has not yet committed.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._write_context():
                cursor = await self._db.execute(
                    f"SELECT {_COLS} FROM decision_records WHERE id = ?",  # noqa: S608
                    (record_id,),
                )
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        return self._row_to_record(dict(row))

    async def list_by_task(
        self,
        task_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        """List decision records for a task, oldest first.

        Serialized against concurrent writers via ``write_context`` so
        reads never observe phantom rows from a mid-transaction
        ``append_with_next_version``.

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
            ``(recorded_at, id)`` so a backfilled decision still
            sorts to its true chronological position; the ``id``
            tiebreaker matches the Postgres backend.

        Raises:
            QueryError: If ``limit`` / ``offset`` fail the type or
                bounds check, or if the underlying SQLite query
                raises. The structured ``WARNING`` is emitted before
                the raise.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
            task_id=task_id,
        )
        effective_limit = min(limit, _MAX_PAGE_LIMIT)
        try:
            async with self._write_context():
                # ``recorded_at ASC, id ASC`` matches the protocol's
                # "oldest first" contract; ``version ASC`` would
                # mis-place a backfilled decision (low ``recorded_at``
                # but a freshly-allocated high ``version``) at the end
                # of the list. Mirrors the Postgres backend.
                cursor = await self._db.execute(
                    f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                    "WHERE task_id = ? "
                    "ORDER BY recorded_at ASC, id ASC LIMIT ? OFFSET ?",
                    (task_id, effective_limit, offset),
                )
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list decision records for task {task_id!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(dict(row)) for row in rows)
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

        ``role`` is validated via ``Literal`` at the type level, but we
        re-check at runtime to guard against bad callers that bypass
        type checking.  A rejected role is logged before raising.
        Serialized against concurrent writers via ``write_context``.

        Args:
            agent_id: Identifier of the agent whose decisions are
                being listed.
            role: Either ``"executor"`` or ``"reviewer"``; selects
                which side of the decision the agent participated on.
                Anything outside that set raises ``QueryError``.
            limit: Maximum number of records to return on this page;
                must be ``>= 1``. Clamped to ``_MAX_PAGE_LIMIT`` to
                prevent unbounded queries.
            offset: Number of records to skip before the page; must
                be ``>= 0``.

        Returns:
            ``tuple[DecisionRecord, ...]`` ordered by
            ``(recorded_at DESC, id DESC)`` so newest decisions come
            first. The ``id`` tiebreaker matches the Postgres
            backend and keeps page boundaries stable under
            concurrent inserts.

        Raises:
            QueryError: If ``role`` is outside the closed set, if
                ``limit`` / ``offset`` fail the type or bounds check,
                or if the underlying SQLite query raises.
        """
        # Runtime defense in depth: the Literal prevents type-safe
        # callers from passing bad values, but untyped callers can
        # still pass anything.  Check the input TYPE first so a
        # list/dict/None argument raises ``ValueError`` with the
        # same message shape as an unknown-string role, instead of
        # a surprising ``TypeError`` (unhashable) inside the dict
        # lookup.  Using a dict lookup instead of if/elif keeps the
        # column name derivation closed over a bounded set of
        # hard-coded identifiers (see the closed-set comment on
        # the SQL query below).  mypy narrows ``role`` to
        # ``Literal[...]`` and treats this branch as unreachable,
        # which is exactly the static case -- but runtime callers
        # can still defeat the Literal.
        # Cast to ``object`` so mypy doesn't narrow to ``Literal``
        # and mark the untyped-caller defense as unreachable.
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
            # column is a closed-set value from _ROLE_TO_COLUMN, never
            # user-supplied; agent_id flows through the positional
            # placeholder. ``id`` is added to the ORDER BY tiebreaker
            # so cursor pagination stays deterministic when two records
            # share a ``recorded_at`` timestamp.
            query = (
                f"SELECT {_COLS} FROM decision_records "  # noqa: S608
                f"WHERE {column} = ? ORDER BY recorded_at DESC, id DESC "
                f"LIMIT ? OFFSET ?"
            )
            async with self._write_context():
                cursor = await self._db.execute(
                    query,
                    (agent_id, effective_limit, offset),
                )
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        results = tuple(self._row_to_record(dict(row)) for row in rows)
        logger.debug(
            PERSISTENCE_DECISION_RECORD_QUERIED,
            agent_id=agent_id,
            role=role,
            count=len(results),
        )
        return results

    def _row_to_record(self, row: dict[str, object]) -> DecisionRecord:
        """Convert a database row to a ``DecisionRecord`` model.

        Every required column is read via explicit ``row["col"]``
        indexing so a missing column (schema drift) surfaces as
        ``KeyError`` with the specific column name logged via
        ``PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED`` before the
        exception re-raises.  Building ``parsed`` via ``dict(row)``
        would silently copy whatever's present and defer the failure
        to ``DecisionRecord.model_validate`` with a less informative
        ``ValidationError``, so we assemble it field-by-field
        instead.

        The JSON-encoded ``criteria_snapshot`` column is shape-checked
        after deserialization: a row that somehow stores a non-array
        (e.g. a bare string or object, from a migration bug or a
        third-party backend) is rejected with ``QueryError`` rather
        than being silently coerced via ``tuple(...)`` which would
        iterate over the object's keys / string characters and
        produce garbage data.

        Returns:
            The reconstructed ``DecisionRecord``.

        Raises:
            QueryError: If row deserialization or validation fails.
            KeyError: If a required dictionary key is missing.
        """  # noqa: DOC501 -- TypeError is caught locally and surfaces as QueryError
        try:
            try:
                # Explicit reads for every required column.  Any
                # missing key raises KeyError and hits the log-and-
                # re-raise handler below.
                parsed: dict[str, object] = {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "approval_id": row["approval_id"],
                    "executing_agent_id": row["executing_agent_id"],
                    "reviewer_agent_id": row["reviewer_agent_id"],
                    "decision": row["decision"],
                    "reason": row["reason"],
                    "recorded_at": row["recorded_at"],
                    "version": row["version"],
                }
                raw_criteria = row["criteria_snapshot"]
                raw_metadata = row["metadata"]
            except KeyError as exc:
                missing = exc.args[0] if exc.args else None
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED,
                    record_id=row.get("id"),
                    missing_column=missing,
                    error_type="KeyError",
                    error=safe_error_description(exc),
                )
                raise
            if isinstance(raw_criteria, str):
                decoded_criteria = json.loads(raw_criteria)
                if not isinstance(decoded_criteria, list):
                    msg = (
                        f"criteria_snapshot for decision record "
                        f"{row.get('id')!r} is not a JSON array "
                        f"(got {type(decoded_criteria).__name__})"
                    )
                    raise TypeError(msg)  # noqa: TRY301
                parsed["criteria_snapshot"] = tuple(decoded_criteria)
            else:
                parsed["criteria_snapshot"] = raw_criteria
            if isinstance(raw_metadata, str):
                parsed["metadata"] = json.loads(raw_metadata)
            else:
                parsed["metadata"] = raw_metadata
            return DecisionRecord.model_validate(parsed)
        except (ValidationError, json.JSONDecodeError, TypeError) as exc:
            msg = (
                f"Failed to deserialize decision record {row.get('id')!r}: "
                f"{type(exc).__name__}"
            )
            logger.warning(
                PERSISTENCE_DECISION_RECORD_DESERIALIZE_FAILED,
                record_id=row.get("id"),
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
            QueryError: If the operation fails.
        """
        try:
            async with self._write_context():
                cursor = await self._db.execute(
                    "DELETE FROM decision_records WHERE recorded_at < ?",
                    (format_iso_utc(threshold),),
                )
                await self._db.commit()
                return cursor.rowcount
        except (sqlite3.Error, aiosqlite.Error) as exc:
            await self._rollback_quietly()
            msg = "Failed to purge decision records"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
