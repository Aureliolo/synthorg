"""Human-in-the-loop client implementation."""

import math

from synthorg.client.human_queue import HumanInputQueue
from synthorg.client.models import (
    ClientFeedback,
    ClientProfile,
    GenerationContext,
    ReviewContext,
    TaskRequirement,
)
from synthorg.observability import get_logger
from synthorg.observability.events.client import (
    CLIENT_CONFIG_INVALID,
    CLIENT_REQUEST_SUBMITTED,
    CLIENT_REVIEW_STARTED,
)

logger = get_logger(__name__)


class HumanClient:
    """Client that delegates decisions to a human via a queue.

    Both ``submit_requirement`` and ``review_deliverable`` enqueue
    a ticket on the injected :class:`HumanInputQueue` and await a
    human response. On timeout, ``submit_requirement`` returns
    ``None`` (declines the round) and ``review_deliverable``
    returns a rejection with a descriptive reason.
    """

    _TIMEOUT_REASON: str = "Human reviewer did not respond within timeout"

    def __init__(
        self,
        *,
        profile: ClientProfile,
        queue: HumanInputQueue,
        timeout: float,
    ) -> None:
        """Initialize the human client.

        Args:
            profile: Profile describing the client persona.
            queue: Transport used to reach the human operator.
            timeout: Seconds to wait for a human response.  Resolve via
                ``ConfigResolver.get_float("client",
                "human_response_timeout_seconds")`` at the call site.

        Raises:
            ValueError: If ``timeout`` is not positive.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            msg = f"timeout must be a finite positive number, got {timeout}"
            logger.warning(
                CLIENT_CONFIG_INVALID,
                error=msg,
                param="timeout",
                value=timeout,
            )
            raise ValueError(msg)
        self._profile = profile
        self._queue = queue
        self._timeout = timeout

    @property
    def profile(self) -> ClientProfile:
        """Expose the client profile for pool/selector consumption."""
        return self._profile

    async def submit_requirement(
        self,
        context: GenerationContext,
    ) -> TaskRequirement | None:
        """Ask a human to supply a requirement."""
        ticket = await self._queue.enqueue_requirement(
            client_id=self._profile.client_id,
            context=context,
        )
        logger.debug(
            CLIENT_REQUEST_SUBMITTED,
            client_id=self._profile.client_id,
            ticket_id=ticket,
            kind="human",
        )
        return await self._queue.await_requirement(
            ticket,
            timeout=self._timeout,
        )

    async def review_deliverable(
        self,
        context: ReviewContext,
    ) -> ClientFeedback:
        """Ask a human to review a deliverable; rejects on timeout."""
        ticket = await self._queue.enqueue_review(
            client_id=self._profile.client_id,
            context=context,
        )
        logger.debug(
            CLIENT_REVIEW_STARTED,
            client_id=self._profile.client_id,
            task_id=context.task_id,
            ticket_id=ticket,
            kind="human",
        )
        feedback = await self._queue.await_review(
            ticket,
            timeout=self._timeout,
        )
        if feedback is None:
            return ClientFeedback(
                task_id=context.task_id,
                client_id=self._profile.client_id,
                accepted=False,
                reason=self._TIMEOUT_REASON,
            )
        return feedback
