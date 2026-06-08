"""Tests for the activity timeline pure functions."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.communication.delegation.models import DelegationRecord
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.hr.activity import (
    ActivityEvent,
    _cost_record_to_activity,
    filter_career_events,
    merge_activity_timeline,
    redact_cost_events,
)
from synthorg.hr.enums import ActivityEventType, LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.tools.invocation_record import ToolInvocationRecord

_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


def _make_lifecycle_event(  # noqa: PLR0913
    *,
    event_type: LifecycleEventType = LifecycleEventType.HIRED,
    timestamp: datetime = _NOW,
    agent_id: str = "agent-001",
    agent_name: str = "alice",
    details: str = "",
    initiated_by: str = "system",
    metadata: dict[str, str] | None = None,
) -> AgentLifecycleEvent:
    return AgentLifecycleEvent(
        agent_id=agent_id,
        agent_name=agent_name,
        event_type=event_type,
        timestamp=timestamp,
        initiated_by=initiated_by,
        details=details,
        metadata=metadata or {},
    )


def _make_task_metric(  # noqa: PLR0913
    *,
    started_at: datetime | None = None,
    completed_at: datetime = _NOW,
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    is_success: bool = True,
    duration_seconds: float = 60.0,
    cost: float = 0.05,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=agent_id,
        task_id=task_id,
        task_type=TaskType.DEVELOPMENT,
        started_at=started_at,
        completed_at=completed_at,
        is_success=is_success,
        duration_seconds=duration_seconds,
        cost=cost,
        currency="USD",
        turns_used=5,
        tokens_used=1000,
        complexity=Complexity.MEDIUM,
    )


def _make_cost_record(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    timestamp: datetime = _NOW,
    cost: float = 0.0025,
    input_tokens: int = 500,
    output_tokens: int = 100,
    model: str = "test-medium-001",
    provider: str = "test-provider",
) -> CostRecord:
    return CostRecord(
        agent_id=agent_id,
        task_id=task_id,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        currency="USD",
        timestamp=timestamp,
    )


def _make_tool_invocation(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    task_id: str | None = "task-001",
    tool_name: str = "read_file",
    is_success: bool = True,
    timestamp: datetime = _NOW,
    error_message: str | None = None,
) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        agent_id=agent_id,
        task_id=task_id,
        tool_name=tool_name,
        is_success=is_success,
        timestamp=timestamp,
        error_message=error_message,
    )


def _make_delegation_record(  # noqa: PLR0913
    *,
    delegation_id: str = "del-001",
    delegator_id: str = "agent-manager",
    delegatee_id: str = "agent-worker",
    original_task_id: str = "task-parent",
    delegated_task_id: str = "del-abc123",
    timestamp: datetime = _NOW,
    refinement: str = "",
) -> DelegationRecord:
    return DelegationRecord(
        delegation_id=delegation_id,
        delegator_id=delegator_id,
        delegatee_id=delegatee_id,
        original_task_id=original_task_id,
        delegated_task_id=delegated_task_id,
        timestamp=timestamp,
        refinement=refinement,
    )


# ── Existing merge tests ─────────────────────────────────────────


@pytest.mark.unit
class TestMergeActivityTimeline:
    def test_merge_lifecycle_and_tasks(self) -> None:
        hired = _make_lifecycle_event(
            event_type=LifecycleEventType.HIRED,
            timestamp=_NOW - timedelta(days=10),
            details="Hired as developer",
        )
        task = _make_task_metric(
            completed_at=_NOW - timedelta(days=5),
            task_id="task-100",
        )
        promoted = _make_lifecycle_event(
            event_type=LifecycleEventType.PROMOTED,
            timestamp=_NOW - timedelta(days=1),
            details="Promoted to senior",
        )

        timeline = merge_activity_timeline(
            lifecycle_events=(hired, promoted),
            task_metrics=(task,),
        )

        assert len(timeline) == 3
        # Most recent first
        assert timeline[0].event_type == "promoted"
        assert timeline[1].event_type == "task_completed"
        assert timeline[2].event_type == "hired"

    def test_empty_inputs(self) -> None:
        timeline = merge_activity_timeline(
            lifecycle_events=(),
            task_metrics=(),
        )
        assert timeline == ()

    def test_only_lifecycle_events(self) -> None:
        hired = _make_lifecycle_event(
            event_type=LifecycleEventType.HIRED,
            timestamp=_NOW - timedelta(days=5),
        )
        onboarded = _make_lifecycle_event(
            event_type=LifecycleEventType.ONBOARDED,
            timestamp=_NOW - timedelta(days=4),
        )

        timeline = merge_activity_timeline(
            lifecycle_events=(hired, onboarded),
            task_metrics=(),
        )

        assert len(timeline) == 2
        assert timeline[0].event_type == "onboarded"
        assert timeline[0].description == "Agent onboarded"
        assert timeline[1].event_type == "hired"
        assert timeline[1].description == "Agent hired"

    def test_only_task_metrics(self) -> None:
        t1 = _make_task_metric(
            completed_at=_NOW - timedelta(hours=2),
            task_id="task-a",
        )
        t2 = _make_task_metric(
            completed_at=_NOW - timedelta(hours=1),
            task_id="task-b",
            is_success=False,
        )

        timeline = merge_activity_timeline(
            lifecycle_events=(),
            task_metrics=(t1, t2),
        )

        assert len(timeline) == 2
        assert timeline[0].event_type == "task_completed"
        assert timeline[0].related_ids["task_id"] == "task-b"
        assert "failed" in timeline[0].description
        assert timeline[1].related_ids["task_id"] == "task-a"
        assert "succeeded" in timeline[1].description
        assert "$" in timeline[1].description

    def test_currency_passed_to_task_metric_descriptions(self) -> None:
        task = _make_task_metric(task_id="task-usd", cost=1.5)
        timeline = merge_activity_timeline(
            lifecycle_events=(),
            task_metrics=(task,),
            currency="USD",
        )
        completed = [e for e in timeline if e.event_type == "task_completed"]
        assert len(completed) == 1
        assert "$" in completed[0].description

    def test_currency_passed_to_cost_record_descriptions(self) -> None:
        cost = _make_cost_record(cost=0.05)
        timeline = merge_activity_timeline(
            lifecycle_events=(),
            task_metrics=(),
            cost_records=(cost,),
            currency="GBP",
        )
        assert len(timeline) == 1
        assert "\u00a3" in timeline[0].description

    def test_identical_timestamps_stable_sort(self) -> None:
        ts = _NOW - timedelta(days=1)
        hired = _make_lifecycle_event(
            event_type=LifecycleEventType.HIRED,
            timestamp=ts,
        )
        task = _make_task_metric(completed_at=ts, task_id="task-same-ts")

        timeline = merge_activity_timeline(
            lifecycle_events=(hired,),
            task_metrics=(task,),
        )

        assert len(timeline) == 2
        # Stable sort: lifecycle events appear before task metrics at same timestamp
        assert timeline[0].event_type == "hired"
        assert timeline[1].event_type == "task_completed"

    def test_related_ids_populated(self) -> None:
        hired = _make_lifecycle_event(agent_id="agent-001")
        task = _make_task_metric(agent_id="agent-001", task_id="task-x")

        timeline = merge_activity_timeline(
            lifecycle_events=(hired,),
            task_metrics=(task,),
        )

        assert timeline[0].related_ids["agent_id"] == "agent-001"
        assert timeline[1].related_ids["agent_id"] == "agent-001"
        assert timeline[1].related_ids["task_id"] == "task-x"

    def test_backward_compat_positional_only(self) -> None:
        """Calling with only positional args still works."""
        hired = _make_lifecycle_event(timestamp=_NOW - timedelta(days=1))
        task = _make_task_metric(completed_at=_NOW)

        timeline = merge_activity_timeline((hired,), (task,))

        assert len(timeline) == 2


# ── task_started tests ────────────────────────────────────────────


@pytest.mark.unit
class TestTaskStartedEvents:
    def test_task_started_event_generated(self) -> None:
        started = _NOW - timedelta(hours=2)
        record = _make_task_metric(
            started_at=started,
            completed_at=_NOW,
            task_id="task-abc",
        )

        timeline = merge_activity_timeline((), (record,))

        types = [e.event_type for e in timeline]
        assert "task_started" in types
        started_evt = next(e for e in timeline if e.event_type == "task_started")
        assert started_evt.timestamp == started
        assert started_evt.related_ids["task_id"] == "task-abc"
        assert started_evt.related_ids["agent_id"] == "agent-001"
        assert "started" in started_evt.description

    def test_no_task_started_without_started_at(self) -> None:
        record = _make_task_metric(started_at=None)

        timeline = merge_activity_timeline((), (record,))

        types = [e.event_type for e in timeline]
        assert "task_started" not in types
        assert len(timeline) == 1
        assert timeline[0].event_type == "task_completed"

    def test_task_started_and_completed_from_same_record(self) -> None:
        started = _NOW - timedelta(hours=1)
        completed = _NOW
        record = _make_task_metric(
            started_at=started,
            completed_at=completed,
            task_id="task-both",
        )

        timeline = merge_activity_timeline((), (record,))

        assert len(timeline) == 2
        # completed is more recent
        assert timeline[0].event_type == "task_completed"
        assert timeline[0].timestamp == completed
        assert timeline[1].event_type == "task_started"
        assert timeline[1].timestamp == started

    def test_merge_ordering_with_task_started(self) -> None:
        started = _NOW - timedelta(hours=3)
        hired = _make_lifecycle_event(
            timestamp=_NOW - timedelta(hours=2),
        )
        record = _make_task_metric(
            started_at=started,
            completed_at=_NOW - timedelta(hours=1),
        )

        timeline = merge_activity_timeline((hired,), (record,))

        # Most recent first: task_completed, hired, task_started
        assert timeline[0].event_type == "task_completed"
        assert timeline[1].event_type == "hired"
        assert timeline[2].event_type == "task_started"


# ── cost_incurred tests ──────────────────────────────────────────


@pytest.mark.unit
class TestCostIncurredEvents:
    def test_cost_incurred_event(self) -> None:
        record = _make_cost_record(
            model="test-medium-001",
            input_tokens=500,
            output_tokens=100,
            cost=0.0025,
            timestamp=_NOW,
        )

        timeline = merge_activity_timeline(
            (),
            (),
            cost_records=(record,),
        )

        assert len(timeline) == 1
        evt = timeline[0]
        assert evt.event_type == "cost_incurred"
        assert evt.timestamp == _NOW
        assert "test-medium-001" in evt.description
        assert "500+100 tokens" in evt.description
        assert "$0.0025" in evt.description
        assert evt.related_ids["agent_id"] == "agent-001"
        assert evt.related_ids["task_id"] == "task-001"

    def test_merge_with_cost_records(self) -> None:
        hired = _make_lifecycle_event(
            timestamp=_NOW - timedelta(hours=2),
        )
        cost = _make_cost_record(
            timestamp=_NOW - timedelta(hours=1),
        )

        timeline = merge_activity_timeline(
            (hired,),
            (),
            cost_records=(cost,),
        )

        assert len(timeline) == 2
        assert timeline[0].event_type == "cost_incurred"
        assert timeline[1].event_type == "hired"

    def test_empty_cost_records(self) -> None:
        timeline = merge_activity_timeline((), (), cost_records=())
        assert timeline == ()


# ── tool_used tests ──────────────────────────────────────────────


@pytest.mark.unit
class TestToolUsedEvents:
    def test_tool_used_success_event(self) -> None:
        record = _make_tool_invocation(
            tool_name="read_file",
            is_success=True,
            timestamp=_NOW,
        )

        timeline = merge_activity_timeline(
            (),
            (),
            tool_invocations=(record,),
        )

        assert len(timeline) == 1
        evt = timeline[0]
        assert evt.event_type == "tool_used"
        assert evt.timestamp == _NOW
        assert "read_file" in evt.description
        assert "successfully" in evt.description
        assert evt.related_ids["agent_id"] == "agent-001"
        assert evt.related_ids["task_id"] == "task-001"

    def test_tool_used_failure_event(self) -> None:
        record = _make_tool_invocation(
            tool_name="write_file",
            is_success=False,
            error_message="Permission denied",
            timestamp=_NOW,
        )

        timeline = merge_activity_timeline(
            (),
            (),
            tool_invocations=(record,),
        )

        evt = timeline[0]
        assert evt.event_type == "tool_used"
        assert "write_file" in evt.description
        assert "failed" in evt.description
        # Error message NOT in description (avoid leaking internals)
        assert "Permission denied" not in evt.description

    def test_tool_used_failure_no_error_message(self) -> None:
        record = _make_tool_invocation(
            tool_name="exec_cmd",
            is_success=False,
            error_message=None,
            timestamp=_NOW,
        )

        timeline = merge_activity_timeline(
            (),
            (),
            tool_invocations=(record,),
        )

        evt = timeline[0]
        assert "exec_cmd" in evt.description
        assert "failed" in evt.description
        assert "None" not in evt.description

    def test_tool_used_no_task_id(self) -> None:
        record = _make_tool_invocation(task_id=None)

        timeline = merge_activity_timeline(
            (),
            (),
            tool_invocations=(record,),
        )

        evt = timeline[0]
        assert "task_id" not in evt.related_ids
        assert evt.related_ids["agent_id"] == "agent-001"

    def test_merge_with_tool_invocations(self) -> None:
        task = _make_task_metric(
            completed_at=_NOW - timedelta(hours=1),
        )
        tool = _make_tool_invocation(
            timestamp=_NOW - timedelta(hours=2),
        )

        timeline = merge_activity_timeline(
            (),
            (task,),
            tool_invocations=(tool,),
        )

        assert len(timeline) == 2
        assert timeline[0].event_type == "task_completed"
        assert timeline[1].event_type == "tool_used"


# ── delegation_sent / delegation_received tests ──────────────────


@pytest.mark.unit
class TestDelegationEvents:
    def test_delegation_sent_event(self) -> None:
        record = _make_delegation_record(
            delegator_id="agent-manager",
            delegatee_id="agent-worker",
            original_task_id="task-parent",
            timestamp=_NOW,
        )

        timeline = merge_activity_timeline(
            (),
            (),
            delegation_records_sent=(record,),
        )

        assert len(timeline) == 1
        evt = timeline[0]
        assert evt.event_type == "delegation_sent"
        assert evt.timestamp == _NOW
        assert "task-parent" in evt.description
        assert "agent-worker" in evt.description
        assert evt.related_ids["agent_id"] == "agent-manager"
        assert evt.related_ids["delegatee_id"] == "agent-worker"
        assert evt.related_ids["delegation_id"] == "del-001"
        assert evt.related_ids["original_task_id"] == "task-parent"
        assert evt.related_ids["delegated_task_id"] == "del-abc123"

    def test_delegation_received_event(self) -> None:
        record = _make_delegation_record(
            delegator_id="agent-manager",
            delegatee_id="agent-worker",
            original_task_id="task-parent",
            timestamp=_NOW,
        )

        timeline = merge_activity_timeline(
            (),
            (),
            delegation_records_received=(record,),
        )

        assert len(timeline) == 1
        evt = timeline[0]
        assert evt.event_type == "delegation_received"
        assert evt.timestamp == _NOW
        assert "task-parent" in evt.description
        assert "agent-manager" in evt.description
        assert evt.related_ids["agent_id"] == "agent-worker"
        assert evt.related_ids["delegator_id"] == "agent-manager"
        assert evt.related_ids["delegation_id"] == "del-001"
        assert evt.related_ids["delegated_task_id"] == "del-abc123"

    def test_delegation_dual_perspective(self) -> None:
        """Same record produces both sent and received events."""
        record = _make_delegation_record(timestamp=_NOW)

        timeline = merge_activity_timeline(
            (),
            (),
            delegation_records_sent=(record,),
            delegation_records_received=(record,),
        )

        types = {e.event_type for e in timeline}
        assert types == {"delegation_sent", "delegation_received"}

    def test_merge_all_event_types(self) -> None:
        """Verify all event types merge and sort correctly."""
        hired = _make_lifecycle_event(
            timestamp=_NOW - timedelta(hours=6),
        )
        task = _make_task_metric(
            started_at=_NOW - timedelta(hours=5),
            completed_at=_NOW - timedelta(hours=3),
        )
        cost = _make_cost_record(
            timestamp=_NOW - timedelta(hours=4),
        )
        tool = _make_tool_invocation(
            timestamp=_NOW - timedelta(hours=2),
        )
        delegation = _make_delegation_record(
            timestamp=_NOW - timedelta(hours=1),
        )

        timeline = merge_activity_timeline(
            (hired,),
            (task,),
            cost_records=(cost,),
            tool_invocations=(tool,),
            delegation_records_sent=(delegation,),
            delegation_records_received=(delegation,),
        )

        # 1 hired + 1 task_completed + 1 task_started + 1 cost + 1 tool + 2 delegation
        assert len(timeline) == 7
        # Verify desc order by timestamp
        for i in range(len(timeline) - 1):
            assert timeline[i].timestamp >= timeline[i + 1].timestamp


# ── Career events tests (unchanged) ─────────────────────────────


@pytest.mark.unit
class TestFilterCareerEvents:
    def test_filters_to_career_types(self) -> None:
        hired = _make_lifecycle_event(
            event_type=LifecycleEventType.HIRED,
            timestamp=_NOW - timedelta(days=30),
            details="Hired",
        )
        status_changed = _make_lifecycle_event(
            event_type=LifecycleEventType.STATUS_CHANGED,
            timestamp=_NOW - timedelta(days=20),
            details="Status changed",
        )
        promoted = _make_lifecycle_event(
            event_type=LifecycleEventType.PROMOTED,
            timestamp=_NOW - timedelta(days=10),
            details="Promoted",
        )

        career = filter_career_events((hired, status_changed, promoted))

        assert len(career) == 2
        assert career[0].event_type == "hired"
        assert career[1].event_type == "promoted"

    def test_chronological_order(self) -> None:
        promoted = _make_lifecycle_event(
            event_type=LifecycleEventType.PROMOTED,
            timestamp=_NOW - timedelta(days=1),
        )
        hired = _make_lifecycle_event(
            event_type=LifecycleEventType.HIRED,
            timestamp=_NOW - timedelta(days=30),
        )

        career = filter_career_events((promoted, hired))

        # Sorted ascending by timestamp
        assert career[0].event_type == "hired"
        assert career[1].event_type == "promoted"

    def test_empty_input(self) -> None:
        assert filter_career_events(()) == ()

    def test_no_career_events(self) -> None:
        status_changed = _make_lifecycle_event(
            event_type=LifecycleEventType.STATUS_CHANGED,
        )
        offboarded = _make_lifecycle_event(
            event_type=LifecycleEventType.OFFBOARDED,
        )

        career = filter_career_events((status_changed, offboarded))

        assert career == ()

    def test_all_career_types_included(self) -> None:
        events = tuple(
            _make_lifecycle_event(
                event_type=et,
                timestamp=_NOW - timedelta(days=i),
            )
            for i, et in enumerate(
                [
                    LifecycleEventType.HIRED,
                    LifecycleEventType.ONBOARDED,
                    LifecycleEventType.PROMOTED,
                    LifecycleEventType.DEMOTED,
                    LifecycleEventType.FIRED,
                ]
            )
        )

        career = filter_career_events(events)

        assert len(career) == 5
        types = {c.event_type for c in career}
        assert types == {"hired", "onboarded", "promoted", "demoted", "fired"}

    def test_empty_details_produces_fallback_description(self) -> None:
        hired = _make_lifecycle_event(
            event_type=LifecycleEventType.HIRED,
            details="",
        )

        career = filter_career_events((hired,))

        assert career[0].description == "Agent hired"

    def test_metadata_and_initiated_by_preserved(self) -> None:
        hired = _make_lifecycle_event(
            event_type=LifecycleEventType.HIRED,
            initiated_by="ceo",
            details="Hired as developer",
            metadata={"reason": "expansion", "team": "backend"},
        )

        career = filter_career_events((hired,))

        assert career[0].initiated_by == "ceo"
        assert career[0].description == "Hired as developer"
        assert career[0].metadata == {"reason": "expansion", "team": "backend"}


@pytest.mark.unit
class TestActivityEventTypeSuperset:
    """ActivityEventType must include every LifecycleEventType value."""

    def test_lifecycle_values_are_subset(self) -> None:
        lifecycle_values = {e.value for e in LifecycleEventType}
        activity_values = {e.value for e in ActivityEventType}
        missing = lifecycle_values - activity_values
        assert not missing, (
            f"LifecycleEventType values missing from ActivityEventType: {missing}"
        )


@pytest.mark.unit
class TestRedactCostEvents:
    def test_redacts_model_and_cost_from_description(self) -> None:
        event = ActivityEvent(
            event_type=ActivityEventType.COST_INCURRED,
            timestamp=_NOW,
            description="API call to test-medium-001 (500+100 tokens, EUR0.01)",
        )
        result = redact_cost_events((event,))
        assert len(result) == 1
        assert result[0].description == "API call (500+100 tokens)"

    def test_leaves_non_cost_events_unchanged(self) -> None:
        hired = ActivityEvent(
            event_type=ActivityEventType.HIRED,
            timestamp=_NOW,
            description="Agent hired",
        )
        tool = ActivityEvent(
            event_type=ActivityEventType.TOOL_USED,
            timestamp=_NOW,
            description="Tool read_file executed successfully",
        )
        result = redact_cost_events((hired, tool))
        assert result[0].description == "Agent hired"
        assert result[1].description == "Tool read_file executed successfully"

    def test_empty_timeline(self) -> None:
        assert redact_cost_events(()) == ()

    def test_preserves_other_fields(self) -> None:
        event = ActivityEvent(
            event_type=ActivityEventType.COST_INCURRED,
            timestamp=_NOW,
            description="API call to test-small-001 (200+50 tokens, $0.005)",
            related_ids={"agent_id": "agent-1", "task_id": "task-1"},
        )
        result = redact_cost_events((event,))
        assert result[0].event_type == "cost_incurred"
        assert result[0].timestamp == _NOW
        assert result[0].related_ids == {"agent_id": "agent-1", "task_id": "task-1"}

    def test_non_matching_description_uses_fallback(self) -> None:
        """If the description format doesn't match the regex, use fallback."""
        event = ActivityEvent(
            event_type=ActivityEventType.COST_INCURRED,
            timestamp=_NOW,
            description="Custom cost event description",
        )
        result = redact_cost_events((event,))
        assert result[0].description == "API call (details redacted)"

    def test_round_trip_with_real_cost_record(self) -> None:
        """Regex matches the actual format produced by _cost_record_to_activity."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test-provider",
            model="test-medium-001",
            input_tokens=500,
            output_tokens=100,
            cost=0.005,
            currency="USD",
            timestamp=_NOW,
        )
        event = _cost_record_to_activity(record, currency="EUR")
        result = redact_cost_events((event,))
        assert "test-medium-001" not in result[0].description
        assert "500+100 tokens" in result[0].description

    def test_mixed_timeline_preserves_order(self) -> None:
        """Redaction preserves event ordering in a mixed timeline."""
        events = (
            ActivityEvent(
                event_type=ActivityEventType.HIRED,
                timestamp=_NOW,
                description="Agent hired",
            ),
            ActivityEvent(
                event_type=ActivityEventType.COST_INCURRED,
                timestamp=_NOW,
                description="API call to test-model (100+50 tokens, EUR0.01)",
            ),
            ActivityEvent(
                event_type=ActivityEventType.TOOL_USED,
                timestamp=_NOW,
                description="Tool read_file executed successfully",
            ),
            ActivityEvent(
                event_type=ActivityEventType.COST_INCURRED,
                timestamp=_NOW,
                description="API call to test-model-2 (200+100 tokens, EUR0.02)",
            ),
            ActivityEvent(
                event_type=ActivityEventType.DELEGATION_SENT,
                timestamp=_NOW,
                description="Delegated task t1 to a2",
            ),
        )
        result = redact_cost_events(events)
        assert len(result) == 5
        assert result[0].description == "Agent hired"
        assert result[1].description == "API call (100+50 tokens)"
        assert result[2].description == "Tool read_file executed successfully"
        assert result[3].description == "API call (200+100 tokens)"
        assert result[4].description == "Delegated task t1 to a2"
