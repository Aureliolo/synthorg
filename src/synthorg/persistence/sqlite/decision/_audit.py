# module-kind: code
"""Append-only write path for the SQLite decision repository.

``append`` is the :class:`AppendOnlyRepository` interface: it persists
a record with a caller-supplied ``version`` (most callers use
``append_with_next_version`` from the CAS path instead).
"""

import copy
import sqlite3
from datetime import datetime
from types import MappingProxyType

import aiosqlite

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.engine.decisions import DecisionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.decision_record import (
    PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
)
from synthorg.persistence._shared import format_iso_utc
from synthorg.persistence.sqlite._shared import is_unique_constraint_error
from synthorg.persistence.sqlite.decision._base import _DecisionRepoBase
from synthorg.persistence.sqlite.decision._sql import (
    _build_insert_params,
    _is_structural_constraint_error,
)

logger = get_logger(__name__)


class _AuditMixin(_DecisionRepoBase):
    """Append-only insert path for ``SQLiteDecisionRepository``."""

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
                record_id=str(event.id),
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
                record_id=str(event.id),
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
                msg = f"Duplicate decision record {str(event.id)!r}"
                logger.warning(
                    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                    record_id=str(event.id),
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
                    record_id=str(event.id),
                    error_type=type(exc).__name__,
                    violation_category="StructuralConstraintViolation",
                    error=safe_error_description(exc),
                    sqlite_errorname=exc.sqlite_errorname,
                )
                raise
            msg = f"Failed to append decision record {str(event.id)!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=str(event.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                sqlite_errorname=getattr(exc, "sqlite_errorname", None),
            )
            raise QueryError(msg) from exc
        except (sqlite3.Error, aiosqlite.Error) as exc:
            await self._rollback_quietly()
            msg = f"Failed to append decision record {str(event.id)!r}"
            logger.warning(
                PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
                record_id=str(event.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def purge_before(self, threshold: datetime) -> int:
        """Delete decision records older than threshold (retention).

        Args:
            threshold: Datetime; records strictly older than this are
                deleted.

        Returns:
            Number of rows removed.

        Raises:
            ValueError: If ``threshold`` is a naive datetime. Rejected
                explicitly (mirroring the Postgres backend) so the two
                arms reject naive input identically rather than relying on
                ``format_iso_utc`` alone.
            QueryError: If the operation fails.
        """
        if threshold.tzinfo is None:
            # Caller-input precondition: raise directly without a warning
            # so this naive-datetime guard does not pollute persistence
            # failure telemetry.
            msg = (
                f"threshold must be timezone-aware, got a naive datetime {threshold!r}"
            )
            raise ValueError(msg)
        try:
            async with (
                self._write_context(),
                self._db.execute(
                    "DELETE FROM decision_records WHERE recorded_at < ?",
                    (format_iso_utc(threshold),),
                ) as cursor,
            ):
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
