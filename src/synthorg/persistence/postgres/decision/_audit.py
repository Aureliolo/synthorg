# module-kind: code
"""Append-only write path for the Postgres decision repository.

``append`` is the :class:`AppendOnlyRepository` interface (caller-set
version).  Each ``pool.connection()`` block commits on clean exit, so
no explicit ``commit`` call is needed.
"""

import copy
from datetime import datetime
from types import MappingProxyType

import psycopg

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.engine.decisions import DecisionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_DECISION_RECORD_QUERY_FAILED,
    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
)
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence.postgres.decision._base import _DecisionRepoBase
from synthorg.persistence.postgres.decision._sql import _build_insert_params

logger = get_logger(__name__)


class _AuditMixin(_DecisionRepoBase):
    """Append-only insert path for ``PostgresDecisionRepository``."""

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

    async def purge_before(self, threshold: datetime) -> int:
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
