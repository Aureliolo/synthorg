"""Adversarial strict feedback strategy for stress testing."""

from typing import Final

from synthorg.client.models import ClientFeedback, ReviewContext
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.client import CLIENT_REVIEW_COMPLETED

logger = get_logger(__name__)
_DEFAULT_MIN_LENGTH: Final[int] = 200
_DEFAULT_MIN_WORDS: Final[int] = 30


class AdversarialFeedback:
    """Deliberately strict feedback strategy for stress testing.

    Rejects on any minor deviation: short summaries, insufficient
    vocabulary, missing acceptance criteria. Intended for
    stress-testing agent robustness and edge-case handling, not
    day-to-day review.

    Accepts only when every one of the following holds:

    - Deliverable summary length ``>= min_length``
    - Distinct word count ``>= min_words``
    - Every acceptance criterion is mentioned (case-insensitive)
    """

    def __init__(
        self,
        *,
        client_id: NotBlankStr,
        min_length: int = _DEFAULT_MIN_LENGTH,
        min_words: int = _DEFAULT_MIN_WORDS,
    ) -> None:
        """Initialize the adversarial feedback strategy.

        Args:
            client_id: Identifier of the reviewing client.
            min_length: Minimum summary length in characters.
            min_words: Minimum number of distinct words.

        Raises:
            ValueError: If either threshold is not positive.
        """
        if min_length <= 0:
            msg = f"min_length must be > 0, got {min_length}"
            logger.warning(msg, client_id=client_id)
            raise ValueError(msg)
        if min_words <= 0:
            msg = f"min_words must be > 0, got {min_words}"
            logger.warning(msg, client_id=client_id)
            raise ValueError(msg)
        self._client_id = client_id
        self._min_length = min_length
        self._min_words = min_words

    async def evaluate(self, context: ReviewContext) -> ClientFeedback:
        """Apply strict checks to the deliverable.

        Args:
            context: Review context with criteria and deliverable.

        Returns:
            ``ClientFeedback`` that is rejected unless every strict
            check passes.
        """
        summary = context.deliverable_summary.strip()
        reasons: list[str] = []
        unmet: list[str] = []

        if len(summary) < self._min_length:
            reasons.append(
                f"summary shorter than {self._min_length} chars (got {len(summary)})"
            )

        distinct_words = len(set(summary.lower().split()))
        if distinct_words < self._min_words:
            reasons.append(
                f"fewer than {self._min_words} distinct words (got {distinct_words})"
            )

        summary_lower = summary.lower()
        unmet.extend(
            criterion
            for criterion in context.acceptance_criteria
            if criterion.lower() not in summary_lower
        )
        if unmet:
            reasons.append(f"{len(unmet)} acceptance criteria not explicitly addressed")

        accepted = not reasons
        logger.debug(
            CLIENT_REVIEW_COMPLETED,
            task_id=context.task_id,
            client_id=self._client_id,
            accepted=accepted,
            failure_count=len(reasons),
            strategy="adversarial",
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
            reason="Adversarial review: " + "; ".join(reasons),
            unmet_criteria=tuple(unmet),
        )
