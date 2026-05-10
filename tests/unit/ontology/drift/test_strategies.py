"""Tests for drift detection strategies."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.ontology.drift.active import ActiveValidatorStrategy
from synthorg.ontology.drift.layered import LayeredDetectionStrategy
from synthorg.ontology.drift.noop import NoDriftDetection
from synthorg.ontology.drift.passive import PassiveMonitorStrategy
from synthorg.ontology.drift.protocol import DriftDetectionStrategy
from synthorg.ontology.models import (
    DriftAction,
    DriftReport,
    EntityDefinition,
    EntitySource,
    EntityTier,
)

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _make_entity(
    name: str,
    definition: str = "",
    *,
    tier: EntityTier = EntityTier.CORE,
) -> EntityDefinition:
    return EntityDefinition(
        name=name,
        tier=tier,
        source=EntitySource.AUTO,
        definition=definition,
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

    async def get(name: str) -> EntityDefinition:
        for e in entities:
            if e.name == name:
                return e
        from synthorg.ontology.errors import OntologyNotFoundError

        raise OntologyNotFoundError(name)

    backend.get = AsyncMock(side_effect=get)
    return backend


def _make_memory(
    entries_by_agent: dict[str, tuple[str, ...]] | None = None,
) -> AsyncMock:
    """Create a mock MemoryBackend with optional stored entries."""
    from synthorg.core.enums import MemoryCategory
    from synthorg.memory.models import MemoryEntry, MemoryMetadata

    memory = AsyncMock()
    memory.is_connected = True

    async def retrieve(
        agent_id: str,
        query: object,
    ) -> tuple[MemoryEntry, ...]:
        if entries_by_agent is None:
            return ()
        contents = entries_by_agent.get(agent_id, ())
        return tuple(
            MemoryEntry(
                id=f"mem-{i}",
                agent_id=agent_id,
                category=MemoryCategory.EPISODIC,
                content=content,
                metadata=MemoryMetadata(),
                created_at=_NOW,
            )
            for i, content in enumerate(contents)
        )

    memory.retrieve = AsyncMock(side_effect=retrieve)
    return memory


@pytest.mark.unit
class TestNoDriftDetection:
    """Tests for NoDriftDetection."""

    async def test_returns_clean_report(self) -> None:
        strategy = NoDriftDetection()
        report = await strategy.detect("Task", ("agent-1",))
        assert report.divergence_score == 0.0
        assert report.recommendation == DriftAction.NO_ACTION
        assert report.entity_name == "Task"

    def test_strategy_name(self) -> None:
        assert NoDriftDetection().strategy_name == "none"


@pytest.mark.unit
class TestPassiveMonitorStrategy:
    """Tests for PassiveMonitorStrategy."""

    async def test_no_entries_zero_drift(self) -> None:
        """No agent memories means zero divergence."""
        entities = (_make_entity("Task", "A unit of work"),)
        ontology = _make_ontology(entities)
        memory = _make_memory()
        strategy = PassiveMonitorStrategy(
            ontology=ontology,
            memory=memory,
        )

        report = await strategy.detect("Task", ("agent-1",))
        assert report.divergence_score == 0.0
        assert report.recommendation == DriftAction.NO_ACTION

    async def test_high_overlap_low_drift(self) -> None:
        """Agent memories matching definition have low divergence."""
        entities = (_make_entity("Task", "A unit of work within the company"),)
        ontology = _make_ontology(entities)
        memory = _make_memory(
            {
                "agent-1": ("This is a unit of work within the company",),
            }
        )
        strategy = PassiveMonitorStrategy(
            ontology=ontology,
            memory=memory,
        )

        report = await strategy.detect("Task", ("agent-1",))
        assert report.divergence_score < 0.3

    async def test_low_overlap_high_drift(self) -> None:
        """Agent memories diverging from definition have high divergence."""
        entities = (_make_entity("Task", "A unit of work within the company"),)
        ontology = _make_ontology(entities)
        memory = _make_memory(
            {
                "agent-1": ("completely unrelated content about weather",),
            }
        )
        strategy = PassiveMonitorStrategy(
            ontology=ontology,
            memory=memory,
        )

        report = await strategy.detect("Task", ("agent-1",))
        assert report.divergence_score > 0.5

    async def test_multiple_agents(self) -> None:
        """Report aggregates across multiple agents."""
        entities = (_make_entity("Task", "A unit of work"),)
        ontology = _make_ontology(entities)
        memory = _make_memory(
            {
                "agent-1": ("This is a unit of work",),
                "agent-2": ("Random unrelated text about cats",),
            }
        )
        strategy = PassiveMonitorStrategy(
            ontology=ontology,
            memory=memory,
        )

        report = await strategy.detect(
            "Task",
            ("agent-1", "agent-2"),
        )
        assert len(report.divergent_agents) >= 1
        assert report.entity_name == "Task"

    async def test_entity_not_found(self) -> None:
        """Returns clean report when entity doesn't exist."""
        ontology = _make_ontology()
        memory = _make_memory()
        strategy = PassiveMonitorStrategy(
            ontology=ontology,
            memory=memory,
        )

        report = await strategy.detect("Nonexistent", ("agent-1",))
        assert report.divergence_score == 0.0

    def test_strategy_name(self) -> None:
        ontology = _make_ontology()
        memory = _make_memory()
        strategy = PassiveMonitorStrategy(
            ontology=ontology,
            memory=memory,
        )
        assert strategy.strategy_name == "passive"


@pytest.mark.unit
class TestActiveValidatorStrategy:
    """Tests for ActiveValidatorStrategy."""

    def test_strategy_name(self) -> None:
        ontology = _make_ontology()
        memory = _make_memory()
        strategy = ActiveValidatorStrategy(
            ontology=ontology,
            memory=memory,
        )
        assert strategy.strategy_name == "active"

    async def test_inherits_passive_detection(self) -> None:
        """Active strategy uses same detection logic as passive."""
        entities = (_make_entity("Task", "A unit of work"),)
        ontology = _make_ontology(entities)
        memory = _make_memory()
        strategy = ActiveValidatorStrategy(
            ontology=ontology,
            memory=memory,
        )

        report = await strategy.detect("Task", ("agent-1",))
        assert report.divergence_score == 0.0


@pytest.mark.unit
class TestLayeredDetectionStrategy:
    """Tests for LayeredDetectionStrategy."""

    async def test_core_uses_core_strategy(self) -> None:
        """CORE entities routed to core strategy."""
        entity = _make_entity("Task", tier=EntityTier.CORE)
        ontology = _make_ontology((entity,))

        clean_report = DriftReport(
            entity_name="Task",
            divergence_score=0.0,
            canonical_version=1,
            recommendation=DriftAction.NO_ACTION,
        )
        core = AsyncMock(spec=DriftDetectionStrategy)
        core.detect = AsyncMock(return_value=clean_report)
        user = AsyncMock(spec=DriftDetectionStrategy)
        user.detect = AsyncMock()

        strategy = LayeredDetectionStrategy(
            ontology=ontology,
            core_strategy=core,
            user_strategy=user,
        )

        await strategy.detect("Task", ("agent-1",))
        core.detect.assert_awaited_once()
        user.detect.assert_not_called()

    async def test_user_uses_user_strategy(self) -> None:
        """USER entities routed to user strategy."""
        entity = _make_entity(
            "Invoice",
            tier=EntityTier.USER,
        )
        ontology = _make_ontology((entity,))

        clean_report = DriftReport(
            entity_name="Invoice",
            divergence_score=0.0,
            canonical_version=1,
            recommendation=DriftAction.NO_ACTION,
        )
        core = AsyncMock(spec=DriftDetectionStrategy)
        core.detect = AsyncMock()
        user = AsyncMock(spec=DriftDetectionStrategy)
        user.detect = AsyncMock(return_value=clean_report)

        strategy = LayeredDetectionStrategy(
            ontology=ontology,
            core_strategy=core,
            user_strategy=user,
        )

        await strategy.detect("Invoice", ("agent-1",))
        user.detect.assert_awaited_once()
        core.detect.assert_not_called()

    def test_strategy_name(self) -> None:
        ontology = _make_ontology()
        strategy = LayeredDetectionStrategy(
            ontology=ontology,
            core_strategy=AsyncMock(spec=DriftDetectionStrategy),
            user_strategy=AsyncMock(spec=DriftDetectionStrategy),
        )
        assert strategy.strategy_name == "layered"
