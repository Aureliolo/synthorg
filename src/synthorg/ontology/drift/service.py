"""Drift detection background service."""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # ``exc_info=True`` would attach the full traceback to
            # the log record and bypass ``safe_error_description``,
            # reintroducing secret / PII leakage on this error path.
            logger.error(
                ONTOLOGY_DRIFT_DETECT_FAILED,
                entity_name=entity_name,
                agent_count=len(agent_ids),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        if report.divergence_score >= self._config.threshold:
            logger.warning(
                ONTOLOGY_DRIFT_DETECTED,
                entity_name=entity_name,
                divergence_score=report.divergence_score,
                recommendation=report.recommendation.value,
            )

        if self._store is not None:
            try:
                await self._store.store_report(report)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # full traceback on a persistence-error path can
                # leak backend metadata; stick to the redacted form.
                logger.error(
                    ONTOLOGY_DRIFT_STORE_FAILED,
                    entity_name=entity_name,
                    divergence_score=report.divergence_score,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

        # Emitted AFTER the optional persistence write so a storage
        # failure cannot leave the audit stream advertising a
        # completed scan that was never durably recorded.
        logger.info(
            ONTOLOGY_DRIFT_CHECK_COMPLETED,
            entity_name=entity_name,
            divergence_score=report.divergence_score,
        )

        return report

    async def check_all(
        self,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> tuple[DriftReport, ...]:
        """Run drift detection for all registered entities.

        Args:
            agent_ids: Agent IDs to sample per entity.

        Returns:
            Drift reports for all entities, in the same order as
            ``self._ontology.list_entities()`` returned them.  Entities
            whose check raised a non-fatal ``Exception`` are dropped from
            the result; fatal builtins propagate via the surrounding
            TaskGroup teardown.
        """
        entities = await self._ontology.list_entities()
        # Preallocate to keep ``index``-aligned writes deterministic;
        # without this the append order matches task completion order
        # and a flaky downstream backend can permute ``reports`` even
        # though the entity list is stable.
        slots: list[DriftReport | None] = [None] * len(entities)

        async def _check_one(index: int, entity_name: NotBlankStr) -> None:
            """Run one entity's drift check; capture non-fatal failures.

            Wrapping each ``check_entity`` invocation in this helper
            lets us re-raise fatal builtins (``MemoryError`` /
            ``RecursionError``) and ``BaseException`` non-
            ``Exception`` (cancellation / shutdown signals)
            immediately so the surrounding ``TaskGroup`` tears down
            the scan instead of buffering until every other entity
            completes -- the failure mode that
            ``asyncio.gather(return_exceptions=True)`` could not
            avoid. Ordinary ``Exception`` is logged per-entity and
            dropped so a single bad entity does not cancel the whole
            scan.
            """
            try:
                report = await self.check_entity(entity_name, agent_ids)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.error(
                    ONTOLOGY_DRIFT_ENTITY_CHECK_FAILED,
                    entity_name=entity_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return
            slots[index] = report

        async with asyncio.TaskGroup() as tg:
            for index, entity in enumerate(entities):
                tg.create_task(_check_one(index, entity.name))
        return tuple(report for report in slots if report is not None)

    @property
    def threshold(self) -> float:
        """Configured drift threshold."""
        return self._config.threshold

    @property
    def strategy_name(self) -> str:
        """Name of the active detection strategy."""
        return self._strategy.strategy_name
