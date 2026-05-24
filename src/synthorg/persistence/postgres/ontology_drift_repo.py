"""Postgres-backed drift report repository."""

import json
from typing import TYPE_CHECKING, Any, Final

from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.ontology import (
    ONTOLOGY_DRIFT_STORE_DESERIALIZE_FAILED,
    ONTOLOGY_DRIFT_STORE_WRITE_FAILED,
)
from synthorg.ontology.models import AgentDrift, DriftAction, DriftReport
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.ontology_protocol import DriftReportFilterSpec


def _import_dict_row() -> Any:
    """Lazily resolve ``psycopg.rows.dict_row``.

    Returns:
        Result of type ``Any``.
    """
    from psycopg.rows import dict_row  # noqa: PLC0415

    return dict_row


logger = get_logger(__name__)

_DEFAULT_LIST_LIMIT_10: Final[int] = 10


def _row_to_report(row: dict[str, Any]) -> DriftReport:
    """Deserialize a dict row into a DriftReport.

    Returns:
        Result of type ``DriftReport``.

    Raises:
        ValueError: If an argument fails validation.
    """
    try:
        agents_raw = row["divergent_agents"]
        agents_data = (
            json.loads(agents_raw) if isinstance(agents_raw, str) else agents_raw
        )
        agents = tuple(
            AgentDrift(
                agent_id=a["agent_id"],
                divergence_score=a["divergence_score"],
                details=a.get("details", ""),
            )
            for a in agents_data
        )
        return DriftReport(
            entity_name=str(row["entity_name"]),
            divergence_score=float(row["divergence_score"]),
            canonical_version=int(row["canonical_version"]),
            recommendation=DriftAction(str(row["recommendation"])),
            divergent_agents=agents,
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        log_exception_redacted(
            logger,
            ONTOLOGY_DRIFT_STORE_DESERIALIZE_FAILED,
            exc,
            entity_name=str(row.get("entity_name")),
        )
        msg = f"Malformed drift report row for entity {row.get('entity_name')!r}"
        raise ValueError(msg) from exc


class PostgresOntologyDriftReportRepository:
    """Postgres implementation of ``OntologyDriftReportRepository``."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._dict_row = _import_dict_row()

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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO drift_reports "
                    "(entity_name, divergence_score, canonical_version, "
                    "recommendation, divergent_agents) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        event.entity_name,
                        event.divergence_score,
                        event.canonical_version,
                        event.recommendation.value,
                        agents_json,
                    ),
                )
        except Exception:
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
        """Filtered drift-report query (not implemented).

        Drift consumers use :meth:`get_latest` / :meth:`get_all_latest`;
        the generic filtered ``query`` surface is unimplemented and
        raises rather than silently returning an empty tuple, which
        would mask the missing functionality from a caller.

        Raises:
            NotImplementedError: If the underlying call raises.
        """
        msg = "OntologyDriftReportRepository.query is not implemented"
        raise NotImplementedError(msg)

    async def purge_before(self, threshold: Any) -> int:
        """Retention purge of drift reports (not implemented).

        Raises rather than silently reporting zero deletions, which
        would let a retention caller believe a sweep ran.

        Raises:
            NotImplementedError: If the underlying call raises.
        """
        msg = "OntologyDriftReportRepository.purge_before is not implemented"
        raise NotImplementedError(msg)

    async def get_latest(
        self,
        entity_name: NotBlankStr,
        *,
        limit: int = _DEFAULT_LIST_LIMIT_10,
    ) -> tuple[DriftReport, ...]:
        """Return most recent drift reports for an entity.

        Ordered by ``created_at DESC`` so the result uses the
        ``(entity_name, created_at DESC)`` index rather than a table
        scan on ``id``.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        dict_row = self._dict_row
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT entity_name, divergence_score, canonical_version, "
                "recommendation, divergent_agents "
                "FROM drift_reports "
                "WHERE entity_name = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (entity_name, limit),
            )
            rows = await cur.fetchall()
        return tuple(_row_to_report(row) for row in rows)

    async def get_all_latest(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[DriftReport, ...]:
        """Return the latest drift report for each entity.

        Uses ``DISTINCT ON (entity_name)`` against the
        ``(entity_name, created_at DESC)`` index so the per-entity
        latest pick is O(#entities) rather than the previous
        correlated ``MAX(id)`` subquery (O(n log n)).

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        dict_row = self._dict_row
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "WITH latest_per_entity AS ("
                "  SELECT DISTINCT ON (entity_name) "
                "    entity_name, divergence_score, canonical_version, "
                "    recommendation, divergent_agents "
                "  FROM drift_reports "
                "  ORDER BY entity_name, created_at DESC"
                ") "
                "SELECT entity_name, divergence_score, canonical_version, "
                "       recommendation, divergent_agents "
                "FROM latest_per_entity "
                "ORDER BY divergence_score DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return tuple(_row_to_report(row) for row in rows)
