# module-kind: code
"""Compare-and-swap write path for the SQLite decision repository.

``append_with_next_version`` derives the next ``(task_id, version)``
atomically in SQL and reads it back under the shared ``write_context``
so concurrent review-gate decisions cannot interleave a
read-modify-write race.
"""

import copy
import sqlite3
from datetime import UTC, datetime
from types import MappingProxyType

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.decisions import DecisionOutcome, DecisionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.decision_record import (
    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
)
from synthorg.persistence.sqlite._shared import is_unique_constraint_error
from synthorg.persistence.sqlite.decision._base import _DecisionRepoBase
from synthorg.persistence.sqlite.decision._sql import (
    _INSERT_SQL,
    _build_insert_params,
    _is_structural_constraint_error,
)

logger = get_logger(__name__)


class _CasMixin(_DecisionRepoBase):
    """Atomic next-version insert path for ``SQLiteDecisionRepository``."""

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
        recorded_at: datetime,
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
                parameter is typed as ``datetime`` but Python
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
        # Reject naive datetimes explicitly.  The draft ``DecisionRecord``
        # declares ``recorded_at: AwareDatetime``, which Pydantic validates
        # on construction -- but this function takes a raw ``datetime``
        # argument, so there is no runtime tz check until then.  A naive
        # datetime passed through ``astimezone(UTC)`` would silently convert
        # assuming local time, producing a timestamp that disagrees with
        # the caller's actual wall clock.  Fail fast instead.
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
