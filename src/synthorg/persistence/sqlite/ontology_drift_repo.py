"""SQLite-backed drift report repository."""

import contextlib
import json
import sqlite3
from datetime import datetime
from typing import Final

import aiosqlite

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.ontology import (
    ONTOLOGY_DRIFT_STORE_DESERIALIZE_FAILED,
    ONTOLOGY_DRIFT_STORE_FAILED,
    ONTOLOGY_DRIFT_STORE_WRITE_FAILED,
)
from synthorg.ontology.models import AgentDrift, DriftAction, DriftReport
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.ontology_protocol import DriftReportFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_DEFAULT_LIST_LIMIT_10: Final[int] = 10


def _row_to_report(row: aiosqlite.Row) -> DriftReport:
    """Deserialize a row into a DriftReport.

    Returns:
        Result of type ``DriftReport``.

    Raises:
        ValueError: If an argument fails validation.
    """
    entity_name, divergence_score, canonical_version, rec, agents_json = row
    try:
        agents_data = json.loads(str(agents_json))
        agents = tuple(
            AgentDrift(
                agent_id=a["agent_id"],
                divergence_score=a["divergence_score"],
                details=a.get("details", ""),
            )
            for a in agents_data
        )
        return DriftReport(
            entity_name=str(entity_name),
            divergence_score=float(divergence_score),
            canonical_version=int(canonical_version),
            recommendation=DriftAction(str(rec)),
            divergent_agents=agents,
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        log_exception_redacted(
            logger,
            ONTOLOGY_DRIFT_STORE_DESERIALIZE_FAILED,
            exc,
            entity_name=str(entity_name),
        )
        msg = f"Malformed drift report row for entity {entity_name!r}"
        raise ValueError(msg) from exc


class SQLiteOntologyDriftReportRepository:
    """SQLite implementation of ``OntologyDriftReportRepository``."""

    __slots__ = ("_db", "_write_context")

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def append(self, event: DriftReport) -> None:
        """Append one drift report (write-only; immutable once written)."""
        agents_json = json.dumps(
            [
                {
                    "agent_id": a.agent_id,
                    "divergence_score": a.divergence_score,
                    "details": a.details,
                }
                for a in event.divergent_agents
            ],
        )
        async with self._write_context():
            try:
                await self._db.execute(
                    "INSERT INTO drift_reports "
                    "(entity_name, divergence_score, canonical_version, "
                    "recommendation, divergent_agents) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event.entity_name,
                        event.divergence_score,
                        event.canonical_version,
                        event.recommendation.value,
                        agents_json,
                    ),
                )
                await self._db.commit()
            except Exception:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.error(
                    ONTOLOGY_DRIFT_STORE_WRITE_FAILED,
                    entity_name=event.entity_name,
                )
                raise

    async def query(
        self,
        filter_spec: DriftReportFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DriftReport, ...]:
        """Return drift reports newest-first, paginated.

        ``DriftReportFilterSpec`` is currently an empty placeholder, so
        every report is in scope; ordering follows the append-only
        contract (descending ``id``, which is monotonic with insertion).

        Args:
            filter_spec: Filter specification (no fields yet).
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Drift reports in descending insertion order.
        """
        _ = filter_spec
        limit = validate_pagination_args(
            limit, offset, event=ONTOLOGY_DRIFT_STORE_FAILED
        )
        async with self._db.execute(
            "SELECT entity_name, divergence_score, canonical_version, "
            "recommendation, divergent_agents "
            "FROM drift_reports "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple(_row_to_report(row) for row in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete drift reports created before ``threshold`` (retention).

        ``threshold`` must be timezone-aware: a naive value compared
        against the UTC-stored ``created_at`` would silently delete the
        wrong window. The stored column uses the DB ``STRFTIME`` ``Z``
        format, so the offset-formatted threshold is re-formatted via
        SQLite ``strftime`` to share the column's exact representation
        before the lexicographic comparison.

        Args:
            threshold: Tz-aware cutoff; rows strictly older are removed.

        Returns:
            Number of rows removed.

        Raises:
            ValueError: If ``threshold`` is naive.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            raise ValueError(msg)
        cutoff = format_iso_utc(threshold)
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM drift_reports "
                    "WHERE created_at < STRFTIME('%Y-%m-%dT%H:%M:%fZ', ?)",
                    (cutoff,),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except Exception:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.error(
                    ONTOLOGY_DRIFT_STORE_WRITE_FAILED,
                    entity_name="<purge_before>",
                )
                raise
        return removed

    async def get_latest(
        self,
        entity_name: NotBlankStr,
        *,
        limit: int = _DEFAULT_LIST_LIMIT_10,
    ) -> tuple[DriftReport, ...]:
        """Return most recent drift reports for an entity.

        Returns:
            Tuple of matching rows; empty when no rows match.
        """
        async with self._db.execute(
            "SELECT entity_name, divergence_score, canonical_version, "
            "recommendation, divergent_agents "
            "FROM drift_reports "
            "WHERE entity_name = ? "
            "ORDER BY id DESC LIMIT ?",
            (entity_name, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple(_row_to_report(row) for row in rows)

    async def get_all_latest(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[DriftReport, ...]:
        """Return the latest drift report for each entity.

        Returns:
            Tuple of matching rows; empty when no rows match.
        """
        async with self._db.execute(
            "SELECT entity_name, divergence_score, canonical_version, "
            "recommendation, divergent_agents "
            "FROM drift_reports dr "
            "WHERE id = ("
            "  SELECT MAX(id) FROM drift_reports "
            "  WHERE entity_name = dr.entity_name"
            ") "
            "ORDER BY divergence_score DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple(_row_to_report(row) for row in rows)
