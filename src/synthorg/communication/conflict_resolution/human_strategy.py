"""Human escalation conflict resolution strategy (see Communication design page).

Strategy 3 from the Communication design: escalate the conflict to a
human operator and await their decision before returning a resolution.

When a conflict arrives, the resolver:

1. Persists an :class:`Escalation` row via the configured
   :class:`EscalationQueueStore`.
2. Registers an ``asyncio.Future`` in the
   :class:`PendingFuturesRegistry`.
3. Dispatches an operator notification through the shared
   :class:`NotificationDispatcher`.
4. Awaits the Future with the configured timeout.  A decision arriving
   via the REST endpoint resolves the Future so the resolver wakes
   with the operator's payload.
5. Hands the decision to the configured :class:`DecisionProcessor` to
   produce a :class:`ConflictResolution`.

On timeout or explicit cancellation the resolver returns a no-winner
:class:`ConflictResolution` with outcome ``ESCALATED_TO_HUMAN`` so
downstream consumers match the previous stub contract (never
``None``), and the store row is transitioned to ``EXPIRED`` so
subsequent GETs surface the terminal state.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,  # noqa: TC001
    EscalationQueueStore,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.notifications.dispatcher import NotificationDispatcher  # noqa: TC001
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATED,
    CONFLICT_ESCALATION_CANCELLED,
    CONFLICT_ESCALATION_NOTIFY_FAILED,
    CONFLICT_ESCALATION_QUEUED,
    CONFLICT_ESCALATION_RESOLVED,
    CONFLICT_ESCALATION_TIMEOUT,
)

logger = get_logger(__name__)

# Upper bound on background notification dispatch so a slow / hung
# notifier sink cannot leak tasks across thousands of escalations.
# Deliberately short -- notification is best-effort; the REST list
# endpoint + sweeper already cover eventual consistency.
_NOTIFICATION_DISPATCH_TIMEOUT_SECONDS: float = 10.0


class HumanEscalationResolver:
    """Escalate conflicts to a human and await the operator decision.

    All dependencies are optional so unit tests that only need the
    "escalate to human" happy path can instantiate the resolver with
    no arguments and receive an immediate ``ESCALATED_TO_HUMAN``
    outcome.  Production deployments inject fully-configured
    dependencies via :func:`build_escalation_queue_store` and
    :func:`build_decision_processor`.

    Args:
        store: Persistent escalation queue.  Defaults to an
            :class:`InMemoryEscalationStore` so callers without a
            configured backend still produce an auditable row.
        processor: Converts an operator decision into a
            :class:`ConflictResolution`.  Defaults to
            :class:`WinnerSelectProcessor`.
        registry: In-process map of awaited Futures.  Decisions
            arriving via the REST endpoint resolve Futures registered
            here to wake the awaiting resolver coroutine.  Defaults
            to a fresh :class:`PendingFuturesRegistry`.
        notifier: Notification dispatcher; receives an
            :class:`NotificationCategory.ESCALATION` event when the
            queue row is created.  When ``None`` the resolver skips
            notification dispatch (useful for tests).
        timeout_seconds: Maximum seconds to wait for a human decision.
            Default is ``None`` -- wait indefinitely for an operator to
            decide via the REST endpoint; the sweeper will still
            transition stale rows to ``EXPIRED`` based on
            ``expires_at`` regardless of whether a resolver is
            listening.  ``0`` causes an immediate timeout -- use this
            in tests that only care about the ESCALATED outcome
            without submitting a decision.  Any positive integer is a
            bounded wait.  Production deployments that want a bounded
            wait should pass an explicit value (or set
            ``EscalationQueueConfig.default_timeout_seconds`` and thread
            it through the resolver wiring).
    """

    def __init__(
        self,
        *,
        store: EscalationQueueStore | None = None,
        processor: DecisionProcessor | None = None,
        registry: PendingFuturesRegistry | None = None,
        notifier: NotificationDispatcher | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """Initialise the resolver with its dependencies."""
        # Local imports keep the optional-dep defaults lightweight.
        from synthorg.communication.conflict_resolution.escalation.in_memory_store import (  # noqa: E501, PLC0415
            InMemoryEscalationStore,
        )
        from synthorg.communication.conflict_resolution.escalation.processors import (  # noqa: PLC0415
            WinnerSelectProcessor,
        )

        self._store: EscalationQueueStore = store or InMemoryEscalationStore()
        self._processor: DecisionProcessor = processor or WinnerSelectProcessor()
        self._registry: PendingFuturesRegistry = registry or PendingFuturesRegistry()
        self._notifier: NotificationDispatcher | None = notifier
        self._timeout_seconds = timeout_seconds
        # Strong refs to in-flight notification tasks so they aren't
        # garbage-collected mid-dispatch (RUF006).  Entries are removed
        # via ``add_done_callback`` once the task completes.
        self._notify_tasks: set[asyncio.Task[None]] = set()

    async def resolve(self, conflict: Conflict) -> ConflictResolution:
        """Create an escalation, notify operators, and await a decision.

        Returns a :class:`ConflictResolution` once the operator decides
        or the timeout fires.
        """
        escalation = self._build_escalation(conflict)
        # Register the Future BEFORE making the row externally visible.
        # A race-fast decision endpoint that sees the row and calls
        # ``registry.resolve`` before we register would otherwise create
        # an orphan decision and leave the resolver waiting until timeout.
        future = await self._registry.register(escalation.id)
        try:
            await self._store.create(escalation)
        except Exception:
            # Reap the future so the registry does not leak, and log
            # the failure so operators see the root cause (a failed
            # ``create`` also surfaces to the caller, but the queue
            # context -- escalation id, conflict id -- is only visible
            # here).
            logger.warning(
                CONFLICT_ESCALATION_QUEUED,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                subject=conflict.subject,
                note="store_create_failed",
            )
            await self._registry.cancel(escalation.id)
            raise
        logger.info(
            CONFLICT_ESCALATION_QUEUED,
            escalation_id=escalation.id,
            conflict_id=conflict.id,
            subject=conflict.subject,
            timeout_seconds=self._timeout_seconds,
        )
        logger.info(
            CONFLICT_ESCALATED,
            conflict_id=conflict.id,
            agent_count=len(conflict.positions),
        )
        if self._notifier is not None:
            # Notification delivery is a side effect: we must not let a
            # slow notifier sink consume the caller's ``timeout_seconds``
            # budget (or block the resolver from awaiting the future at
            # all).  Fire-and-forget on a named background task instead,
            # with the same exception logging as before so failures stay
            # visible.  The sweeper + per-resolver timeout bound the
            # queue row's lifecycle even if no notification is delivered.
            notify_task = asyncio.create_task(
                self._dispatch_notification(escalation, conflict),
                name=f"escalation-notify[{escalation.id}]",
            )
            self._notify_tasks.add(notify_task)
            notify_task.add_done_callback(self._notify_tasks.discard)
            notify_task.add_done_callback(
                log_task_exceptions(
                    logger,
                    CONFLICT_ESCALATION_NOTIFY_FAILED,
                    escalation_id=escalation.id,
                    conflict_id=conflict.id,
                ),
            )

        try:
            if self._timeout_seconds is None:
                decision = await future
            else:
                decision = await asyncio.wait_for(
                    future,
                    timeout=float(self._timeout_seconds),
                )
        except TimeoutError:
            # Multi-worker correctness: a decision may have been persisted
            # by a peer worker whose NOTIFY wake-up we missed (subscriber
            # restart, network blip, deployment rollout).  Re-read the row
            # before declaring timeout so we honour the operator's choice
            # instead of masking it with an ESCALATED_TO_HUMAN fallback.
            late_decision = await self._read_late_decision(escalation, conflict)
            if late_decision is not None:
                return late_decision
            await self._handle_timeout_cleanup(escalation, conflict)
            return self._timeout_resolution(conflict)
        except asyncio.CancelledError:
            await self._handle_cancelled_cleanup(escalation, conflict)
            return self._cancelled_resolution(conflict)

        # The decision endpoint is responsible for persisting the
        # DECIDED row and then resolving the Future -- the resolver
        # only has to hand the decision to the processor.
        decided_by = await self._resolve_decided_by(escalation.id)
        resolution = self._processor.process(
            conflict,
            decision,
            decided_by=decided_by,
        )
        logger.info(
            CONFLICT_ESCALATION_RESOLVED,
            escalation_id=escalation.id,
            conflict_id=conflict.id,
            outcome=resolution.outcome.value,
        )
        return resolution

    async def _dispatch_notification(
        self,
        escalation: Escalation,
        conflict: Conflict,
    ) -> None:
        """Deliver the escalation notification in the background.

        Failures are logged (same context as the previous inline
        try/except) but never propagate -- the notifier is a side
        effect.  Cancellation must re-raise so shutdown can reap the
        task cleanly.  A hard timeout bounds pathological notifier
        sinks so tasks cannot accumulate in ``self._notify_tasks``.
        """
        if self._notifier is None:
            return
        try:
            await asyncio.wait_for(
                self._notifier.dispatch(
                    self._build_notification(escalation, conflict),
                ),
                timeout=_NOTIFICATION_DISPATCH_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                CONFLICT_ESCALATION_QUEUED,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                timeout_seconds=_NOTIFICATION_DISPATCH_TIMEOUT_SECONDS,
                note="notification_dispatch_timeout",
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_QUEUED,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="notification_dispatch_failed",
            )

    async def _handle_timeout_cleanup(
        self,
        escalation: Escalation,
        conflict: Conflict,
    ) -> None:
        """Shield-wrap the timeout cleanup so the contract is never broken.

        ``resolve`` must always return a terminal ``ConflictResolution``
        on ``TimeoutError``; ``asyncio.shield`` keeps the cleanup awaits
        running through downstream cancellation, and the try/except
        guards swallow storage failures so they log and keep the
        resolver's fallback path intact.
        """
        try:
            await asyncio.shield(self._registry.cancel(escalation.id))
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_TIMEOUT,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="registry_cancel_failed",
            )
        try:
            await asyncio.shield(
                self._store.mark_expired(datetime.now(UTC).isoformat()),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_TIMEOUT,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="mark_expired_failed",
            )
        logger.warning(
            CONFLICT_ESCALATION_TIMEOUT,
            escalation_id=escalation.id,
            conflict_id=conflict.id,
            timeout_seconds=self._timeout_seconds,
        )

    async def _handle_cancelled_cleanup(
        self,
        escalation: Escalation,
        conflict: Conflict,
    ) -> None:
        """Shield-wrap the cancel cleanup so the row transitions terminally.

        Persist the CANCELLED state even when the cancelling caller
        tries to cancel us mid-cleanup, then log for audit.
        """
        try:
            await asyncio.shield(self._registry.cancel(escalation.id))
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_CANCELLED,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="registry_cancel_failed",
            )
        try:
            await asyncio.shield(
                self._store.cancel(
                    escalation.id,
                    cancelled_by="system:resolver_cancelled",
                ),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_CANCELLED,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="store_cancel_failed",
            )
        logger.warning(
            CONFLICT_ESCALATION_CANCELLED,
            escalation_id=escalation.id,
            conflict_id=conflict.id,
        )

    async def _read_late_decision(
        self,
        escalation: Escalation,
        conflict: Conflict,
    ) -> ConflictResolution | None:
        """Re-read the row on ``TimeoutError`` and honour a persisted decision.

        Covers the missed-NOTIFY window for multi-worker Postgres
        deployments: if a peer worker persisted a DECIDED row while our
        wait timed out (subscriber restart, dropped notification,
        deployment rollover), the authoritative decision is already
        durable and must override the generic timeout fallback.

        Returns ``None`` when the row is missing, still PENDING, or the
        lookup fails -- the caller then follows the normal timeout
        cleanup + ``ESCALATED_TO_HUMAN`` contract.
        """
        try:
            row = await self._store.get(escalation.id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_TIMEOUT,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="late_decision_lookup_failed",
            )
            return None
        if row is None:
            return None
        if row.status != EscalationStatus.DECIDED or row.decision is None:
            return None
        # Drain the future so the registry does not leak; no-op if the
        # future completed concurrently.
        try:
            await asyncio.shield(self._registry.cancel(escalation.id))
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation.id,
                conflict_id=conflict.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="late_decision_registry_cancel_failed",
            )
        decided_by = row.decided_by or "human"
        resolution = self._processor.process(
            conflict,
            row.decision,
            decided_by=decided_by,
        )
        logger.info(
            CONFLICT_ESCALATION_RESOLVED,
            escalation_id=escalation.id,
            conflict_id=conflict.id,
            outcome=resolution.outcome.value,
            note="late_decision_observed_after_timeout",
        )
        return resolution

    async def _resolve_decided_by(self, escalation_id: str) -> str:
        """Look up the persisted ``decided_by`` or fall back to ``"human"``.

        The REST endpoint saves the DECIDED row with the authoritative
        operator identity before resolving the Future, so the row is
        already visible by the time we reach here.  We still fall back
        to the generic ``"human"`` label if the lookup races or the
        backend is in-memory and scoped to the resolver under test.
        """
        try:
            row = await self._store.get(escalation_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_RESOLVED,
                escalation_id=escalation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="store_lookup_failed",
            )
            return "human"
        if row is None or not row.decided_by:
            return "human"
        return row.decided_by

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Delegate dissent record construction to the processor."""
        return self._processor.build_dissent_records(conflict, resolution)

    def _build_escalation(self, conflict: Conflict) -> Escalation:
        """Construct the initial PENDING :class:`Escalation`."""
        now = datetime.now(UTC)
        expires_at: datetime | None = None
        if self._timeout_seconds is not None:
            expires_at = now + timedelta(seconds=self._timeout_seconds)
        return Escalation(
            # Full UUID (32 hex chars, 122 bits entropy) so persisted
            # escalation IDs cannot collide across long-lived queues.
            id=f"escalation-{uuid4().hex}",
            conflict=conflict,
            status=EscalationStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
        )

    def _build_notification(
        self,
        escalation: Escalation,
        conflict: Conflict,
    ) -> Notification:
        """Render an operator-facing notification for the new escalation."""
        summary_lines = [f"Conflict subject: {conflict.subject}"]
        summary_lines.extend(
            f"- {position.agent_id} ({position.agent_department}, "
            f"{position.agent_level}): {position.position}"
            for position in conflict.positions
        )
        body = "\n".join(summary_lines)
        metadata: dict[str, object] = {
            "escalation_id": escalation.id,
            "conflict_id": conflict.id,
            "conflict_type": conflict.type.value,
            "subject": conflict.subject,
        }
        if conflict.task_id is not None:
            metadata["task_id"] = conflict.task_id
        if escalation.expires_at is not None:
            metadata["expires_at"] = escalation.expires_at.isoformat()
        return Notification(
            category=NotificationCategory.ESCALATION,
            severity=NotificationSeverity.WARNING,
            title=f"Conflict escalation pending: {conflict.id}",
            body=body,
            source="conflict_resolution.human_strategy",
            metadata=metadata,
        )

    def _timeout_resolution(self, conflict: Conflict) -> ConflictResolution:
        """Resolution returned when no decision arrives in time."""
        reason = (
            "No human decision was collected before the escalation timeout. "
            "Conflict remains ESCALATED_TO_HUMAN; operators may still decide "
            "via the REST API."
        )
        return ConflictResolution(
            conflict_id=conflict.id,
            outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
            winning_agent_id=None,
            winning_position=None,
            decided_by="human",
            reasoning=reason,
            resolved_at=datetime.now(UTC),
        )

    def _cancelled_resolution(self, conflict: Conflict) -> ConflictResolution:
        """Resolution returned when the resolver coroutine is cancelled."""
        reason = (
            "Escalation resolver was cancelled before a human decision "
            "could be collected."
        )
        return ConflictResolution(
            conflict_id=conflict.id,
            outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
            winning_agent_id=None,
            winning_position=None,
            decided_by="human",
            reasoning=reason,
            resolved_at=datetime.now(UTC),
        )
