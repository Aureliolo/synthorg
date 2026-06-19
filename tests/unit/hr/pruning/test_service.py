"""Tests for PruningService."""

from datetime import UTC, datetime, timedelta
from typing import override
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import FiringReason
from synthorg.hr.models import FiringRequest, OffboardingRecord
from synthorg.hr.offboarding_service import OffboardingService
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.pruning.models import PruningEvaluation, PruningServiceConfig
from synthorg.hr.pruning.service import PruningService
from synthorg.hr.registry import AgentRegistryService
from tests._shared import FakeClock, mock_of
from tests.unit.hr.conftest import make_agent_identity

from .conftest import (
    NOW,
    make_performance_snapshot,
)

# ── Fakes ────────────────────────────────────────────────────────


def _tracker(
    snapshots: dict[str, AgentPerformanceSnapshot] | None = None,
) -> PerformanceTracker:
    """Autospec ``PerformanceTracker`` returning pre-configured snapshots."""
    snaps = snapshots or {}

    async def _get(
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
    ) -> AgentPerformanceSnapshot:
        key = str(agent_id)
        if key in snaps:
            return snaps[key]
        return make_performance_snapshot(agent_id=key)

    tracker: PerformanceTracker = mock_of[PerformanceTracker](
        get_snapshot=AsyncMock(side_effect=_get)
    )
    return tracker


class FakeOffboardingService(OffboardingService):
    """Fake OffboardingService that records calls."""

    def __init__(self) -> None:
        # Test fake: the parent initialiser is intentionally skipped --
        # only ``offboard`` is exercised, so no real dependencies are wired.
        self.offboard_calls: list[FiringRequest] = []
        self.should_raise: Exception | None = None

    @override
    async def offboard(self, request: FiringRequest) -> OffboardingRecord:
        if self.should_raise is not None:
            raise self.should_raise
        self.offboard_calls.append(request)
        return OffboardingRecord(
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            firing_request_id=NotBlankStr(str(request.id)),
            tasks_reassigned=(),
            memory_archive_id=None,
            org_memories_promoted=0,
            team_notification_sent=True,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )


class AlwaysEligiblePolicy:
    """Test policy that always marks agents as eligible."""

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> PruningEvaluation:
        return PruningEvaluation(
            agent_id=agent_id,
            eligible=True,
            reasons=(NotBlankStr("always eligible"),),
            scores={"test": 1.0},
            policy_name=NotBlankStr("always-eligible"),
            snapshot=snapshot,
            evaluated_at=datetime.now(UTC),
        )


class NeverEligiblePolicy:
    """Test policy that never marks agents as eligible."""

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> PruningEvaluation:
        return PruningEvaluation(
            agent_id=agent_id,
            eligible=False,
            reasons=(),
            scores={},
            policy_name=NotBlankStr("never-eligible"),
            snapshot=snapshot,
            evaluated_at=datetime.now(UTC),
        )


class FailingPolicy:
    """Test policy that raises an exception."""

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> PruningEvaluation:
        msg = "Policy evaluation failed"
        raise RuntimeError(msg)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def registry() -> AgentRegistryService:
    return AgentRegistryService()


@pytest.fixture
def approval_store() -> ApprovalStore:
    return ApprovalStore(clock=FakeClock(start=NOW))


@pytest.fixture
def tracker() -> PerformanceTracker:
    return _tracker()


@pytest.fixture
def offboarding() -> FakeOffboardingService:
    return FakeOffboardingService()


def _make_service(  # noqa: PLR0913
    *,
    policies: tuple[object, ...] = (),
    registry: AgentRegistryService | None = None,
    tracker: PerformanceTracker | None = None,
    approval_store: ApprovalStore | None = None,
    offboarding: FakeOffboardingService | None = None,
    config: PruningServiceConfig | None = None,
    on_notification: object = None,
) -> PruningService:
    """Build a PruningService with test defaults."""
    return PruningService(
        policies=policies,  # type: ignore[arg-type]
        registry=registry or AgentRegistryService(),
        tracker=tracker or _tracker(),
        approval_store=approval_store or ApprovalStore(),
        offboarding_service=offboarding or FakeOffboardingService(),
        config=config,
        on_notification=on_notification,  # type: ignore[arg-type]
    )


# ── Initialization ───────────────────────────────────────────────


@pytest.mark.unit
class TestPruningServiceInit:
    """PruningService construction."""

    def test_initializes_with_required_dependencies(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        tracker: PerformanceTracker,
        offboarding: FakeOffboardingService,
    ) -> None:
        service = PruningService(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            tracker=tracker,
            approval_store=approval_store,
            offboarding_service=offboarding,
        )
        assert service is not None
        assert not service.is_running

    def test_respects_custom_config(self) -> None:
        config = PruningServiceConfig(
            evaluation_interval_seconds=120.0,
            max_approvals_per_cycle=3,
        )
        service = _make_service(config=config)
        assert service is not None


# ── Pruning Cycle ────────────────────────────────────────────────


@pytest.mark.unit
class TestPruningCycle:
    """PruningService.run_pruning_cycle tests."""

    async def test_cycle_with_no_active_agents(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.agents_evaluated == 0
        assert job.agents_eligible == 0
        assert job.approval_requests_created == 0

    async def test_cycle_evaluates_all_active_agents(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        tracker: PerformanceTracker,
    ) -> None:
        agent1 = make_agent_identity(name="agent-a")
        agent2 = make_agent_identity(name="agent-b")
        await registry.register(agent1)
        await registry.register(agent2)

        service = _make_service(
            policies=(NeverEligiblePolicy(),),
            registry=registry,
            tracker=tracker,
            approval_store=approval_store,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.agents_evaluated == 2
        assert job.agents_eligible == 0

    async def test_cycle_creates_approval_for_eligible_agents(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        tracker: PerformanceTracker,
    ) -> None:
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            tracker=tracker,
            approval_store=approval_store,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.agents_eligible == 1
        assert job.approval_requests_created == 1

        items = await approval_store.list_items(action_type="hr:prune")
        assert len(items) == 1
        assert items[0].risk_level == ApprovalRiskLevel.CRITICAL
        assert items[0].metadata["agent_id"] == str(agent.id)

    async def test_cycle_skips_ineligible_agents(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        agent = make_agent_identity(name="good-performer")
        await registry.register(agent)

        service = _make_service(
            policies=(NeverEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.agents_eligible == 0
        assert job.approval_requests_created == 0

        items = await approval_store.list_items(action_type="hr:prune")
        assert len(items) == 0

    async def test_cycle_respects_max_approvals_per_cycle(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        for i in range(5):
            agent = make_agent_identity(name=f"agent-{i}")
            await registry.register(agent)

        config = PruningServiceConfig(max_approvals_per_cycle=2)
        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            config=config,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.agents_eligible == 5
        assert job.approval_requests_created == 2

    async def test_cycle_deduplicates_pending_approvals(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
        )

        # The ``approval_store`` fixture injects ``FakeClock(NOW)`` so the
        # lazy-expiration check inside ``ApprovalStore`` sees the same time
        # as the pruning cycle; without a pinned clock real wall-clock may
        # exceed ``expires_at`` and silently expire the first approval.

        # First cycle creates approval.
        job1 = await service.run_pruning_cycle(now=NOW)
        assert job1.approval_requests_created == 1

        # Second cycle should skip (pending approval already exists).
        job2 = await service.run_pruning_cycle(now=NOW)
        assert job2.approval_requests_created == 0

        items = await approval_store.list_items(action_type="hr:prune")
        assert len(items) == 1

    async def test_cycle_aggregates_errors_without_stopping(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        """Error evaluating one agent does not stop the cycle."""
        agent1 = make_agent_identity(name="agent-a")
        agent2 = make_agent_identity(name="agent-b")
        await registry.register(agent1)
        await registry.register(agent2)

        # Use a tracker that fails for agent1 but succeeds for agent2.
        async def _partial_fail(
            agent_id: NotBlankStr,
            *,
            now: datetime | None = None,
        ) -> AgentPerformanceSnapshot:
            if str(agent_id) == str(agent1.id):
                msg = "Snapshot failed"
                raise RuntimeError(msg)
            return make_performance_snapshot(agent_id=str(agent_id))

        partial_tracker = mock_of[PerformanceTracker](
            get_snapshot=AsyncMock(side_effect=_partial_fail),
        )
        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            tracker=partial_tracker,
            approval_store=approval_store,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.agents_evaluated == 2
        assert len(job.errors) >= 1
        assert job.agents_eligible >= 1

    async def test_cycle_returns_accurate_job_run(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        agent = make_agent_identity(name="test-agent")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.run_at == NOW
        assert job.agents_evaluated == 1
        assert job.elapsed_seconds >= 0.0
        assert isinstance(job.job_id, UUID)

    async def test_first_eligible_policy_wins(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        """When multiple policies, first eligible result is used."""
        agent = make_agent_identity(name="test-agent")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(), NeverEligiblePolicy()),
            registry=registry,
            approval_store=approval_store,
        )
        job = await service.run_pruning_cycle(now=NOW)
        assert job.agents_eligible == 1

        items = await approval_store.list_items(action_type="hr:prune")
        assert items[0].metadata["policy_name"] == "always-eligible"


# ── Approval Processing ──────────────────────────────────────────


@pytest.mark.unit
class TestApprovalProcessing:
    """PruningService approval decision handling."""

    async def test_approved_agent_is_offboarded(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )

        # Run cycle to create approval.
        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")
        assert len(items) == 1

        # Simulate human approval.
        approved = items[0].model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
            },
        )
        await approval_store.save(approved)

        # Run another cycle -- should process the approval.
        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))

        assert len(offboarding.offboard_calls) == 1
        call = offboarding.offboard_calls[0]
        assert call.reason == FiringReason.PERFORMANCE
        assert str(call.agent_id) == str(agent.id)

    async def test_rejected_agent_is_not_offboarded(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )

        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")

        # Simulate rejection.
        rejected = items[0].model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
                "decision_reason": NotBlankStr("Agent showing improvement"),
            },
        )
        await approval_store.save(rejected)

        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))

        assert len(offboarding.offboard_calls) == 0

    async def test_approval_metadata_includes_required_fields(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        agent = make_agent_identity(name="test-agent")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
        )
        await service.run_pruning_cycle(now=NOW)

        items = await approval_store.list_items(action_type="hr:prune")
        assert len(items) == 1
        meta = items[0].metadata
        assert meta["agent_id"] == str(agent.id)
        assert meta["policy_name"] == "always-eligible"
        assert "pruning_request_id" in meta
        assert len(meta["pruning_request_id"]) > 0

    async def test_missing_agent_in_registry_after_approval(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        """If agent was removed between approval and processing."""
        agent = make_agent_identity(name="vanished-agent")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )

        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")

        # Simulate approval.
        approved = items[0].model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
            },
        )
        await approval_store.save(approved)

        # Remove agent from registry before processing.
        await registry.unregister(NotBlankStr(str(agent.id)))

        # Should not crash -- logs warning and skips.
        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))
        assert len(offboarding.offboard_calls) == 0

    async def test_notification_callback_invoked_on_offboard(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        callback = AsyncMock()

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
            on_notification=callback,
        )

        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")

        approved = items[0].model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
            },
        )
        await approval_store.save(approved)

        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))
        callback.assert_awaited_once()

    async def test_restart_does_not_reoffboard_processed_approval(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        """A fresh service over the same store must not re-offboard.

        The durable ``pruning_processed`` marker stamped into the
        approval metadata is the cross-restart dedup anchor; the
        post-restart service has an empty in-memory ``_processed`` set
        and would otherwise re-offboard the still-APPROVED item.
        """
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )
        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")
        approved = items[0].model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
            },
        )
        await approval_store.save(approved)

        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))
        assert len(offboarding.offboard_calls) == 1

        # The durable marker must have landed on the stored approval.
        stored = await approval_store.get(NotBlankStr(str(approved.id)))
        assert stored is not None
        assert stored.metadata.get("pruning_processed") == "true"

        # Simulate a restart: a fresh service has an empty in-memory
        # processed set but shares the durable approval store.
        restarted = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )
        await restarted.run_pruning_cycle(now=NOW + timedelta(hours=3))
        # No second offboard: the durable marker deduped it.
        assert len(offboarding.offboard_calls) == 1

    async def test_notification_callback_failure_does_not_crash(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        callback = AsyncMock(side_effect=RuntimeError("Notification failed"))

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
            on_notification=callback,
        )

        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")

        approved = items[0].model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
            },
        )
        await approval_store.save(approved)

        # Should not raise despite callback failure.
        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))
        assert len(offboarding.offboard_calls) == 1

    async def test_offboarding_failure_does_not_crash_cycle(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
    ) -> None:
        """Offboarding failure is non-fatal and retries on next cycle."""
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        offboarding = FakeOffboardingService()
        offboarding.should_raise = RuntimeError("Offboarding exploded")

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )

        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")

        approved = items[0].model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
            },
        )
        await approval_store.save(approved)

        # Should not crash despite offboarding failure.
        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))
        assert len(offboarding.offboard_calls) == 0

        # Clear transient error -- retry should succeed.
        offboarding.should_raise = None
        await service.run_pruning_cycle(now=NOW + timedelta(hours=3))
        assert len(offboarding.offboard_calls) == 1

    async def test_expired_approval_is_not_processed(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        """EXPIRED approvals are neither offboarded nor rejected."""
        agent = make_agent_identity(name="slow-approver")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )

        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")

        expired = items[0].model_copy(
            update={"status": ApprovalStatus.EXPIRED},
        )
        await approval_store.save(expired)

        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))
        assert len(offboarding.offboard_calls) == 0

    async def test_missing_agent_id_in_approval_metadata(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        """Approval with missing agent_id metadata is skipped safely."""
        from .conftest import make_approval_item

        bad_item = make_approval_item(
            status=ApprovalStatus.APPROVED,
            decided_at=NOW + timedelta(hours=1),
            decided_by="admin",
            metadata={"policy_name": "threshold"},
        )
        await approval_store.add(bad_item)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )

        # Should not crash -- logs error and skips.
        await service.run_pruning_cycle(now=NOW)
        assert len(offboarding.offboard_calls) == 0

    async def test_approved_agent_not_reprocessed_on_next_cycle(
        self,
        registry: AgentRegistryService,
        approval_store: ApprovalStore,
        offboarding: FakeOffboardingService,
    ) -> None:
        """Once an approval is processed, it should not be reprocessed."""
        agent = make_agent_identity(name="poor-performer")
        await registry.register(agent)

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=approval_store,
            offboarding=offboarding,
        )

        await service.run_pruning_cycle(now=NOW)
        items = await approval_store.list_items(action_type="hr:prune")

        approved = items[0].model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": NOW + timedelta(hours=1),
                "decided_by": NotBlankStr("admin"),
            },
        )
        await approval_store.save(approved)

        # First cycle processes the approval.
        await service.run_pruning_cycle(now=NOW + timedelta(hours=2))
        assert len(offboarding.offboard_calls) == 1

        # Second cycle should NOT reprocess the same approval.
        await service.run_pruning_cycle(now=NOW + timedelta(hours=3))
        assert len(offboarding.offboard_calls) == 1


# ── Scheduler Lifecycle ──────────────────────────────────────────


@pytest.mark.unit
class TestSchedulerLifecycle:
    """PruningService start/stop/is_running."""

    async def test_start_begins_background_task(self) -> None:
        service = _make_service(
            policies=(NeverEligiblePolicy(),),
            config=PruningServiceConfig(evaluation_interval_seconds=60.0),
        )
        await service.start()
        assert service.is_running
        await service.stop()
        assert not service.is_running

    async def test_double_start_is_idempotent(self) -> None:
        service = _make_service(
            policies=(NeverEligiblePolicy(),),
            config=PruningServiceConfig(evaluation_interval_seconds=60.0),
        )
        await service.start()
        await service.start()  # Should not raise or create a second task.
        assert service.is_running
        await service.stop()

    async def test_stop_when_not_running_is_noop(self) -> None:
        service = _make_service(policies=(NeverEligiblePolicy(),))
        assert not service.is_running
        await service.stop()  # Should not raise.
        assert not service.is_running

    async def test_wake_triggers_early_cycle(self) -> None:
        """Calling wake() triggers a cycle before the interval elapses."""
        import asyncio
        from unittest.mock import patch

        registry = AgentRegistryService()
        store = ApprovalStore()

        agent = make_agent_identity(name="test-agent")
        await registry.register(agent)

        cycle_executed = asyncio.Event()
        loop_waiting = asyncio.Event()

        service = _make_service(
            policies=(AlwaysEligiblePolicy(),),
            registry=registry,
            approval_store=store,
            config=PruningServiceConfig(evaluation_interval_seconds=3600.0),
        )

        original_wait_for = asyncio.wait_for

        async def patched_wait_for(
            coro: object,
            *,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            loop_waiting.set()
            return await original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        original_cycle = service.run_pruning_cycle

        async def patched_cycle(**kwargs: object) -> object:
            result = await original_cycle(**kwargs)  # type: ignore[arg-type]
            cycle_executed.set()
            return result

        service.run_pruning_cycle = patched_cycle  # type: ignore[assignment]

        with patch("asyncio.wait_for", side_effect=patched_wait_for):
            await service.start()
            await loop_waiting.wait()
            service.wake()

            try:
                await asyncio.wait_for(
                    cycle_executed.wait(),
                    timeout=5.0,
                )
            finally:
                await service.stop()

        items = await store.list_items(action_type="hr:prune")
        assert len(items) >= 1
