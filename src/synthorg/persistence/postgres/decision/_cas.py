# module-kind: code
"""Compare-and-swap write path for the Postgres decision repository.

Under READ COMMITTED (psycopg's default), the
``SELECT COALESCE(MAX(version), 0) + 1`` subquery inside ``_INSERT_SQL``
is NOT atomic against concurrent writers on the same ``task_id``.  Two
writers can compute the same next version; the
``UNIQUE(task_id, version)`` constraint forces exactly one to succeed
and the loser retries with a freshly computed version.
"""

import copy
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import psycopg
from pydantic import ValidationError

from synthorg.core.enums import DecisionOutcome
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.engine.decisions import DecisionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.decision_record import (
    PERSISTENCE_DECISION_RECORD_SAVE_FAILED,
)
from synthorg.persistence.postgres.decision._base import _DecisionRepoBase
from synthorg.persistence.postgres.decision._sql import (
    _INSERT_SQL,
    _build_insert_params,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class _CasMixin(_DecisionRepoBase):
    """Atomic next-version insert path for ``PostgresDecisionRepository``."""

    #: Maximum attempts to retry a version-race UniqueViolation before
    #: giving up and treating the failure as a genuine duplicate record
    #: id.  Picked to comfortably exceed contention between concurrent
    #: review gates on the same task without allowing runaway retries
    #: under pathological load.
    _MAX_VERSION_RACE_ATTEMPTS: Final[int] = 5

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
