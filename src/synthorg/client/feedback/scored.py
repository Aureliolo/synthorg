"""Multi-dimensional scored feedback strategy."""

import hashlib
import struct
from typing import Final

from synthorg.client.models import ClientFeedback, ReviewContext
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.client import CLIENT_REVIEW_COMPLETED

logger = get_logger(__name__)
_DEFAULT_PASSING_SCORE: Final[float] = 0.7


class ScoredFeedback:
    """Per-criterion scoring feedback strategy.

    Produces a deterministic score per acceptance criterion and
    aggregates into a weighted mean. A criterion mentioned in the
    deliverable summary (case-insensitive substring) scores a full
    ``1.0``; otherwise the score comes from a stable BLAKE2 hash of
    ``deliverable || criterion`` mapped into ``[0.3, 0.8]``.

    Accepts when the mean score meets or exceeds
    ``passing_score * strictness_multiplier`` and no criterion is
    below ``passing_score`` individually.
    """

    # Floor and width of the deterministic fallback-score band.  The
    # hashed score lands in ``[0.3, 0.8]`` so the band straddles the
    # default ``passing_score`` of 0.7 -- some criteria pass, some
    # don't, producing realistic mixed-acceptance behaviour without an
    # LLM call.
    _MIN_FALLBACK_SCORE: float = 0.3
    _FALLBACK_SCORE_RANGE: float = 0.5
    _DEFAULT_CRITERION: str = "__default__"

    def __init__(
        self,
        *,
        client_id: NotBlankStr,
        passing_score: float = _DEFAULT_PASSING_SCORE,
        strictness_multiplier: float = 1.0,
    ) -> None:
        """Initialize the scored feedback strategy.

        Args:
            client_id: Identifier of the reviewing client.
            passing_score: Minimum mean score to accept in
                ``[0.0, 1.0]``.
            strictness_multiplier: Positive scaling factor applied
                to ``passing_score``.

        Raises:
            ValueError: If inputs are out of valid range.
        """
        if not (0.0 <= passing_score <= 1.0):
            msg = f"passing_score must be in [0.0, 1.0], got {passing_score}"
            raise ValueError(msg)
        if strictness_multiplier <= 0:
            msg = f"strictness_multiplier must be > 0, got {strictness_multiplier}"
            raise ValueError(msg)
        self._client_id = client_id
        self._passing_score = passing_score
        self._strictness_multiplier = strictness_multiplier

    async def evaluate(self, context: ReviewContext) -> ClientFeedback:
        """Score every criterion and return aggregated feedback.

        Args:
            context: Review context with acceptance criteria and
                deliverable summary.

        Returns:
            ``ClientFeedback`` with per-criterion ``scores`` and an
            accept/reject verdict derived from the mean.
        """
        criteria: tuple[str, ...] = context.acceptance_criteria or (
            self._DEFAULT_CRITERION,
        )
        scores: dict[str, float] = {}
        unmet: list[str] = []
        for criterion in criteria:
            score = self._score_for(context.deliverable_summary, criterion)
            scores[criterion] = score
            if score < self._passing_score:
                unmet.append(criterion)

        mean = sum(scores.values()) / len(scores)
        effective_threshold = min(
            1.0, self._passing_score * self._strictness_multiplier
        )
        accepted = mean >= effective_threshold and not unmet

        logger.debug(
            CLIENT_REVIEW_COMPLETED,
            task_id=context.task_id,
            client_id=self._client_id,
            accepted=accepted,
            mean_score=mean,
            threshold=effective_threshold,
            strategy="scored",
        )

        if accepted:
            return ClientFeedback(
                task_id=context.task_id,
                client_id=self._client_id,
                accepted=True,
                scores=scores,
            )
        reason = (
            f"Mean score {mean:.2f} below threshold {effective_threshold:.2f}"
            if mean < effective_threshold
            else f"{len(unmet)} criteria below passing score"
        )
        return ClientFeedback(
            task_id=context.task_id,
            client_id=self._client_id,
            accepted=False,
            reason=reason,
            scores=scores,
            unmet_criteria=tuple(unmet),
        )

    def _score_for(self, deliverable: str, criterion: str) -> float:
        """Produce a deterministic score in ``[0.0, 1.0]``.

        Full credit when the criterion text appears in the
        deliverable summary; otherwise a stable hash-derived score
        in ``[0.3, 0.8]``.

        Returns:
            ``1.0`` when the criterion text appears in the deliverable,
            otherwise a stable hash-derived score in ``[0.3, 0.8]``.
        """
        if criterion.lower() in deliverable.lower():
            return 1.0
        digest = hashlib.blake2b(
            f"{deliverable}||{criterion}".encode(),
            digest_size=8,
        ).digest()
        (hash_val,) = struct.unpack(">Q", digest)
        fraction = (hash_val % 1000) / 1000
        return float(
            self._MIN_FALLBACK_SCORE + fraction * self._FALLBACK_SCORE_RANGE,
        )
