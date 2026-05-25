# mypy: disable-error-code="explicit-any,explicit-override"
"""Unit tests for :class:`ActivityFeedService`."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.communication.delegation.models import DelegationRecord
from synthorg.core.enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr import activity_service as activity_service_module
from synthorg.hr.activity_service import ActivityFeedService
from synthorg.hr.enums import ActivityEventType, LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.tools.invocation_record import ToolInvocationRecord

pytestmark = pytest.mark.unit

# Fixed "now" used across the module so the service's window calc is
# deterministic relative to test data. ``_FROZEN_NOW`` is one hour
# before the module's pinned wall-clock anchor to mirror the real
# service behaviour ("events after ``now`` cannot exist"); the
# ``freeze_activity_clock`` fixture patches ``datetime.now`` on the
# service module so ``get_agent_activity`` computes ``since`` against
# the same anchor.
_FROZEN_WALL = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
_NOW = _FROZEN_WALL - timedelta(hours=1)
_AGENT_ID = "agent-alice"


class _FrozenDatetime(datetime):
    """``datetime`` subclass whose ``.now`` returns ``_FROZEN_WALL``."""

    @classmethod
    def now(cls, tz: Any = None) -> _FrozenDatetime:
        # Cast to the subclass so mypy treats the return value as
        # ``_FrozenDatetime`` (matching the supertype covariance rule).
        base = _FROZEN_WALL if tz is None else _FROZEN_WALL.astimezone(tz)
        return cls(
            base.year,
            base.month,
            base.day,
            base.hour,
            base.minute,
            base.second,
            base.microsecond,
            tzinfo=base.tzinfo,
        )


@pytest.fixture(autouse=True)
def freeze_activity_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``datetime.now`` on the service module to ``_FROZEN_WALL``.

    The service imports ``datetime`` directly (``from datetime import
    ... datetime``), so patching the imported name is sufficient.
    """
    monkeypatch.setattr(activity_service_module, "datetime", _FrozenDatetime)


def _lifecycle(
    event_type: LifecycleEventType = LifecycleEventType.HIRED,
    *,
    offset_minutes: int = 0,
    event_id: str = "evt-1",
) -> AgentLifecycleEvent:
    return AgentLifecycleEvent(
        id=NotBlankStr(event_id),
        agent_id=NotBlankStr(_AGENT_ID),
        agent_name=NotBlankStr("alice"),
        event_type=event_type,
        timestamp=_NOW - timedelta(minutes=offset_minutes),
        initiated_by=NotBlankStr("system"),
        details=f"Agent {event_type.value}",
        metadata={},
    )


def _task_metric(
    offset_minutes: int = 0,
    task_id: str = "task-1",
    is_success: bool = True,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        task_id=NotBlankStr(task_id),
        agent_id=NotBlankStr(_AGENT_ID),
        task_type=TaskType.DEVELOPMENT,
        started_at=_NOW - timedelta(minutes=offset_minutes + 1),
        completed_at=_NOW - timedelta(minutes=offset_minutes),
        duration_seconds=60.0,
        is_success=is_success,
        cost=0.01,
        currency="USD",
        turns_used=1,
        tokens_used=100,
        complexity=Complexity.MEDIUM,
    )


def _cost_record(offset_minutes: int = 0, task_id: str = "task-cost") -> CostRecord:
    return CostRecord(
        agent_id=NotBlankStr(_AGENT_ID),
        task_id=NotBlankStr(task_id),
        provider=NotBlankStr("test-provider"),
        model=NotBlankStr("test-small-001"),
        input_tokens=10,
        output_tokens=20,
        cost=0.02,
        currency="USD",
        timestamp=_NOW - timedelta(minutes=offset_minutes),
    )


def _tool_record(
    offset_minutes: int = 0, record_id: str = "inv-1"
) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        id=NotBlankStr(record_id),
        agent_id=NotBlankStr(_AGENT_ID),
        tool_name=NotBlankStr("test_tool"),
        is_success=True,
        timestamp=_NOW - timedelta(minutes=offset_minutes),
        task_id=None,
    )


def _delegation(
    offset_minutes: int = 0,
    delegator_first: bool = True,
    delegation_id: str = "del-1",
) -> DelegationRecord:
    delegator = _AGENT_ID if delegator_first else "other-agent"
    delegatee = "other-agent" if delegator_first else _AGENT_ID
    return DelegationRecord(
        delegation_id=NotBlankStr(delegation_id),
        delegator_id=NotBlankStr(delegator),
        delegatee_id=NotBlankStr(delegatee),
        original_task_id=NotBlankStr("task-original"),
        delegated_task_id=NotBlankStr("task-delegated"),
        timestamp=_NOW - timedelta(minutes=offset_minutes),
    )


class _FakeLifecycleRepo:
    def __init__(self, events: list[AgentLifecycleEvent]) -> None:
        self._events = events
        self.calls: list[dict[str, object]] = []

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        event_type: LifecycleEventType | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[AgentLifecycleEvent, ...]:
        self.calls.append(
            {"agent_id": agent_id, "since": since, "limit": limit},
        )
        out = [
            e
            for e in self._events
            if (agent_id is None or str(e.agent_id) == agent_id)
            and (since is None or e.timestamp >= since)
        ]
        if limit is not None:
            out = out[:limit]
        return tuple(out)


class _FakePerformanceTracker:
    def __init__(self, metrics: list[TaskMetricRecord]) -> None:
        self._metrics = metrics

    def get_task_metrics(
        self,
        *,
        agent_id: str | None,
        since: datetime,
        until: datetime,
    ) -> tuple[TaskMetricRecord, ...]:
        return tuple(
            m
            for m in self._metrics
            if (agent_id is None or str(m.agent_id) == agent_id)
            and since <= m.completed_at <= until
        )


class _FakeCostTracker:
    def __init__(self, records: list[CostRecord]) -> None:
        self._records = records

    async def get_records(
        self,
        *,
        agent_id: str | None,
        start: datetime,
        end: datetime,
    ) -> tuple[CostRecord, ...]:
        return tuple(
            r
            for r in self._records
            if (agent_id is None or str(r.agent_id) == agent_id)
            and start <= r.timestamp <= end
        )


class _FakeToolTracker:
    def __init__(self, records: list[ToolInvocationRecord]) -> None:
        self._records = records

    async def get_records(
        self,
        *,
        agent_id: str | None,
        start: datetime,
        end: datetime,
    ) -> tuple[ToolInvocationRecord, ...]:
        return tuple(
            r
            for r in self._records
            if (agent_id is None or str(r.agent_id) == agent_id)
            and start <= r.timestamp <= end
        )


class _FakeDelegationStore:
    def __init__(
        self,
        sent: list[DelegationRecord] | None = None,
        received: list[DelegationRecord] | None = None,
    ) -> None:
        self._sent = sent or []
        self._received = received or []

    async def get_records_as_delegator(
        self,
        agent_id: str,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[DelegationRecord, ...]:
        # Enforce the query contract: only records for *agent_id*
        # (as the delegator) within ``[start, end]``. This makes
        # the fake catch bugs where ``ActivityFeedService`` forwards
        # the wrong identifier or window.
        return tuple(
            r
            for r in self._sent
            if str(r.delegator_id) == agent_id and start <= r.timestamp <= end
        )

    async def get_records_as_delegatee(
        self,
        agent_id: str,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[DelegationRecord, ...]:
        return tuple(
            r
            for r in self._received
            if str(r.delegatee_id) == agent_id and start <= r.timestamp <= end
        )


class TestGetAgentActivity:
    """Merge + pagination + optional sources."""

    async def test_merges_required_sources(self) -> None:
        service = ActivityFeedService(
            performance_tracker=_FakePerformanceTracker(  # type: ignore[arg-type]
                [_task_metric(offset_minutes=30)],
            ),
            lifecycle_repo=_FakeLifecycleRepo(  # type: ignore[arg-type]
                [_lifecycle(offset_minutes=60)],
            ),
        )

        page, total = await service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=0,
            limit=50,
        )

        # Lifecycle + task_started + task_completed = 3 events.
        assert total == 3
        assert len(page) == 3
        event_types = {e.event_type for e in page}
        assert ActivityEventType.HIRED in event_types
        assert ActivityEventType.TASK_COMPLETED in event_types
        assert ActivityEventType.TASK_STARTED in event_types

    async def test_includes_optional_sources_when_present(self) -> None:
        cost_fake = _FakeCostTracker([_cost_record(offset_minutes=10)])
        tool_fake = _FakeToolTracker([_tool_record(offset_minutes=20)])
        del_fake = _FakeDelegationStore(
            sent=[_delegation(offset_minutes=5, delegator_first=True)],
        )
        service = ActivityFeedService(
            performance_tracker=_FakePerformanceTracker([]),  # type: ignore[arg-type]
            lifecycle_repo=_FakeLifecycleRepo([]),  # type: ignore[arg-type]
            cost_tracker=cost_fake,  # type: ignore[arg-type]
            tool_invocation_tracker=tool_fake,  # type: ignore[arg-type]
            delegation_store=del_fake,  # type: ignore[arg-type]
        )

        page, total = await service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=0,
            limit=50,
        )

        types = {e.event_type for e in page}
        assert total == 3, f"total={total}, types={types}"
        assert ActivityEventType.COST_INCURRED in types
        assert ActivityEventType.TOOL_USED in types
        assert ActivityEventType.DELEGATION_SENT in types

    async def test_paginates_newest_first(self) -> None:
        events = [
            _lifecycle(offset_minutes=m, event_id=f"evt-{m}")
            for m in (5, 10, 15, 20, 25)
        ]
        service = ActivityFeedService(
            performance_tracker=_FakePerformanceTracker([]),  # type: ignore[arg-type]
            lifecycle_repo=_FakeLifecycleRepo(events),  # type: ignore[arg-type]
        )

        page, total = await service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=1,
            limit=2,
        )

        assert total == 5
        assert len(page) == 2
        # Events are newest-first; slice is contiguous in descending
        # timestamp order.
        assert page[0].timestamp > page[1].timestamp

    async def test_window_filters_old_events(self) -> None:
        # 10h old vs 200h old -- window 24h should drop the latter.
        events = [
            _lifecycle(offset_minutes=10 * 60, event_id="recent"),
            _lifecycle(
                event_type=LifecycleEventType.PROMOTED,
                offset_minutes=200 * 60,
                event_id="ancient",
            ),
        ]
        service = ActivityFeedService(
            performance_tracker=_FakePerformanceTracker([]),  # type: ignore[arg-type]
            lifecycle_repo=_FakeLifecycleRepo(events),  # type: ignore[arg-type]
        )

        page, total = await service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=0,
            limit=50,
            window_hours=24,
        )

        assert total == 1
        assert page[0].event_type == ActivityEventType.HIRED

    async def test_window_boundary_inclusive(self) -> None:
        """Events that should be inside the window are not dropped.

        ``_NOW`` is anchored at ``datetime.now(UTC) - 1h``, so an event
        at offset N minutes is ``wall_now - 1h - N*min`` in real time.
        The service computes ``since = datetime.now(UTC) - window``.
        With ``window_hours=24`` every event under ~22h old is inside.
        """
        events = [
            _lifecycle(offset_minutes=10, event_id="recent"),
            _lifecycle(
                event_type=LifecycleEventType.PROMOTED,
                offset_minutes=120,
                event_id="middle",
            ),
        ]
        service = ActivityFeedService(
            performance_tracker=_FakePerformanceTracker([]),  # type: ignore[arg-type]
            lifecycle_repo=_FakeLifecycleRepo(events),  # type: ignore[arg-type]
        )

        _page, total = await service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=0,
            limit=50,
            window_hours=24,
        )

        # With a 24h window both events (both under ~3.5h old) are
        # inside -- the boundary is not dropping valid events.
        assert total == 2

    async def test_no_sources_returns_empty(self) -> None:
        service = ActivityFeedService(
            performance_tracker=_FakePerformanceTracker([]),  # type: ignore[arg-type]
            lifecycle_repo=_FakeLifecycleRepo([]),  # type: ignore[arg-type]
        )

        page, total = await service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=0,
            limit=50,
        )

        assert total == 0
        assert page == ()


class TestRequestValidation:
    """Pagination + window_hours boundary validation.

    Each invalid input must raise ``ValueError`` with a descriptive
    message AND emit ``HR_ACTIVITY_INVALID_REQUEST`` at WARNING with
    structured context, so bad MCP requests are visible in the audit
    trail instead of surfacing silently as handler-level errors.
    """

    @pytest.fixture
    def empty_service(self) -> ActivityFeedService:
        return ActivityFeedService(
            performance_tracker=_FakePerformanceTracker([]),  # type: ignore[arg-type]
            lifecycle_repo=_FakeLifecycleRepo([]),  # type: ignore[arg-type]
        )

    async def test_negative_offset_rejected(
        self,
        empty_service: ActivityFeedService,
    ) -> None:
        with pytest.raises(ValueError, match="offset must be >= 0"):
            await empty_service.get_agent_activity(
                NotBlankStr(_AGENT_ID),
                offset=-1,
                limit=50,
            )

    async def test_zero_limit_rejected(
        self,
        empty_service: ActivityFeedService,
    ) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            await empty_service.get_agent_activity(
                NotBlankStr(_AGENT_ID),
                offset=0,
                limit=0,
            )

    @pytest.mark.parametrize(
        "window_hours",
        [0, -1, 721, 1_000_000],
        ids=["zero", "negative", "just_over_cap", "absurdly_large"],
    )
    async def test_window_hours_out_of_range_rejected(
        self,
        empty_service: ActivityFeedService,
        window_hours: int,
    ) -> None:
        with pytest.raises(ValueError, match="window_hours must be between"):
            await empty_service.get_agent_activity(
                NotBlankStr(_AGENT_ID),
                offset=0,
                limit=50,
                window_hours=window_hours,
            )

    async def test_minimum_valid_boundaries_accepted(
        self,
        empty_service: ActivityFeedService,
    ) -> None:
        """offset=0, limit=1, window_hours=1 is the tightest valid input."""
        page, total = await empty_service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=0,
            limit=1,
            window_hours=1,
        )
        assert page == ()
        assert total == 0

    async def test_maximum_valid_window_accepted(
        self,
        empty_service: ActivityFeedService,
    ) -> None:
        """``window_hours=720`` (30d cap) is inclusive on the upper bound."""
        page, total = await empty_service.get_agent_activity(
            NotBlankStr(_AGENT_ID),
            offset=0,
            limit=50,
            window_hours=720,
        )
        assert page == ()
        assert total == 0
