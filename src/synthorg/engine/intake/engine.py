"""Intake engine orchestrator."""

from synthorg.client.models import ClientRequest, RequestStatus
from synthorg.engine.intake.models import IntakeResult  # noqa: TC001
from synthorg.engine.intake.protocol import IntakeStrategy  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.client import (
    CLIENT_REQUEST_APPROVED,
    CLIENT_REQUEST_REJECTED,
    CLIENT_REQUEST_SCOPED,
    CLIENT_REQUEST_TRANSITION_CONFIG_ERROR,
    CLIENT_REQUEST_TRANSITION_INVALID,
    CLIENT_REQUEST_TRIAGING,
)
from synthorg.observability.events.review_pipeline import (
    INTAKE_REQUEST_ACCEPTED,
    INTAKE_REQUEST_RECEIVED,
    INTAKE_REQUEST_REJECTED,
)

logger = get_logger(__name__)


class IntakeEngine:
    """Walks a :class:`ClientRequest` through its intake lifecycle.

    Emits observability events at every state transition, delegates
    the accept/reject decision to the injected
    :class:`IntakeStrategy`, and returns the final
    :class:`ClientRequest` plus the :class:`IntakeResult`. Callers
    are responsible for persisting the final request state.
    """

    def __init__(self, *, strategy: IntakeStrategy) -> None:
        """Initialize the intake engine.

        Args:
            strategy: Strategy implementation that decides accept
                or reject and (on accept) creates the task.
        """
        self._strategy = strategy

    @property
    def strategy(self) -> IntakeStrategy:
        """Return the configured intake strategy.

        Read-only introspection seam used by the client-simulation
        runtime builder and its tests to assert which strategy was
        wired at boot without reaching into a private attribute.
        """
        return self._strategy

    async def process(
        self,
        request: ClientRequest,
    ) -> tuple[ClientRequest, IntakeResult]:
        """Run the full intake lifecycle for ``request``.

        Args:
            request: A newly-submitted client request. Must have
                ``status == RequestStatus.SUBMITTED``.

        Returns:
            A tuple ``(final_request, result)`` where
            ``final_request`` has the terminal status and
            metadata, and ``result`` reports the outcome.

        Raises:
            ValueError: If ``request.status`` is not ``SUBMITTED``.
        """
        if request.status is not RequestStatus.SUBMITTED:
            logger.warning(
                CLIENT_REQUEST_TRANSITION_INVALID,
                request_id=request.request_id,
                client_id=request.client_id,
                from_status=request.status.value,
                expected=RequestStatus.SUBMITTED.value,
                operation="process",
            )
            msg = (
                "IntakeEngine.process requires SUBMITTED request, "
                f"got {request.status.value!r}"
            )
            raise ValueError(msg)

        logger.info(
            INTAKE_REQUEST_RECEIVED,
            request_id=request.request_id,
            client_id=request.client_id,
        )

        triaging = request.with_status(RequestStatus.TRIAGING)
        logger.info(
            CLIENT_REQUEST_TRIAGING,
            request_id=triaging.request_id,
            client_id=triaging.client_id,
        )
        scoping = triaging.with_status(RequestStatus.SCOPING)
        logger.info(
            CLIENT_REQUEST_SCOPED,
            request_id=scoping.request_id,
            client_id=scoping.client_id,
        )

        result = await self._strategy.process(scoping)

        if result.accepted:
            return self._finalize_accepted(scoping, result)
        return self._finalize_rejected(scoping, result)

    async def finalize_scoped(
        self,
        request: ClientRequest,
    ) -> tuple[ClientRequest, IntakeResult]:
        """Finalize an already-scoped request via the strategy.

        Used by manual scope flows that want to run triage +
        scoping synchronously via ``POST /requests/{id}/scope`` and
        then separately approve via ``POST /requests/{id}/approve``.

        Args:
            request: A client request already in SCOPING status.

        Raises:
            ValueError: If ``request.status`` is not ``SCOPING``.
        """
        if request.status is not RequestStatus.SCOPING:
            logger.warning(
                CLIENT_REQUEST_TRANSITION_INVALID,
                request_id=request.request_id,
                client_id=request.client_id,
                from_status=request.status.value,
                expected=RequestStatus.SCOPING.value,
                operation="finalize_scoped",
            )
            msg = (
                "IntakeEngine.finalize_scoped requires SCOPING request, "
                f"got {request.status.value!r}"
            )
            raise ValueError(msg)
        result = await self._strategy.process(request)
        if result.accepted:
            return self._finalize_accepted(request, result)
        return self._finalize_rejected(request, result)

    @staticmethod
    def _finalize_accepted(
        request: ClientRequest,
        result: IntakeResult,
    ) -> tuple[ClientRequest, IntakeResult]:
        if result.task_id is None:
            logger.error(
                CLIENT_REQUEST_TRANSITION_CONFIG_ERROR,
                request_id=request.request_id,
                client_id=request.client_id,
                reason="accepted intake result missing task_id",
            )
            msg = "Accepted intake result missing task_id"
            raise ValueError(msg)
        approved = request.with_status(RequestStatus.APPROVED)
        logger.info(
            CLIENT_REQUEST_APPROVED,
            request_id=approved.request_id,
            client_id=approved.client_id,
        )
        task_metadata = dict(approved.metadata)
        task_metadata["task_id"] = result.task_id
        created = approved.with_status(
            RequestStatus.TASK_CREATED,
            metadata=task_metadata,
        )
        logger.info(
            INTAKE_REQUEST_ACCEPTED,
            request_id=created.request_id,
            client_id=created.client_id,
            task_id=result.task_id,
        )
        return created, result

    @staticmethod
    def _finalize_rejected(
        request: ClientRequest,
        result: IntakeResult,
    ) -> tuple[ClientRequest, IntakeResult]:
        rejection_metadata = dict(request.metadata)
        if result.rejection_reason is not None:
            rejection_metadata["rejection_reason"] = result.rejection_reason
        cancelled = request.with_status(
            RequestStatus.CANCELLED,
            metadata=rejection_metadata,
        )
        logger.info(
            CLIENT_REQUEST_REJECTED,
            request_id=cancelled.request_id,
            client_id=cancelled.client_id,
            reason=result.rejection_reason,
        )
        logger.info(
            INTAKE_REQUEST_REJECTED,
            request_id=cancelled.request_id,
            client_id=cancelled.client_id,
            reason=result.rejection_reason,
        )
        return cancelled, result
