"""Integration tests for the training mode pipeline.

Tests the full pipeline from plan creation through extraction,
curation, guards, and memory storage.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.memory_enums import MemoryCategory
from synthorg.hr.enums import AgentStatus
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.curation.relevance import (
    RelevanceScoreCuration,
)
from synthorg.hr.training.extractors.memory_backed import (
    MemoryBackedExtractor,
)
from synthorg.hr.training.extractors.tool_patterns import (
    ToolPatternExtractor,
)
from synthorg.hr.training.guards.review_gate import ReviewGateGuard
from synthorg.hr.training.guards.sanitization import SanitizationGuard
from synthorg.hr.training.guards.volume_cap import VolumeCapGuard
from synthorg.hr.training.models import (
    ContentType,
    TrainingPlan,
    TrainingPlanStatus,
)
from synthorg.hr.training.protocol import TrainingGuard
from synthorg.hr.training.service import TrainingService
from synthorg.hr.training.source_selectors.role_top_performers import (
    RoleTopPerformers,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.tools.invocation_tracker import ToolInvocationTracker


def _now() -> datetime:
    return datetime.now(UTC)


def _make_identity(
    *,
    agent_id: str | None = None,
    role: str = "engineer",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.SENIOR,
) -> MagicMock:
    identity = MagicMock()
    identity.id = agent_id or str(uuid4())
    identity.name = f"agent-{identity.id}"
    identity.role = role
    identity.department = department
    identity.level = level
    identity.status = AgentStatus.ACTIVE
    return identity


def _make_memory_entry(
    *,
    memory_id: str = "mem-1",
    agent_id: str = "senior-1",
    category: MemoryCategory = MemoryCategory.PROCEDURAL,
    content: str = "Always validate inputs before processing",
) -> MemoryEntry:
    now = _now()
    return MemoryEntry(
        id=memory_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(source="test", tags=("skill",)),
        created_at=now,
    )


def _make_plan(
    *,
    require_review: bool = False,
    override_sources: tuple[str, ...] = (),
    skip_training: bool = False,
    volume_caps: tuple[tuple[ContentType, int], ...] | None = None,
) -> TrainingPlan:
    return TrainingPlan(
        new_agent_id="new-agent-1",
        new_agent_role="engineer",
        new_agent_level=SeniorityLevel.JUNIOR,
        require_review=require_review,
        override_sources=override_sources,
        skip_training=skip_training,
        volume_caps=volume_caps
        or (
            (ContentType.PROCEDURAL, 50),
            (ContentType.SEMANTIC, 10),
            (ContentType.TOOL_PATTERNS, 20),
        ),
        created_at=_now(),
    )


def _build_service(
    *,
    registry: AsyncMock | None = None,
    tracker: AsyncMock | None = None,
    backend: AsyncMock | None = None,
    tool_tracker: AsyncMock | None = None,
    approval_store: AsyncMock | None = None,
) -> TrainingService:
    """Build a real TrainingService with mocked backends.

    The collaborators stay on the looser ``AsyncMock(spec=Protocol)``
    rung rather than ``mock_of[Protocol]``: ``TrainingService.__init__``
    deep-copies the extractors dict, and the ``create_autospec(Protocol,
    instance=True, spec_set=True)`` instances that ``mock_of`` produces
    carry references to ``_abc._abc_data`` (Protocol's abstract-method
    registry) which is unpicklable. ``copy.deepcopy`` falls through to
    the pickle protocol for those nodes and raises
    ``TypeError: cannot pickle 'module' object`` /
    ``cannot pickle '_abc._abc_data' object``. ``AsyncMock(spec=X)``
    stores ``X`` as a class reference without the autospec proxy
    layer, so deepcopy succeeds.
    """
    if registry is None:
        senior = _make_identity(agent_id="senior-1")
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = (senior,)

    if tracker is None:
        tracker = AsyncMock(spec=PerformanceTracker)
        snapshot = MagicMock()
        snapshot.overall_quality_score = 0.8
        tracker.get_snapshot.return_value = snapshot

    if backend is None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve.return_value = (
            _make_memory_entry(content="Procedural lesson 1"),
            _make_memory_entry(content="Procedural lesson 2"),
        )
        backend.store.return_value = "stored-id"

    if tool_tracker is None:
        tool_tracker = AsyncMock(spec=ToolInvocationTracker)
        record = MagicMock()
        record.tool_name = "api_tool"
        record.is_success = True
        record.agent_id = "senior-1"
        tool_tracker.get_records.return_value = (record,)

    selector = RoleTopPerformers(
        registry=registry,
        tracker=tracker,
        top_n=3,
    )
    extractors: dict[
        ContentType,
        MemoryBackedExtractor | ToolPatternExtractor,
    ] = {
        ContentType.PROCEDURAL: MemoryBackedExtractor(
            backend=backend,
            memory_category=MemoryCategory.PROCEDURAL,
            content_type=ContentType.PROCEDURAL,
        ),
        ContentType.SEMANTIC: MemoryBackedExtractor(
            backend=backend,
            memory_category=MemoryCategory.SEMANTIC,
            content_type=ContentType.SEMANTIC,
        ),
        ContentType.TOOL_PATTERNS: ToolPatternExtractor(
            tracker=tool_tracker,
        ),
    }
    curation = RelevanceScoreCuration(top_k=50)
    guards: tuple[TrainingGuard, ...] = (
        SanitizationGuard(),
        VolumeCapGuard(),
    )
    if approval_store is not None:
        guards = (*guards, ReviewGateGuard(approval_store=approval_store))

    return TrainingService(
        selector=selector,
        extractors=extractors,
        curation=curation,
        guards=guards,
        memory_backend=backend,
    )


@pytest.mark.integration
class TestFullTrainingPipeline:
    """Full training pipeline integration tests."""

    async def test_hire_agent_and_train(self) -> None:
        """Full pipeline: select sources, extract, curate, guard, store."""
        backend = AsyncMock()
        backend.retrieve.return_value = (
            _make_memory_entry(content="Always check return values"),
            _make_memory_entry(content="Log before and after API calls"),
        )
        backend.store.return_value = "stored-id"

        service = _build_service(backend=backend)
        plan = _make_plan()
        result = await service.execute(plan)

        assert result.new_agent_id == "new-agent-1"
        assert len(result.source_agents_used) > 0
        # Items should have been stored
        total_stored = sum(c for _, c in result.items_stored)
        assert total_stored > 0
        assert backend.store.call_count > 0

    async def test_sanitization_strips_paths(self) -> None:
        """Items with paths should be sanitized, not rejected."""
        backend = AsyncMock()
        backend.retrieve.return_value = (
            _make_memory_entry(
                content="Error at C:\\Users\\dev\\app.py but recovered",
            ),
        )
        backend.store.return_value = "stored-id"

        service = _build_service(backend=backend)
        plan = _make_plan()
        result = await service.execute(plan)

        # Should pass sanitization (content has non-path text)
        total_stored = sum(c for _, c in result.items_stored)
        assert total_stored > 0
        # Stored content must not contain the original path fragment.
        for call in backend.store.call_args_list:
            stored_request = call.args[1]
            assert "C:\\Users\\dev\\app.py" not in stored_request.content

    async def test_volume_caps_enforced(self) -> None:
        """Volume caps truncate items per content type."""
        entries = tuple(
            _make_memory_entry(
                memory_id=f"mem-{i}",
                content=f"Lesson number {i} with sufficient text",
            )
            for i in range(20)
        )
        backend = AsyncMock()
        backend.retrieve.return_value = entries
        backend.store.return_value = "stored-id"

        caps = (
            (ContentType.PROCEDURAL, 5),
            (ContentType.SEMANTIC, 5),
            (ContentType.TOOL_PATTERNS, 5),
        )
        service = _build_service(backend=backend)
        plan = _make_plan(volume_caps=caps)
        result = await service.execute(plan)

        cap_by_type = dict(caps)
        for ct, count in result.items_after_guards:
            assert count <= cap_by_type[ct], (
                f"{ct.value}: {count} exceeds cap {cap_by_type[ct]}"
            )

    async def test_override_sources_precedence(self) -> None:
        """override_sources bypasses the selector entirely."""
        registry = AsyncMock()
        senior = _make_identity(agent_id="override-senior")
        registry.list_active.return_value = (senior,)

        tracker = AsyncMock()
        snapshot = MagicMock()
        snapshot.overall_quality_score = 0.9
        tracker.get_snapshot.return_value = snapshot

        service = _build_service(registry=registry, tracker=tracker)
        plan = _make_plan(
            override_sources=("override-senior",),
        )
        result = await service.execute(plan)

        assert "override-senior" in result.source_agents_used
        # Selector should not have been called since overrides exist
        registry.list_active.assert_not_called()

    async def test_idempotent_re_execution(self) -> None:
        """Re-running an already-executed plan is a no-op."""
        service = _build_service()
        plan = TrainingPlan(
            new_agent_id="new-1",
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            status=TrainingPlanStatus.EXECUTED,
            executed_at=_now(),
            require_review=False,
            created_at=_now(),
        )
        result = await service.execute(plan)
        assert result.items_stored == ()
        assert result.source_agents_used == ()

    async def test_skip_training_produces_empty_result(self) -> None:
        """skip_training=True produces an empty result."""
        service = _build_service()
        plan = _make_plan(skip_training=True)
        result = await service.execute(plan)
        assert result.items_stored == ()
        assert result.source_agents_used == ()

    async def test_review_gate_blocks_seeding(self) -> None:
        """When review is required the pipeline seeds nothing and
        records a pending approval so onboarding can resume later."""
        approval_store = AsyncMock()
        approval_store.add = AsyncMock()

        backend = AsyncMock()
        backend.retrieve.return_value = (
            _make_memory_entry(content="Procedural lesson"),
        )
        backend.store.return_value = "stored-id"

        service = _build_service(
            backend=backend,
            approval_store=approval_store,
        )
        plan = _make_plan(require_review=True)
        result = await service.execute(plan)

        # Review gate must block seeding entirely.
        assert result.review_pending is True
        assert result.approval_item_id is not None
        assert len(result.pending_approvals) >= 1
        for _, count in result.items_stored:
            assert count == 0
        backend.store.assert_not_called()
        approval_store.add.assert_called()

    async def test_review_gate_creates_pending_approvals(self) -> None:
        """Review gate creates PENDING approvals and prevents storage.

        Verifies that when ``require_review=True`` the guard creates
        approval items in PENDING state and blocks seeding entirely.
        This test does NOT exercise the reviewer rejection or resume
        flow -- those are separate endpoint concerns.
        """
        approval_store = AsyncMock()
        approval_items: list[ApprovalItem] = []

        async def _capture(item: ApprovalItem) -> None:
            approval_items.append(item)

        approval_store.add.side_effect = _capture

        backend = AsyncMock()
        backend.retrieve.return_value = (
            _make_memory_entry(content="Procedural lesson A"),
            _make_memory_entry(memory_id="mem-2", content="Procedural lesson B"),
        )
        backend.store.return_value = "stored-id"

        service = _build_service(
            backend=backend,
            approval_store=approval_store,
        )
        plan = _make_plan(require_review=True)
        result = await service.execute(plan)

        assert result.review_pending is True
        for item in approval_items:
            assert item.status == ApprovalStatus.PENDING

        backend.store.assert_not_called()
        total_stored = sum(count for _, count in result.items_stored)
        assert total_stored == 0

    async def test_extractor_error_fails_pipeline_with_logging(self) -> None:
        """Backend errors propagate through the TaskGroup with context."""
        backend = AsyncMock()
        backend.retrieve.side_effect = RuntimeError("backend unavailable")

        service = _build_service(backend=backend)
        plan = _make_plan()

        with pytest.raises(BaseExceptionGroup) as excinfo:
            await service.execute(plan)
        assert excinfo.group_contains(RuntimeError)
