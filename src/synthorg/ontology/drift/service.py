"""Drift detection background service."""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_DRIFT_CHECK_COMPLETED,
    ONTOLOGY_DRIFT_CHECK_STARTED,
    ONTOLOGY_DRIFT_DETECT_FAILED,
    ONTOLOGY_DRIFT_DETECTED,
    ONTOLOGY_DRIFT_ENTITY_CHECK_FAILED,
    ONTOLOGY_DRIFT_STORE_FAILED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.ontology.config import DriftDetectionConfig
    from synthorg.ontology.drift.protocol import DriftDetectionStrategy
    from synthorg.ontology.drift.store import DriftReportStore
    from synthorg.ontology.models import DriftReport
    from synthorg.ontology.protocol import OntologyBackend

logger = get_logger(__name__)


class DriftDetectionService:
    """Runs drift detection strategies and stores results.

    Provides on-demand checking for single entities or full scans.
    Background scheduling is handled by the caller (e.g. an asyncio
    periodic task in the engine).

    Args:
        strategy: Drift detection strategy implementation.
        ontology: Ontology backend for entity listing.
        config: Drift detection configuration.
        store: Optional report store for persistence.
    """

    __slots__ = ("_config", "_ontology", "_store", "_strategy")

    def __init__(
        self,
        *,
        strategy: DriftDetectionStrategy,
        ontology: OntologyBackend,
        config: DriftDetectionConfig,
        store: DriftReportStore | None = None,
    ) -> None:
        self._strategy = strategy
        self._ontology = ontology
        self._config = config
        self._store = store

    async def check_entity(
        self,
        entity_name: NotBlankStr,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> DriftReport:
        """Run drift detection for a single entity.

        Args:
            entity_name: Entity to check.
            agent_ids: Agent IDs to sample.

        Returns:
            Drift report for the entity.
        """
        logger.info(
            ONTOLOGY_DRIFT_CHECK_STARTED,
            entity_name=entity_name,
            agent_count=len(agent_ids),
        )

        try:
            report = await self._strategy.detect(entity_name, agent_ids)
        except Exception:
            logger.error(
                ONTOLOGY_DRIFT_DETECT_FAILED,
                entity_name=entity_name,
                agent_count=len(agent_ids),
                exc_info=True,
            )
            raise

        if report.divergence_score >= self._config.threshold:
            logger.warning(
                ONTOLOGY_DRIFT_DETECTED,
                entity_name=entity_name,
                divergence_score=report.divergence_score,
                recommendation=report.recommendation.value,
            )

        logger.info(
            ONTOLOGY_DRIFT_CHECK_COMPLETED,
            entity_name=entity_name,
            divergence_score=report.divergence_score,
        )

        if self._store is not None:
            try:
                await self._store.store_report(report)
            except Exception:
                logger.error(
                    ONTOLOGY_DRIFT_STORE_FAILED,
                    entity_name=entity_name,
                    divergence_score=report.divergence_score,
                    exc_info=True,
                )
                raise

        return report

    async def check_all(
        self,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> tuple[DriftReport, ...]:
        """Run drift detection for all registered entities.

        Args:
            agent_ids: Agent IDs to sample per entity.

        Returns:
            Drift reports for all entities.
        """
        import asyncio  # noqa: PLC0415

        entities = await self._ontology.list_entities()

        results = await asyncio.gather(
            *(self.check_entity(entity.name, agent_ids) for entity in entities),
            return_exceptions=True,
        )

        reports: list[DriftReport] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error(
                    ONTOLOGY_DRIFT_ENTITY_CHECK_FAILED,
                    entity_name=entities[i].name,
                    error=str(result),
                )
            else:
                reports.append(result)
        return tuple(reports)

    @property
    def threshold(self) -> float:
        """Configured drift threshold."""
        return self._config.threshold

    @property
    def strategy_name(self) -> str:
        """Name of the active detection strategy."""
        return self._strategy.strategy_name
