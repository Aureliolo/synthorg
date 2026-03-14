"""Tests for the conflict resolution service."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conflict_resolution.authority_strategy import (
    AuthorityResolver,
)
from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
)
from synthorg.communication.conflict_resolution.human_strategy import (
    HumanEscalationResolver,
)
from synthorg.communication.conflict_resolution.models import (
    ConflictResolutionOutcome,
)
from synthorg.communication.conflict_resolution.service import (
    ConflictResolutionService,
)
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.enums import (
    ConflictResolutionStrategy,
    ConflictType,
)
from synthorg.communication.errors import ConflictResolutionError
from synthorg.core.enums import SeniorityLevel

from .conftest import make_position

pytestmark = pytest.mark.timeout(30)


def _make_service(
    hierarchy: HierarchyResolver,
    strategy: ConflictResolutionStrategy = ConflictResolutionStrategy.AUTHORITY,
) -> ConflictResolutionService:
    """Create a service with authority + human resolvers."""
    config = ConflictResolutionConfig(strategy=strategy)
    return ConflictResolutionService(
        config=config,
        resolvers={
            ConflictResolutionStrategy.AUTHORITY: AuthorityResolver(
                hierarchy=hierarchy,
            ),
            ConflictResolutionStrategy.HUMAN: HumanEscalationResolver(),
        },
    )


@pytest.mark.unit
class TestCreateConflict:
    def test_creates_conflict_with_id(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Microservices vs monolith",
            positions=[
                make_position(agent_id="sr_dev"),
                make_position(
                    agent_id="jr_dev",
                    position="Use monolith",
                ),
            ],
        )
        assert conflict.id.startswith("conflict-")
        assert conflict.type == ConflictType.ARCHITECTURE

    def test_cross_department_detection(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.PRIORITY,
            subject="Priority dispute",
            positions=[
                make_position(agent_id="sr_dev", department="engineering"),
                make_position(
                    agent_id="qa_eng",
                    department="qa",
                    position="QA first",
                ),
            ],
        )
        assert conflict.is_cross_department is True

    def test_same_department_not_cross(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.IMPLEMENTATION,
            subject="Implementation dispute",
            positions=[
                make_position(agent_id="sr_dev", department="engineering"),
                make_position(
                    agent_id="jr_dev",
                    department="engineering",
                    position="Other",
                ),
            ],
        )
        assert conflict.is_cross_department is False

    def test_fewer_than_two_positions_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        with pytest.raises(ConflictResolutionError, match="at least 2"):
            service.create_conflict(
                conflict_type=ConflictType.ARCHITECTURE,
                subject="Solo",
                positions=[make_position()],
            )

    def test_duplicate_agent_ids_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        with pytest.raises(ConflictResolutionError, match="Duplicate"):
            service.create_conflict(
                conflict_type=ConflictType.ARCHITECTURE,
                subject="Dup",
                positions=[
                    make_position(agent_id="same"),
                    make_position(agent_id="same", position="Other"),
                ],
            )

    def test_optional_task_id(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.RESOURCE,
            subject="Budget allocation",
            positions=[
                make_position(agent_id="sr_dev"),
                make_position(agent_id="jr_dev", position="Other"),
            ],
            task_id="task-42",
        )
        assert conflict.task_id == "task-42"


@pytest.mark.unit
class TestResolve:
    async def test_authority_resolution(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design disagreement",
            positions=[
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                ),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        resolution, records = await service.resolve(conflict)
        assert resolution.winning_agent_id == "sr_dev"
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY
        assert records[0].dissenting_agent_id == "jr_dev"

    async def test_human_resolution(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy, ConflictResolutionStrategy.HUMAN)
        conflict = service.create_conflict(
            conflict_type=ConflictType.PROCESS,
            subject="Process change",
            positions=[
                make_position(agent_id="sr_dev"),
                make_position(agent_id="jr_dev", position="Other"),
            ],
        )
        resolution, _records = await service.resolve(conflict)
        assert resolution.outcome == ConflictResolutionOutcome.ESCALATED_TO_HUMAN

    async def test_unregistered_strategy_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy, ConflictResolutionStrategy.DEBATE)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design",
            positions=[
                make_position(agent_id="sr_dev"),
                make_position(agent_id="jr_dev", position="Other"),
            ],
        )
        with pytest.raises(ConflictResolutionError, match="No resolver"):
            await service.resolve(conflict)


@pytest.mark.unit
class TestAuditTrail:
    async def test_records_appended(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design",
            positions=[
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        await service.resolve(conflict)
        records = service.get_dissent_records()
        assert len(records) == 1

    async def test_multiple_resolutions(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        for i in range(3):
            conflict = service.create_conflict(
                conflict_type=ConflictType.ARCHITECTURE,
                subject=f"Design dispute {i}",
                positions=[
                    make_position(
                        agent_id=f"agent-{i}a",
                        level=SeniorityLevel.SENIOR,
                    ),
                    make_position(
                        agent_id=f"agent-{i}b",
                        level=SeniorityLevel.JUNIOR,
                        position="Other",
                    ),
                ],
            )
            await service.resolve(conflict)
        assert len(service.get_dissent_records()) == 3

    async def test_query_by_agent_id(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design",
            positions=[
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        await service.resolve(conflict)
        results = service.query_dissent_records(agent_id="jr_dev")
        assert len(results) == 1
        assert results[0].dissenting_agent_id == "jr_dev"

    async def test_query_by_conflict_type(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        for ct in [ConflictType.ARCHITECTURE, ConflictType.PRIORITY]:
            conflict = service.create_conflict(
                conflict_type=ct,
                subject=f"{ct} dispute",
                positions=[
                    make_position(
                        agent_id=f"a-{ct}",
                        level=SeniorityLevel.SENIOR,
                    ),
                    make_position(
                        agent_id=f"b-{ct}",
                        level=SeniorityLevel.JUNIOR,
                        position="Other",
                    ),
                ],
            )
            await service.resolve(conflict)
        results = service.query_dissent_records(
            conflict_type=ConflictType.ARCHITECTURE,
        )
        assert len(results) == 1

    async def test_query_by_since(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design",
            positions=[
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        await service.resolve(conflict)
        # Query with future timestamp — should return empty
        future = datetime(2099, 1, 1, tzinfo=UTC)
        results = service.query_dissent_records(since=future)
        assert len(results) == 0

    async def test_query_no_filters_returns_all(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design",
            positions=[
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        await service.resolve(conflict)
        results = service.query_dissent_records()
        assert len(results) == 1

    async def test_query_by_strategy(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        # Resolve via authority
        conflict_a = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design dispute",
            positions=[
                make_position(
                    agent_id="sr_dev",
                    level=SeniorityLevel.SENIOR,
                ),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        await service.resolve(conflict_a)
        # Resolve via human escalation
        service_h = _make_service(hierarchy, ConflictResolutionStrategy.HUMAN)
        conflict_h = service_h.create_conflict(
            conflict_type=ConflictType.PROCESS,
            subject="Process dispute",
            positions=[
                make_position(agent_id="a-h", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="b-h",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        await service_h.resolve(conflict_h)
        # Query authority service by strategy
        results = service.query_dissent_records(
            strategy=ConflictResolutionStrategy.AUTHORITY,
        )
        assert len(results) == 1
        assert results[0].strategy_used == ConflictResolutionStrategy.AUTHORITY

    async def test_query_combined_filters(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        # Create two conflicts with different types
        for ct in [ConflictType.ARCHITECTURE, ConflictType.PRIORITY]:
            conflict = service.create_conflict(
                conflict_type=ct,
                subject=f"{ct} dispute",
                positions=[
                    make_position(
                        agent_id=f"a-{ct}",
                        level=SeniorityLevel.SENIOR,
                    ),
                    make_position(
                        agent_id=f"b-{ct}",
                        level=SeniorityLevel.JUNIOR,
                        position="Other",
                    ),
                ],
            )
            await service.resolve(conflict)
        # Query with both agent_id and conflict_type filters
        results = service.query_dissent_records(
            agent_id="b-architecture",
            conflict_type=ConflictType.ARCHITECTURE,
        )
        assert len(results) == 1
        assert results[0].dissenting_agent_id == "b-architecture"

    def test_zero_positions_raises(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        with pytest.raises(ConflictResolutionError, match="at least 2"):
            service.create_conflict(
                conflict_type=ConflictType.ARCHITECTURE,
                subject="Empty",
                positions=[],
            )

    async def test_get_dissent_records_returns_tuple(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        service = _make_service(hierarchy)
        conflict = service.create_conflict(
            conflict_type=ConflictType.ARCHITECTURE,
            subject="Design",
            positions=[
                make_position(agent_id="sr_dev", level=SeniorityLevel.SENIOR),
                make_position(
                    agent_id="jr_dev",
                    level=SeniorityLevel.JUNIOR,
                    position="Other",
                ),
            ],
        )
        await service.resolve(conflict)
        records = service.get_dissent_records()
        assert isinstance(records, tuple)
