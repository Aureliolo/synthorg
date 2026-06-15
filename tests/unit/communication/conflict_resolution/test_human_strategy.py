"""Tests for the human escalation resolution strategy."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
    RejectDecision,
    WinnerDecision,
)
from synthorg.communication.conflict_resolution.escalation.processors import (
    HybridDecisionProcessor,
    WinnerOnlyDecisionProcessor,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.communication.conflict_resolution.human_strategy import (
    HumanEscalationResolver,
)
from synthorg.communication.conflict_resolution.models import (
    ConflictResolutionOutcome,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.errors import (
    EscalationDecisionAgentError,
    EscalationDecisionShapeError,
)
from synthorg.core.approval import ApprovalItem
from synthorg.notifications.dispatcher import NotificationDispatcher
from tests._shared import as_uuid, mock_of, sid

from .conftest import make_conflict


def _tracking_registry() -> tuple[PendingFuturesRegistry, asyncio.Queue[str]]:
    """Return a registry that posts registered IDs onto a queue.

    Tests use the queue as an event signal instead of real-time polling
    over ``store.list_items``.
    """
    registry = PendingFuturesRegistry()
    enqueued: asyncio.Queue[str] = asyncio.Queue()
    original = registry.register

    async def tracked(escalation_id: str) -> asyncio.Future[EscalationDecision]:
        future = await original(escalation_id)
        await enqueued.put(escalation_id)
        return future

    registry.register = tracked  # type: ignore[method-assign]
    return registry, enqueued


@pytest.mark.unit
class TestApprovalStoreRouting:
    """The resolver mirrors the conflict into the approval queue in parallel."""

    async def test_routes_to_approval_store_when_injected(self) -> None:
        added: list[ApprovalItem] = []

        async def _record(item: ApprovalItem) -> None:
            added.append(item)

        store = mock_of[ApprovalStoreProtocol](add=_record)
        resolver = HumanEscalationResolver(approval_store=store, timeout_seconds=0)
        conflict = make_conflict()
        await resolver.resolve(conflict)
        await asyncio.gather(*tuple(resolver._approval_tasks))
        assert len(added) == 1
        assert added[0].action_type == "conflict.escalation"
        assert added[0].metadata["conflict_id"] == str(conflict.id)

    async def test_no_submission_without_approval_store(self) -> None:
        resolver = HumanEscalationResolver(timeout_seconds=0)
        await resolver.resolve(make_conflict())
        assert resolver._approval_tasks == set()

    async def test_store_failure_does_not_break_escalation(self) -> None:
        async def _boom(_item: ApprovalItem) -> None:
            msg = "approval store down"
            raise RuntimeError(msg)

        store = mock_of[ApprovalStoreProtocol](add=_boom)
        resolver = HumanEscalationResolver(approval_store=store, timeout_seconds=0)
        resolution = await resolver.resolve(make_conflict())
        await asyncio.gather(*tuple(resolver._approval_tasks), return_exceptions=True)
        assert resolution.outcome == ConflictResolutionOutcome.ESCALATED_TO_HUMAN


@pytest.mark.unit
class TestHumanEscalationDefaults:
    """With no arguments the resolver times out immediately."""

    async def test_returns_escalated_outcome(self) -> None:
        resolver = HumanEscalationResolver(timeout_seconds=0)
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert resolution.outcome == ConflictResolutionOutcome.ESCALATED_TO_HUMAN

    async def test_no_winning_agent(self) -> None:
        resolver = HumanEscalationResolver(timeout_seconds=0)
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert resolution.winning_agent_id is None
        assert resolution.winning_position is None

    async def test_decided_by_human(self) -> None:
        resolver = HumanEscalationResolver(timeout_seconds=0)
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert resolution.decided_by == "human"

    async def test_dissent_records_strategy(self) -> None:
        resolver = HumanEscalationResolver(timeout_seconds=0)
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        records = resolver.build_dissent_records(conflict, resolution)
        assert len(records) == 2
        assert records[0].strategy_used == ConflictResolutionStrategy.HUMAN
        assert ("escalation_reason", "human_review_required") in records[0].metadata
        dissenter_ids = {r.dissenting_agent_id for r in records}
        assert dissenter_ids == {"agent-a", "agent-b"}


@pytest.mark.unit
class TestHumanEscalationFullLoop:
    """End-to-end: escalate -> decide -> resolver wakes with decision."""

    async def test_winner_decision_resolves_awaiting_resolver(self) -> None:
        store = InMemoryEscalationStore()
        registry, enqueued = _tracking_registry()
        resolver = HumanEscalationResolver(
            store=store,
            processor=WinnerOnlyDecisionProcessor(),
            registry=registry,
            timeout_seconds=5,
        )
        conflict = make_conflict()

        async def _decide_after_enqueue() -> None:
            # Event-driven sync with the resolver's register() call;
            # no real-time polling.
            escalation_id = await asyncio.wait_for(enqueued.get(), timeout=5.0)
            decision = WinnerDecision(
                winning_agent_id="agent-a",
                reasoning="Tie-breaking call by operator",
            )
            await store.apply_decision(
                escalation_id,
                decision=decision,
                decided_by="human:op-1",
            )
            await registry.resolve(escalation_id, decision)

        resolve_task = asyncio.create_task(resolver.resolve(conflict))
        decide_task = asyncio.create_task(_decide_after_enqueue())
        resolution, _ = await asyncio.gather(resolve_task, decide_task)

        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_HUMAN
        assert resolution.winning_agent_id == "agent-a"
        assert resolution.decided_by == "human:op-1"
        # Decided rows are preserved in the store for audit.
        rows, total = await store.list_items(status=EscalationStatus.DECIDED)
        assert total == 1
        assert rows[0].decision == WinnerDecision(
            winning_agent_id="agent-a",
            reasoning="Tie-breaking call by operator",
        )

    async def test_reject_decision_with_hybrid_processor(self) -> None:
        store = InMemoryEscalationStore()
        registry, enqueued = _tracking_registry()
        resolver = HumanEscalationResolver(
            store=store,
            processor=HybridDecisionProcessor(),
            registry=registry,
            timeout_seconds=5,
        )
        conflict = make_conflict()

        async def _reject() -> None:
            escalation_id = await asyncio.wait_for(enqueued.get(), timeout=5.0)
            decision = RejectDecision(reasoning="Both proposals off-strategy")
            await store.apply_decision(
                escalation_id,
                decision=decision,
                decided_by="human:op-2",
            )
            await registry.resolve(escalation_id, decision)

        resolve_task = asyncio.create_task(resolver.resolve(conflict))
        await asyncio.gather(resolve_task, asyncio.create_task(_reject()))
        resolution = resolve_task.result()
        assert resolution.outcome == ConflictResolutionOutcome.REJECTED_BY_HUMAN
        assert resolution.winning_agent_id is None
        assert resolution.decided_by == "human:op-2"

    async def test_winner_mode_rejects_reject_decision(self) -> None:
        processor = WinnerOnlyDecisionProcessor()
        conflict = make_conflict()
        with pytest.raises(EscalationDecisionShapeError, match="only accepts 'winner'"):
            processor.process(
                conflict,
                RejectDecision(reasoning="no"),
                decided_by="human:op-x",
            )

    async def test_winner_mode_rejects_unknown_agent(self) -> None:
        processor = WinnerOnlyDecisionProcessor()
        conflict = make_conflict()
        with pytest.raises(EscalationDecisionAgentError, match="does not match"):
            processor.process(
                conflict,
                WinnerDecision(
                    winning_agent_id="agent-zzz",
                    reasoning="bad pick",
                ),
                decided_by="human:op-x",
            )

    async def test_hybrid_mode_rejects_unknown_agent(self) -> None:
        processor = HybridDecisionProcessor()
        conflict = make_conflict()
        with pytest.raises(EscalationDecisionAgentError, match="does not match"):
            processor.process(
                conflict,
                WinnerDecision(
                    winning_agent_id="agent-zzz",
                    reasoning="bad pick",
                ),
                decided_by="human:op-x",
            )

    async def test_timeout_returns_escalated_outcome(self) -> None:
        # ``timeout_seconds=0`` triggers the TimeoutError branch
        # deterministically (no real wall-clock sleep).  The resolver
        # contract documents ``0`` as "immediate timeout", which is
        # exactly the path we want to exercise here.
        store = InMemoryEscalationStore()
        registry = PendingFuturesRegistry()
        resolver = HumanEscalationResolver(
            store=store,
            registry=registry,
            timeout_seconds=0,
        )
        conflict = make_conflict()
        resolution = await resolver.resolve(conflict)
        assert resolution.outcome == ConflictResolutionOutcome.ESCALATED_TO_HUMAN
        # The store row should be marked EXPIRED afterwards.
        rows, _ = await store.list_items(status=EscalationStatus.EXPIRED)
        assert len(rows) == 1

    async def test_timeout_reads_late_decision(self) -> None:
        # Missed-NOTIFY window: peer worker persisted a DECIDED row
        # before our local wait timed out.  The resolver must honour
        # the operator's decision instead of the timeout fallback.
        store = InMemoryEscalationStore()
        registry = PendingFuturesRegistry()
        resolver = HumanEscalationResolver(
            store=store,
            registry=registry,
            processor=WinnerOnlyDecisionProcessor(),
            timeout_seconds=0,
        )
        conflict = make_conflict()
        winning_agent_id = conflict.positions[0].agent_id

        # Monkey-patch the store.get called from _read_late_decision so
        # it returns a DECIDED row even though timeout_seconds=0 beat
        # the decision endpoint.  The real missed-NOTIFY scenario maps
        # onto this: the row is durable; the wake-up never arrived.
        original_get = store.get

        async def late_get(escalation_id: str) -> Escalation | None:
            row = await original_get(escalation_id)
            if row is None:
                return None
            return row.model_copy(
                update={
                    "status": EscalationStatus.DECIDED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": "human:op-late",
                    "decision": WinnerDecision(
                        winning_agent_id=winning_agent_id,
                        reasoning="late decision observed after timeout",
                    ),
                },
            )

        store.get = late_get  # type: ignore[method-assign]

        resolution = await resolver.resolve(conflict)
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_HUMAN
        assert resolution.winning_agent_id == winning_agent_id
        assert resolution.decided_by == "human:op-late"

    async def test_sweeper_expires_stale_rows(self) -> None:
        store = InMemoryEscalationStore()
        conflict = make_conflict()
        from synthorg.communication.conflict_resolution.escalation.models import (
            Escalation,
        )

        past = datetime.now(UTC) - timedelta(seconds=10)
        escalation = Escalation(
            id=as_uuid("escalation-stale-0001"),
            conflict=conflict,
            created_at=past,
            expires_at=past,
        )
        await store.create(escalation)
        expired = await store.mark_expired(datetime.now(UTC).isoformat())
        assert expired == (sid("escalation-stale-0001"),)
        row = await store.get(sid("escalation-stale-0001"))
        assert row is not None
        assert row.status == EscalationStatus.EXPIRED


@pytest.mark.unit
class TestHumanEscalationErrorPaths:
    """Storage / notifier / registry failures degrade without swallowing
    interpreter-critical exceptions."""

    async def test_store_create_failure_reaps_future_and_reraises(self) -> None:
        store = InMemoryEscalationStore()
        store.create = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("create boom"),
        )
        registry = PendingFuturesRegistry()
        resolver = HumanEscalationResolver(
            store=store,
            registry=registry,
            timeout_seconds=0,
        )
        conflict = make_conflict()

        with pytest.raises(RuntimeError, match="create boom"):
            await resolver.resolve(conflict)

    async def test_notification_dispatch_failure_is_swallowed(self) -> None:
        notifier = mock_of[NotificationDispatcher](
            dispatch=AsyncMock(
                spec=NotificationDispatcher.dispatch,
                side_effect=RuntimeError("dispatch boom"),
            ),
        )
        resolver = HumanEscalationResolver(notifier=notifier, timeout_seconds=0)
        conflict = make_conflict()
        escalation = resolver._build_escalation(conflict)

        # Notifier is best-effort: a non-critical dispatch failure is
        # logged and never propagated to the resolver.
        await resolver._dispatch_notification(escalation, conflict)

    async def test_timeout_cleanup_tolerates_registry_and_store_failures(
        self,
    ) -> None:
        store = InMemoryEscalationStore()
        store.mark_expired = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("mark_expired boom"),
        )
        registry = PendingFuturesRegistry()
        registry.cancel = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("cancel boom"),
        )
        resolver = HumanEscalationResolver(
            store=store,
            registry=registry,
            timeout_seconds=5,
        )
        conflict = make_conflict()
        escalation = resolver._build_escalation(conflict)

        # Both cleanup awaits fail; the method still completes so
        # ``resolve`` can return its terminal timeout resolution.
        await resolver._handle_timeout_cleanup(escalation, conflict)

    async def test_cancelled_cleanup_tolerates_registry_and_store_failures(
        self,
    ) -> None:
        store = InMemoryEscalationStore()
        store.cancel = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("store cancel boom"),
        )
        registry = PendingFuturesRegistry()
        registry.cancel = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("registry cancel boom"),
        )
        resolver = HumanEscalationResolver(
            store=store,
            registry=registry,
            timeout_seconds=5,
        )
        conflict = make_conflict()
        escalation = resolver._build_escalation(conflict)

        await resolver._handle_cancelled_cleanup(escalation, conflict)

    async def test_read_late_decision_lookup_failure_returns_none(self) -> None:
        store = InMemoryEscalationStore()
        store.get = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("get boom"),
        )
        resolver = HumanEscalationResolver(store=store, timeout_seconds=5)
        conflict = make_conflict()
        escalation = resolver._build_escalation(conflict)

        result = await resolver._read_late_decision(escalation, conflict)

        assert result is None

    async def test_read_late_decision_tolerates_registry_cancel_failure(
        self,
    ) -> None:
        store = InMemoryEscalationStore()
        registry = PendingFuturesRegistry()
        registry.cancel = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("cancel boom"),
        )
        resolver = HumanEscalationResolver(
            store=store,
            registry=registry,
            processor=WinnerOnlyDecisionProcessor(),
            timeout_seconds=5,
        )
        conflict = make_conflict()
        escalation = resolver._build_escalation(conflict)
        winning_agent_id = conflict.positions[0].agent_id
        decided_row = escalation.model_copy(
            update={
                "status": EscalationStatus.DECIDED,
                "decided_at": datetime.now(UTC),
                "decided_by": "human:op-late",
                "decision": WinnerDecision(
                    winning_agent_id=winning_agent_id,
                    reasoning="late decision",
                ),
            },
        )
        store.get = AsyncMock(  # type: ignore[method-assign]
            return_value=decided_row,
        )

        # Registry cancel fails while reaping the future, but the
        # durable decision is still honoured.
        resolution = await resolver._read_late_decision(escalation, conflict)

        assert resolution is not None
        assert resolution.outcome == ConflictResolutionOutcome.RESOLVED_BY_HUMAN
        assert resolution.winning_agent_id == winning_agent_id

    async def test_resolve_decided_by_lookup_failure_falls_back_to_human(
        self,
    ) -> None:
        store = InMemoryEscalationStore()
        store.get = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("get boom"),
        )
        resolver = HumanEscalationResolver(store=store, timeout_seconds=5)

        decided_by = await resolver._resolve_decided_by("escalation-missing")

        assert decided_by == "human"
