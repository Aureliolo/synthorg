"""Binary accept/reject feedback strategy."""

import math

from synthorg.client.models import ClientFeedback, ReviewContext
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.client import CLIENT_REVIEW_COMPLETED

logger = get_logger(__name__)


class BinaryFeedback:
    """Binary accept/reject feedback based on summary length.

    Accepts when the deliverable summary meets a minimum threshold
    scaled by the number of acceptance criteria and a strictness
    multiplier; rejects otherwise with a reason. Simplest reference
    ``FeedbackStrategy`` implementation: no scoring, no per-criterion
    tracking.
    """

    _BASE_THRESHOLD: int = 20

    def __init__(
        self,
        *,
        client_id: NotBlankStr,
        strictness_multiplier: float = 1.0,
    ) -> None:
        """Initialize the binary feedback strategy.

        Args:
            client_id: Identifier of the reviewing client.
            strictness_multiplier: Positive scaling factor on the
                length threshold. Higher values reject shorter
                summaries.

        Raises:
            ValueError: If ``strictness_multiplier`` is not positive.
        """
        if not math.isfinite(strictness_multiplier) or strictness_multiplier <= 0:
            msg = (
                "strictness_multiplier must be a finite positive "
                f"number, got {strictness_multiplier}"
            )
            raise ValueError(msg)
        self._client_id = client_id
        self._strictness_multiplier = strictness_multiplier

    async def evaluate(self, context: ReviewContext) -> ClientFeedback:
        """Evaluate a deliverable and return binary feedback.

        Args:
            context: Review context with task details and deliverable.

        Returns:
            Accepted ``ClientFeedback`` when the summary meets the
            threshold, rejected with a reason otherwise.
        """
        criteria_count = max(1, len(context.acceptance_criteria))
        threshold = max(
            self._BASE_THRESHOLD,
            int(self._BASE_THRESHOLD * criteria_count * self._strictness_multiplier),
        )
        summary_length = len(context.deliverable_summary.strip())
        accepted = summary_length >= threshold

        logger.debug(
            CLIENT_REVIEW_COMPLETED,
            task_id=context.task_id,
            client_id=self._client_id,
            accepted=accepted,
            threshold=threshold,
            summary_length=summary_length,
            strategy="binary",
        )

        if accepted:
            return ClientFeedback(
                task_id=context.task_id,
                client_id=self._client_id,
                accepted=True,
            )
        return ClientFeedback(
            task_id=context.task_id,
            client_id=self._client_id,
            accepted=False,
            reason=(
                f"Deliverable summary too brief: {summary_length} "
                f"chars below threshold of {threshold}"
            ),
            unmet_criteria=context.acceptance_criteria,
        )
