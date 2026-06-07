"""Unit tests for training mode models."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.models import (
    ContentType,
    TrainingGuardDecision,
    TrainingItem,
    TrainingPlan,
    TrainingPlanStatus,
    TrainingResult,
)

# -- Fixtures ---------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _make_item(
    *,
    source_agent_id: str = "senior-1",
    content_type: ContentType = ContentType.PROCEDURAL,
    content: str = "Always validate inputs",
    relevance_score: float = 0.8,
) -> TrainingItem:
    return TrainingItem(
        source_agent_id=source_agent_id,
        content_type=content_type,
        content=content,
        relevance_score=relevance_score,
        created_at=_now(),
    )


_DEFAULT_VOLUME_CAPS = (
    (ContentType.PROCEDURAL, 50),
    (ContentType.SEMANTIC, 10),
    (ContentType.TOOL_PATTERNS, 20),
)


def _make_plan(  # noqa: PLR0913
    *,
    new_agent_id: str = "new-agent-1",
    skip_training: bool = False,
    status: TrainingPlanStatus = TrainingPlanStatus.PENDING,
    executed_at: datetime | None = None,
    volume_caps: tuple[tuple[ContentType, int], ...] | None = None,
    enabled_content_types: frozenset[ContentType] | None = None,
) -> TrainingPlan:
    return TrainingPlan(
        new_agent_id=new_agent_id,
        new_agent_role="engineer",
        new_agent_level=SeniorityLevel.JUNIOR,
        skip_training=skip_training,
        status=status,
        executed_at=executed_at,
        volume_caps=volume_caps if volume_caps is not None else _DEFAULT_VOLUME_CAPS,
        enabled_content_types=enabled_content_types
        if enabled_content_types is not None
        else frozenset(ContentType),
        created_at=_now(),
    )


# -- ContentType enum ------------------------------------------------


@pytest.mark.unit
class TestContentType:
    """ContentType enum tests."""

    def test_has_three_members(self) -> None:
        assert len(ContentType) == 3

    def test_values(self) -> None:
        assert ContentType.PROCEDURAL.value == "procedural"
        assert ContentType.SEMANTIC.value == "semantic"
        assert ContentType.TOOL_PATTERNS.value == "tool_patterns"


# -- TrainingPlanStatus enum ------------------------------------------


@pytest.mark.unit
class TestTrainingPlanStatus:
    """TrainingPlanStatus enum tests."""

    def test_has_three_members(self) -> None:
        assert len(TrainingPlanStatus) == 3

    def test_values(self) -> None:
        assert TrainingPlanStatus.PENDING.value == "pending"
        assert TrainingPlanStatus.EXECUTED.value == "executed"
        assert TrainingPlanStatus.FAILED.value == "failed"


# -- TrainingItem model -----------------------------------------------


@pytest.mark.unit
class TestTrainingItem:
    """TrainingItem model tests."""

    def test_create_minimal(self) -> None:
        item = _make_item()
        assert item.source_agent_id == "senior-1"
        assert item.content_type == ContentType.PROCEDURAL
        assert item.content == "Always validate inputs"
        assert item.relevance_score == 0.8
        assert item.source_memory_id is None
        assert item.metadata_tags == ()

    def test_frozen(self) -> None:
        item = _make_item()
        with pytest.raises(Exception):  # noqa: B017, PT011
            item.content = "modified"  # type: ignore[misc]

    def test_auto_generates_id(self) -> None:
        item1 = _make_item()
        item2 = _make_item()
        assert item1.id != item2.id

    def test_relevance_score_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            _make_item(relevance_score=-0.1)
        with pytest.raises(ValueError, match="less than or equal to 1"):
            _make_item(relevance_score=1.1)

    def test_with_source_memory_id(self) -> None:
        item = TrainingItem(
            source_agent_id="senior-1",
            content_type=ContentType.SEMANTIC,
            content="Domain knowledge",
            source_memory_id="mem-123",
            created_at=_now(),
        )
        assert item.source_memory_id == "mem-123"

    def test_with_metadata_tags(self) -> None:
        item = TrainingItem(
            source_agent_id="senior-1",
            content_type=ContentType.TOOL_PATTERNS,
            content="Use API tool",
            metadata_tags=("tag-1", "tag-2"),
            created_at=_now(),
        )
        assert item.metadata_tags == ("tag-1", "tag-2")

    def test_rejects_blank_content(self) -> None:
        with pytest.raises(ValueError, match="whitespace-only"):
            TrainingItem(
                source_agent_id="senior-1",
                content_type=ContentType.PROCEDURAL,
                content="   ",
                created_at=_now(),
            )

    def test_rejects_blank_source_agent_id(self) -> None:
        with pytest.raises(ValueError, match="whitespace-only"):
            TrainingItem(
                source_agent_id="   ",
                content_type=ContentType.PROCEDURAL,
                content="valid content",
                created_at=_now(),
            )


# -- TrainingGuardDecision model --------------------------------------


@pytest.mark.unit
class TestTrainingGuardDecision:
    """TrainingGuardDecision model tests."""

    def test_create_approved(self) -> None:
        item = _make_item()
        decision = TrainingGuardDecision(
            approved_items=(item,),
            rejected_count=0,
            guard_name="sanitization",
        )
        assert len(decision.approved_items) == 1
        assert decision.rejected_count == 0
        assert decision.approval_item_id is None

    def test_create_with_rejections(self) -> None:
        decision = TrainingGuardDecision(
            approved_items=(),
            rejected_count=3,
            guard_name="volume_cap",
            rejection_reasons=("Exceeded cap",) * 3,
        )
        assert decision.rejected_count == 3
        assert len(decision.rejection_reasons) == 3

    def test_create_with_approval_item(self) -> None:
        decision = TrainingGuardDecision(
            approved_items=(),
            rejected_count=0,
            guard_name="review_gate",
            approval_item_id="approval-123",
        )
        assert decision.approval_item_id == "approval-123"

    def test_frozen(self) -> None:
        decision = TrainingGuardDecision(
            approved_items=(),
            rejected_count=0,
            guard_name="test",
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            decision.rejected_count = 5  # type: ignore[misc]

    def test_rejects_negative_count(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            TrainingGuardDecision(
                approved_items=(),
                rejected_count=-1,
                guard_name="test",
            )


# -- TrainingPlan model -----------------------------------------------


@pytest.mark.unit
class TestTrainingPlan:
    """TrainingPlan model tests."""

    def test_create_with_defaults(self) -> None:
        plan = _make_plan()
        assert plan.status == TrainingPlanStatus.PENDING
        assert plan.source_selector_type == "role_top_performers"
        assert plan.curation_strategy_type == "relevance"
        assert plan.skip_training is False
        assert plan.require_review is True
        assert plan.override_sources == ()
        assert plan.executed_at is None
        assert len(plan.enabled_content_types) == 3

    def test_frozen(self) -> None:
        plan = _make_plan()
        with pytest.raises(Exception):  # noqa: B017, PT011
            plan.status = TrainingPlanStatus.EXECUTED  # type: ignore[misc]

    def test_auto_generates_id(self) -> None:
        plan1 = _make_plan()
        plan2 = _make_plan()
        assert plan1.id != plan2.id

    def test_volume_caps_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            _make_plan(
                volume_caps=(
                    (ContentType.PROCEDURAL, 0),
                    (ContentType.SEMANTIC, 10),
                    (ContentType.TOOL_PATTERNS, 20),
                ),
            )

    def test_volume_caps_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            _make_plan(
                volume_caps=(
                    (ContentType.PROCEDURAL, -5),
                    (ContentType.SEMANTIC, 10),
                    (ContentType.TOOL_PATTERNS, 20),
                ),
            )

    def test_content_types_required_when_active(self) -> None:
        with pytest.raises(
            ValueError,
            match="At least one content type",
        ):
            _make_plan(enabled_content_types=frozenset())

    def test_empty_content_types_allowed_when_skipping(self) -> None:
        plan = _make_plan(
            skip_training=True,
            enabled_content_types=frozenset(),
        )
        assert plan.skip_training is True
        assert len(plan.enabled_content_types) == 0

    def test_executed_requires_executed_at(self) -> None:
        with pytest.raises(ValueError, match="executed_at must be set"):
            _make_plan(
                status=TrainingPlanStatus.EXECUTED,
                executed_at=None,
            )

    def test_pending_rejects_executed_at(self) -> None:
        with pytest.raises(ValueError, match="executed_at must be None"):
            _make_plan(
                status=TrainingPlanStatus.PENDING,
                executed_at=_now(),
            )

    def test_executed_with_timestamp(self) -> None:
        now = _now()
        plan = _make_plan(
            status=TrainingPlanStatus.EXECUTED,
            executed_at=now,
        )
        assert plan.executed_at == now

    def test_failed_allows_executed_at(self) -> None:
        now = _now()
        plan = _make_plan(
            status=TrainingPlanStatus.FAILED,
            executed_at=now,
        )
        assert plan.status == TrainingPlanStatus.FAILED

    def test_override_sources(self) -> None:
        plan = TrainingPlan(
            new_agent_id="new-1",
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            override_sources=("senior-a", "senior-b"),
            created_at=_now(),
        )
        assert plan.override_sources == ("senior-a", "senior-b")

    def test_default_volume_caps(self) -> None:
        plan = _make_plan()
        caps_dict = dict(plan.volume_caps)
        assert caps_dict[ContentType.PROCEDURAL] == 50
        assert caps_dict[ContentType.SEMANTIC] == 10
        assert caps_dict[ContentType.TOOL_PATTERNS] == 20


# -- TrainingResult model ---------------------------------------------


@pytest.mark.unit
class TestTrainingResult:
    """TrainingResult model tests."""

    def test_create_minimal(self) -> None:
        now = _now()
        result = TrainingResult(
            plan_id="plan-1",
            new_agent_id="new-1",
            started_at=now,
            completed_at=now,
        )
        assert result.plan_id == "plan-1"
        assert result.source_agents_used == ()
        assert result.items_extracted == ()
        assert result.errors == ()
        assert result.approval_item_id is None

    def test_frozen(self) -> None:
        now = _now()
        result = TrainingResult(
            plan_id="plan-1",
            new_agent_id="new-1",
            started_at=now,
            completed_at=now,
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            result.plan_id = "modified"  # type: ignore[misc]

    def test_completed_before_started_rejected(self) -> None:
        now = _now()
        with pytest.raises(ValueError, match="completed_at"):
            TrainingResult(
                plan_id="plan-1",
                new_agent_id="new-1",
                started_at=now,
                completed_at=now - timedelta(seconds=1),
            )

    def test_with_full_pipeline_counts(self) -> None:
        now = _now()
        result = TrainingResult(
            plan_id="plan-1",
            new_agent_id="new-1",
            source_agents_used=("senior-1", "senior-2"),
            items_extracted=(
                (ContentType.PROCEDURAL, 100),
                (ContentType.SEMANTIC, 20),
            ),
            items_after_curation=(
                (ContentType.PROCEDURAL, 50),
                (ContentType.SEMANTIC, 10),
            ),
            items_after_guards=(
                (ContentType.PROCEDURAL, 45),
                (ContentType.SEMANTIC, 10),
            ),
            items_stored=(
                (ContentType.PROCEDURAL, 45),
                (ContentType.SEMANTIC, 10),
            ),
            errors=("Sanitization rejected 5 items",),
            started_at=now,
            completed_at=now + timedelta(seconds=30),
        )
        assert len(result.source_agents_used) == 2
        assert len(result.items_extracted) == 2
        assert len(result.errors) == 1

    def test_auto_generates_id(self) -> None:
        now = _now()
        r1 = TrainingResult(
            plan_id="plan-1",
            new_agent_id="new-1",
            started_at=now,
            completed_at=now,
        )
        r2 = TrainingResult(
            plan_id="plan-1",
            new_agent_id="new-1",
            started_at=now,
            completed_at=now,
        )
        assert r1.id != r2.id
