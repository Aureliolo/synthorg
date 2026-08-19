"""What the rollup does with an initiative that can no longer advance."""

from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.initiative_stall import (
    DISPOSITION_METADATA_KEY,
    ESCALATION_ACTOR,
    INITIATIVE_STALL_ACTION_TYPE,
    REASON_METADATA_KEY,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import (
    REPLAN_IN_PROGRESS_DISPOSITIONS,
    ReplanDisposition,
    StallReason,
)
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.initiative.stall_escalation import StallEscalationService
from synthorg.engine.review_staffing.notices import DispatcherSource
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability.events.initiative import (
    INITIATIVE_STALL_ESCALATION_FAILED,
    INITIATIVE_STALL_NOTICE_FAILED,
)
from tests._shared import (
    FakeClock,
    as_uuid,
    mock_of,
    sid,
)
from tests._shared import (
    RecordingReplanTrigger as _RecordingReplanTrigger,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend
from tests.unit.engine.initiative._rollup_fixtures import (
    ITEM_A,
    ITEM_B,
    PLAN_ID,
    decided_plan_ids,
    item,
    open_decisions,
    plan_of,
    seed,
    task_of,
)

pytestmark = pytest.mark.unit


def _dispatcher(dispatch: AsyncMock) -> DispatcherSource:
    """Build a late-bound source answering a dispatcher over *dispatch*.

    Returns:
        The source, matching the shape the escalation calls per send.
    """
    return lambda: mock_of[NotificationDispatcher](dispatch=dispatch)


async def _stalled(
    *,
    disposition: ReplanDisposition | None,
    approvals: ApprovalStoreProtocol | None,
    notifications: DispatcherSource = None,
) -> tuple[ProjectRollupService, FakePersistenceBackend]:
    """Seed a plan whose every outstanding item is dead.

    Returns:
        The rollup and its backend.
    """
    trigger = (
        None
        if disposition is None
        else _RecordingReplanTrigger(disposition=disposition)
    )
    return await seed(
        plan_of(item(ITEM_A), item(ITEM_B)),
        task_of(ITEM_A, TaskStatus.COMPLETED),
        task_of(ITEM_B, TaskStatus.FAILED),
        replan_trigger=trigger,
        approvals=approvals,
        notifications=notifications,
    )


class TestReplanTrigger:
    """A plan that can no longer advance replans instead of hanging."""

    async def test_fires_when_no_item_can_advance(self) -> None:
        trigger = _RecordingReplanTrigger()
        service, _ = await seed(
            plan_of(item(ITEM_A), item(ITEM_B)),
            task_of(ITEM_A, TaskStatus.COMPLETED),
            task_of(ITEM_B, TaskStatus.FAILED),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(PLAN_ID))

        assert trigger.fired == [(sid(PLAN_ID), StallReason.ALL_FAILED)]

    async def test_does_not_fire_while_work_is_in_flight(self) -> None:
        trigger = _RecordingReplanTrigger()
        service, _ = await seed(
            plan_of(item(ITEM_A), item(ITEM_B)),
            task_of(ITEM_A, TaskStatus.FAILED),
            task_of(ITEM_B, TaskStatus.IN_PROGRESS),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(PLAN_ID))

        assert trigger.fired == []

    async def test_does_not_fire_for_a_terminal_plan(self) -> None:
        """A superseded plan's dead items are the retired revision's, not live."""
        trigger = _RecordingReplanTrigger()
        service, _ = await seed(
            plan_of(item(ITEM_A), status=PlanStatus.SUPERSEDED),
            task_of(ITEM_A, TaskStatus.FAILED),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(PLAN_ID))

        assert trigger.fired == []

    async def test_drain_delegates_to_the_trigger(self) -> None:
        trigger = _RecordingReplanTrigger()
        service, _ = await seed(
            plan_of(item(ITEM_A)),
            task_of(ITEM_A, TaskStatus.FAILED),
            replan_trigger=trigger,
        )

        await service.drain_replan_trigger(timeout_sec=5.0)

        assert trigger.drained == [5.0]

    async def test_drain_is_a_noop_without_a_wired_trigger(self) -> None:
        service, _ = await seed(
            plan_of(item(ITEM_A)),
            task_of(ITEM_A, TaskStatus.FAILED),
        )

        await service.drain_replan_trigger(timeout_sec=5.0)


class TestStallEscalation:
    """An initiative with no automatic route left reaches the operator."""

    async def test_an_exhausted_budget_raises_one_decision(self) -> None:
        """The refusal the trigger returns is what the operator is told about.

        A refusal decided inside the detached replan reaches nobody, so the
        rollup re-schedules a replan that can never run, on every pass, with a
        rewritten warning as the only trace.
        """
        store = ApprovalStore()
        service, backend = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED, approvals=store
        )

        await service.recompute(as_uuid(PLAN_ID))

        assert await decided_plan_ids(store) == (sid(PLAN_ID),)
        # The plan is left alone: it is still replannable by hand while the
        # operator decides, and ending it is their call rather than the org's.
        plan = await backend.plans.get(NotBlankStr(sid(PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EXECUTING

    async def test_a_disabled_trigger_raises_the_same_decision(self) -> None:
        """Switching auto-replan off hangs an initiative just as silently."""
        store = ApprovalStore()
        service, _ = await _stalled(
            disposition=ReplanDisposition.DISABLED, approvals=store
        )

        await service.recompute(as_uuid(PLAN_ID))

        assert await decided_plan_ids(store) == (sid(PLAN_ID),)

    async def test_no_trigger_at_all_raises_the_same_decision(self) -> None:
        store = ApprovalStore()
        service, backend = await _stalled(disposition=None, approvals=store)

        await service.recompute(as_uuid(PLAN_ID))

        assert await decided_plan_ids(store) == (sid(PLAN_ID),)
        plan = await backend.plans.get(NotBlankStr(sid(PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EXECUTING

    @pytest.mark.parametrize(
        "disposition", sorted(REPLAN_IN_PROGRESS_DISPOSITIONS, key=str)
    )
    async def test_a_replan_under_way_raises_nothing(
        self, disposition: ReplanDisposition
    ) -> None:
        """Every disposition in the set, so narrowing it is caught here.

        Driving only the scheduled one lets a member be dropped from the
        frozenset and still pass, and a dropped member escalates an initiative
        that is already being replanned.
        """
        store = ApprovalStore()
        service, _ = await _stalled(disposition=disposition, approvals=store)

        await service.recompute(as_uuid(PLAN_ID))

        assert await decided_plan_ids(store) == ()

    async def test_later_passes_do_not_raise_a_second_decision(self) -> None:
        """The rollup asks on every recompute; the operator is told once."""
        store = ApprovalStore()
        service, _ = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED, approvals=store
        )

        await service.recompute(as_uuid(PLAN_ID))
        await service.recompute(as_uuid(PLAN_ID))
        await service.recompute(as_uuid(PLAN_ID))

        assert await decided_plan_ids(store) == (sid(PLAN_ID),)

    async def test_an_open_decision_stops_the_trigger_being_re_asked(self) -> None:
        """Nothing automatic runs while a person is deciding, and nothing logs.

        The refusal is a WARNING, so re-asking every pass rebuilds the
        repeating log line the decision exists to replace.
        """
        store = ApprovalStore()
        trigger = _RecordingReplanTrigger(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED
        )
        service, _ = await seed(
            plan_of(item(ITEM_A), item(ITEM_B)),
            task_of(ITEM_A, TaskStatus.COMPLETED),
            task_of(ITEM_B, TaskStatus.FAILED),
            replan_trigger=trigger,
            approvals=store,
        )

        await service.recompute(as_uuid(PLAN_ID))
        await service.recompute(as_uuid(PLAN_ID))
        await service.recompute(as_uuid(PLAN_ID))

        assert len(trigger.fired) == 1

    async def test_with_nothing_able_to_ask_the_plan_fails(self) -> None:
        """A plan nobody can decide is worse than one that says it stopped."""
        service, backend = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED, approvals=None
        )

        await service.recompute(as_uuid(PLAN_ID))

        plan = await backend.plans.get(NotBlankStr(sid(PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.FAILED
        assert plan.failure_reason == "initiative stalled: all_failed"


class TestTheDecisionItself:
    """What the operator is handed has to be answerable and attributable."""

    @staticmethod
    async def _one_decision() -> ApprovalItem:
        """Raise a decision and return it.

        Returns:
            The single pending item.
        """
        store = ApprovalStore()
        service, _ = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED, approvals=store
        )
        await service.recompute(as_uuid(PLAN_ID))
        items = await open_decisions(store)
        assert len(items) == 1
        return items[0]

    async def test_it_carries_the_reason_the_answer_is_confirmed_against(
        self,
    ) -> None:
        """The reason decides HOW the stall is re-confirmed, so it is recorded.

        Re-deriving over items answers "recovered" for every tail-stage
        verdict, because every item is done in those cases.
        """
        decision = await self._one_decision()

        assert decision.metadata[REASON_METADATA_KEY] == StallReason.ALL_FAILED.value

    async def test_it_names_which_refusal_raised_it(self) -> None:
        decision = await self._one_decision()

        assert (
            decision.metadata[DISPOSITION_METADATA_KEY]
            == ReplanDisposition.BUDGET_EXHAUSTED.value
        )

    async def test_it_is_attributed_to_the_organisation_that_raised_it(self) -> None:
        """The resume flow reads this back, so a forged item cannot be acted on."""
        decision = await self._one_decision()

        assert str(decision.requested_by) == ESCALATION_ACTOR
        assert str(decision.action_type) == INITIATIVE_STALL_ACTION_TYPE
        assert decision.status is ApprovalStatus.PENDING

    async def test_its_description_names_what_died(self) -> None:
        """A decision that does not say what happened cannot be answered."""
        decision = await self._one_decision()

        described = str(decision.description).lower()
        assert "item " in described
        assert "failed" in described

    async def test_its_title_names_the_objective_never_an_id(self) -> None:
        decision = await self._one_decision()

        assert "Ship it" in str(decision.title)
        assert sid(PLAN_ID) not in str(decision.title)


class TestTheNotification:
    """The decision is announced once, and a failed announcement is not fatal."""

    async def test_one_notification_on_the_edge_that_opens_the_decision(
        self,
    ) -> None:
        store = ApprovalStore()
        dispatch = AsyncMock()
        service, _ = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED,
            approvals=store,
            notifications=_dispatcher(dispatch),
        )

        await service.recompute(as_uuid(PLAN_ID))
        await service.recompute(as_uuid(PLAN_ID))

        assert dispatch.await_count == 1
        sent = dispatch.await_args
        assert sent is not None
        assert "Ship it" in str(sent.args[0].title)

    async def test_a_failed_send_leaves_the_decision_standing(self) -> None:
        """The operator still finds the item; only the announcement degraded."""
        store = ApprovalStore()
        dispatch = AsyncMock(side_effect=RuntimeError("sink unavailable"))
        service, _ = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED,
            approvals=store,
            notifications=_dispatcher(dispatch),
        )

        with capture_logs() as caplog:
            await service.recompute(as_uuid(PLAN_ID))

        assert await decided_plan_ids(store) == (sid(PLAN_ID),)
        assert any(
            entry.get("event") == INITIATIVE_STALL_NOTICE_FAILED for entry in caplog
        )

    async def test_an_unwired_dispatcher_is_not_a_failure(self) -> None:
        store = ApprovalStore()
        service, _ = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED,
            approvals=store,
            notifications=lambda: None,
        )

        await service.recompute(as_uuid(PLAN_ID))

        assert await decided_plan_ids(store) == (sid(PLAN_ID),)

    async def test_the_dispatcher_is_read_per_send_never_captured(self) -> None:
        """A settings write rewires notifications; a captured one is stale."""
        first = AsyncMock()
        second = AsyncMock()
        live: list[AsyncMock] = [first]
        store = ApprovalStore()
        service, _ = await _stalled(
            disposition=ReplanDisposition.BUDGET_EXHAUSTED,
            approvals=store,
            notifications=lambda: mock_of[NotificationDispatcher](dispatch=live[0]),
        )
        live[0] = second

        await service.recompute(as_uuid(PLAN_ID))

        assert first.await_count == 0
        assert second.await_count == 1


class TestADegradedApprovalStore:
    """A recompute is reachable over HTTP, so it must not answer a 500."""

    async def test_the_recompute_survives_and_says_so(self) -> None:
        broken = mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(side_effect=RuntimeError("approvals unavailable"))
        )
        service, backend = await seed(
            plan_of(item(ITEM_A), item(ITEM_B)),
            task_of(ITEM_A, TaskStatus.COMPLETED),
            task_of(ITEM_B, TaskStatus.FAILED),
            replan_trigger=_RecordingReplanTrigger(
                disposition=ReplanDisposition.BUDGET_EXHAUSTED
            ),
        )
        clock = FakeClock()
        service.attach_tail(
            stall_escalation=StallEscalationService(
                persistence=backend,
                plan_status_writer=build_plan_service(backend, clock=clock),
                approvals=broken,
                clock=clock,
            )
        )

        with capture_logs() as caplog:
            await service.recompute(as_uuid(PLAN_ID))

        assert any(
            entry.get("event") == INITIATIVE_STALL_ESCALATION_FAILED for entry in caplog
        )
        plan = await backend.plans.get(NotBlankStr(sid(PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EXECUTING
