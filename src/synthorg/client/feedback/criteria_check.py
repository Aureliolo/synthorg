"""Per-criterion checklist feedback strategy."""

import re

from synthorg.client.models import ClientFeedback, ReviewContext
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.client import CLIENT_REVIEW_COMPLETED

logger = get_logger(__name__)


class CriteriaCheckFeedback:
    """Per-criterion pass/fail checklist feedback strategy.

    Iterates every acceptance criterion and checks whether the
    deliverable summary mentions it via case-insensitive substring
    match. Accepts only when every criterion is present; rejects
    with a detailed ``unmet_criteria`` tuple and explanatory reason.
    """

    def __init__(self, *, client_id: NotBlankStr) -> None:
        """Initialize the criteria check feedback strategy.

        Args:
            client_id: Identifier of the reviewing client.
        """
        self._client_id = client_id

    async def evaluate(self, context: ReviewContext) -> ClientFeedback:
        """Check every acceptance criterion against the deliverable.

        Args:
            context: Review context with criteria and deliverable.

        Returns:
            ``ClientFeedback`` accepted iff every criterion is
            explicitly mentioned in the deliverable summary.
        """
        deliverable_lower = context.deliverable_summary.lower()
        unmet = tuple(
            criterion
            for criterion in context.acceptance_criteria
            if not re.search(
                r"\b" + re.escape(criterion.lower()) + r"\b",
                deliverable_lower,
            )
        )
        accepted = not unmet

        logger.debug(
            CLIENT_REVIEW_COMPLETED,
            task_id=context.task_id,
            client_id=self._client_id,
            accepted=accepted,
            unmet_count=len(unmet),
            strategy="criteria_check",
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
                f"{len(unmet)} of {len(context.acceptance_criteria)} "
                f"criteria not addressed in deliverable"
            ),
            unmet_criteria=unmet,
        )
