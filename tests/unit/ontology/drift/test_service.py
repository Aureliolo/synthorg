"""Tests for DriftDetectionService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.ontology.config import DriftDetectionConfig
from synthorg.ontology.drift.noop import NoDriftDetection
from synthorg.ontology.drift.protocol import DriftDetectionStrategy
from synthorg.ontology.drift.service import DriftDetectionService
from synthorg.ontology.models import (
    DriftAction,
    DriftReport,
    EntityDefinition,
    EntitySource,
    EntityTier,
)
from synthorg.persistence.ontology_protocol import OntologyDriftReportRepository
from tests._shared import mock_of

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_entity(name: str) -> EntityDefinition:
    return EntityDefinition(
        name=name,
        tier=EntityTier.CORE,
        source=EntitySource.AUTO,
        definition="test",
        created_by="system",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_ontology(
    entities: tuple[EntityDefinition, ...] = (),
) -> AsyncMock:
    backend = AsyncMock()
    backend.list_entities = AsyncMock(return_value=entities)
    backend.get_version_manifest = AsyncMock(
        return_value={e.name: 1 for e in entities},
    )
    return backend


@pytest.mark.unit
class TestDriftDetectionService:
    """Tests for DriftDetectionService."""

    async def test_check_entity_returns_report(self) -> None:
        """check_entity returns a DriftReport."""
        ontology = _make_ontology()
        config = DriftDetectionConfig()
        service = DriftDetectionService(
            strategy=NoDriftDetection(),
            ontology=ontology,
            config=config,
        )

        report = await service.check_entity("Task", ("agent-1",))
        assert isinstance(report, DriftReport)
        assert report.entity_name == "Task"
        assert report.divergence_score == 0.0

    async def test_check_entity_stores_report(self) -> None:
        """check_entity persists report when store is provided."""
        ontology = _make_ontology()
        config = DriftDetectionConfig()
        store = mock_of[OntologyDriftReportRepository](
            append=AsyncMock(),
        )

        service = DriftDetectionService(
            strategy=NoDriftDetection(),
            ontology=ontology,
            config=config,
            store=store,
        )

        await service.check_entity("Task", ("agent-1",))
        store.append.assert_awaited_once()

    async def test_check_all_iterates_entities(self) -> None:
        """check_all runs detection for all registered entities."""
        entities = (_make_entity("Task"), _make_entity("Agent"))
        ontology = _make_ontology(entities)
        config = DriftDetectionConfig()

        service = DriftDetectionService(
            strategy=NoDriftDetection(),
            ontology=ontology,
            config=config,
        )

        reports = await service.check_all(("agent-1",))
        assert len(reports) == 2
        names = {r.entity_name for r in reports}
        assert names == {"Task", "Agent"}

    async def test_check_all_empty(self) -> None:
        """check_all with no entities returns empty."""
        ontology = _make_ontology()
        config = DriftDetectionConfig()

        service = DriftDetectionService(
            strategy=NoDriftDetection(),
            ontology=ontology,
            config=config,
        )

        reports = await service.check_all(("agent-1",))
        assert reports == ()

    def test_threshold_property(self) -> None:
        """threshold returns configured value."""
        config = DriftDetectionConfig(threshold=0.5)
        service = DriftDetectionService(
            strategy=NoDriftDetection(),
            ontology=_make_ontology(),
            config=config,
        )
        assert service.threshold == 0.5

    def test_strategy_name_property(self) -> None:
        """strategy_name returns active strategy name."""
        service = DriftDetectionService(
            strategy=NoDriftDetection(),
            ontology=_make_ontology(),
            config=DriftDetectionConfig(),
        )
        assert service.strategy_name == "none"

    async def test_high_drift_logs_warning(self) -> None:
        """High drift score triggers warning log (coverage)."""
        high_report = DriftReport(
            entity_name="Task",
            divergence_score=0.8,
            canonical_version=1,
            recommendation=DriftAction.ESCALATE,
        )
        strategy = mock_of[DriftDetectionStrategy](
            detect=AsyncMock(return_value=high_report),
        )
        strategy.strategy_name = "mock"

        config = DriftDetectionConfig(threshold=0.3)
        service = DriftDetectionService(
            strategy=strategy,
            ontology=_make_ontology(),
            config=config,
        )

        report = await service.check_entity("Task", ("agent-1",))
        assert report.divergence_score == 0.8
