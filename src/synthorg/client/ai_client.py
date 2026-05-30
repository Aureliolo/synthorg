"""AI-backed client implementation."""

from synthorg.client.models import (
    ClientFeedback,
    ClientProfile,
    GenerationContext,
    ReviewContext,
    TaskRequirement,
)
from synthorg.client.protocols import (
    FeedbackStrategy,
    RequirementGenerator,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.client import (
    CLIENT_REQUEST_SUBMITTED,
    CLIENT_REVIEW_STARTED,
)

logger = get_logger(__name__)


class AIClient:
    """LLM-backed client with configurable strategies.

    Composes an injected :class:`RequirementGenerator` and
    :class:`FeedbackStrategy` with a :class:`ClientProfile`. Both
    ``ClientInterface`` methods delegate to the strategies: the
    generator produces one requirement per ``submit_requirement``
    call (returning ``None`` if the generator produces nothing),
    and the feedback strategy evaluates deliverables.
    """

    def __init__(
        self,
        *,
        profile: ClientProfile,
        generator: RequirementGenerator,
        feedback: FeedbackStrategy,
    ) -> None:
        """Initialize the AI client.

        Args:
            profile: Profile describing the client persona.
            generator: Requirement generator for ``submit_requirement``.
            feedback: Feedback strategy for ``review_deliverable``.
        """
        self._profile = profile
        self._generator = generator
        self._feedback = feedback

    @property
    def profile(self) -> ClientProfile:
        """Expose the client profile for pool/selector consumption."""
        return self._profile

    async def submit_requirement(
        self,
        context: GenerationContext,
    ) -> TaskRequirement | None:
        """Delegate to the generator and return the first requirement.

        Returns:
            The first generated requirement, or ``None`` when the
            generator yields an empty tuple (this client declines to
            participate).
        """
        single = context.model_copy(update={"count": 1})
        try:
            produced = await self._generator.generate(single)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                CLIENT_REQUEST_SUBMITTED,
                exc,
                client_id=self._profile.client_id,
                domain=context.domain,
                stage="generate",
            )
            raise
        logger.info(
            CLIENT_REQUEST_SUBMITTED,
            client_id=self._profile.client_id,
            domain=context.domain,
            generated=len(produced),
            kind="ai",
        )
        if not produced:
            return None
        return produced[0]

    async def review_deliverable(
        self,
        context: ReviewContext,
    ) -> ClientFeedback:
        """Delegate review to the injected feedback strategy.

        Returns:
            The client feedback produced by the feedback strategy.
        """
        logger.info(
            CLIENT_REVIEW_STARTED,
            client_id=self._profile.client_id,
            task_id=context.task_id,
            kind="ai",
        )
        try:
            return await self._feedback.evaluate(context)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                CLIENT_REVIEW_STARTED,
                exc,
                client_id=self._profile.client_id,
                task_id=context.task_id,
                stage="evaluate",
            )
            raise
