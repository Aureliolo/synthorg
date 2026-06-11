"""Concurrency regression tests for PruningService.

``_process_decided_approvals`` previously had a check-then-act race on
``_processed_approval_ids``: two concurrent cycles could both pass the
containment check and both invoke ``OffboardingService.offboard`` for the
same approval id. The fix claims the id under a lock before running
offboarding, so concurrent cycles must execute offboard at most once per
approval.
"""

import asyncio
from datetime import UTC, datetime
from typing import override
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.models import FiringRequest, OffboardingRecord
from synthorg.hr.offboarding_service import OffboardingService
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.pruning.service import PruningService
from synthorg.hr.registry import AgentRegistryService
from tests._shared import mock_of
from tests.unit.hr.conftest import make_agent_identity

from .conftest import make_approval_item, make_performance_snapshot


class _SlowOffboardingService(OffboardingService):
    """Offboarding stub that counts calls per agent and yields control."""

    def __init__(self) -> None:
        # Test fake: the parent initialiser is intentionally skipped --
        # only ``offboard`` is exercised, so no real dependencies are wired.
        self.calls: list[str] = []
        self._started = asyncio.Event()
        self._release = asyncio.Event()

    def release(self) -> None:
        self._release.set()

    async def wait_started(self) -> None:
        await self._started.wait()

    @override
    async def offboard(self, request: FiringRequest) -> OffboardingRecord:
        self.calls.append(str(request.agent_id))
        self._started.set()
        await self._release.wait()
        now = datetime.now(UTC)
        return OffboardingRecord(
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            firing_request_id=NotBlankStr(str(request.id)),
            tasks_reassigned=(),
            memory_archive_id=None,
            org_memories_promoted=0,
            team_notification_sent=True,
            started_at=now,
            completed_at=now,
        )


def _tracker() -> PerformanceTracker:
    """Autospec ``PerformanceTracker`` returning a snapshot per agent id."""

    async def _get(
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
    ) -> AgentPerformanceSnapshot:
        return make_performance_snapshot(agent_id=str(agent_id))

    tracker: PerformanceTracker = mock_of[PerformanceTracker](
        get_snapshot=AsyncMock(side_effect=_get)
    )
    return tracker


@pytest.mark.unit
class TestProcessDecidedApprovalsConcurrency:
    """Two concurrent cycles must not double-offboard the same agent."""

    async def test_concurrent_process_decided_approvals_no_double_offboard(
        self,
    ) -> None:
        registry = AgentRegistryService()
        agent = make_agent_identity(name="target-agent")
        await registry.register(agent)

        approval_store = ApprovalStore()
        approval = make_approval_item(
            approval_id="approval-conc-001",
            status=ApprovalStatus.APPROVED,
            decided_at=datetime.now(UTC),
            decided_by="ceo",
            metadata={
                "agent_id": str(agent.id),
                "policy_name": "threshold",
                "reason_summary": "test",
                "pruning_request_id": "req-001",
            },
        )
        await approval_store.add(approval)

        offboarding = _SlowOffboardingService()
        service = PruningService(
            policies=(),
            registry=registry,
            tracker=_tracker(),
            approval_store=approval_store,
            offboarding_service=offboarding,
        )

        # Fire two concurrent cycles. First one should start offboarding
        # while the second one observes the claim and skips.
        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(service._process_decided_approvals())
            await offboarding.wait_started()
            # Task A is parked inside ``OffboardingService.offboard`` with
            # the approval id held in ``_in_flight_approvals`` (and the
            # outer ``_processing_lock`` released).  Awaiting the second
            # cycle directly is deterministic: B acquires the lock,
            # observes the claim, skips, and returns before we unblock A.
            await service._process_decided_approvals()
            offboarding.release()

        assert offboarding.calls == [str(agent.id)], (
            f"offboard must run exactly once per approval; got {offboarding.calls}"
        )

    async def test_handler_exception_releases_claim_so_next_cycle_retries(
        self,
    ) -> None:
        """If _handle_approved raises, the in-flight claim must be released.

        The next cycle must then be able to re-claim the same approval id
        rather than deadlocking on a leaked in-flight marker.
        """
        registry = AgentRegistryService()
        agent = make_agent_identity(name="retry-agent")
        await registry.register(agent)

        approval_store = ApprovalStore()
        approval = make_approval_item(
            approval_id="approval-retry-001",
            status=ApprovalStatus.APPROVED,
            decided_at=datetime.now(UTC),
            decided_by="ceo",
            metadata={
                "agent_id": str(agent.id),
                "policy_name": "threshold",
                "reason_summary": "test",
                "pruning_request_id": "req-retry-001",
            },
        )
        await approval_store.add(approval)

        class ExplodingOffboardingService(OffboardingService):
            def __init__(self) -> None:
                # Test fake: parent initialiser intentionally skipped.
                self.calls = 0

            @override
            async def offboard(self, request: FiringRequest) -> OffboardingRecord:
                self.calls += 1
                if self.calls == 1:
                    msg = "transient"
                    raise RuntimeError(msg)
                now = datetime.now(UTC)
                return OffboardingRecord(
                    agent_id=request.agent_id,
                    agent_name=request.agent_name,
                    firing_request_id=NotBlankStr(str(request.id)),
                    tasks_reassigned=(),
                    memory_archive_id=None,
                    org_memories_promoted=0,
                    team_notification_sent=True,
                    started_at=now,
                    completed_at=now,
                )

        offboarding = ExplodingOffboardingService()
        service = PruningService(
            policies=(),
            registry=registry,
            tracker=_tracker(),
            approval_store=approval_store,
            offboarding_service=offboarding,
        )

        # First cycle: offboarding fails transiently, claim is released.
        await service._process_decided_approvals()
        assert offboarding.calls == 1
        assert str(approval.id) not in service._in_flight_approvals
        assert str(approval.id) not in service._processed_approval_ids

        # Second cycle: claim succeeds, offboarding completes, id is
        # marked processed.
        await service._process_decided_approvals()
        assert offboarding.calls == 2
        assert str(approval.id) in service._processed_approval_ids
