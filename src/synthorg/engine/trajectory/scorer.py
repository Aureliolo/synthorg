"""Trajectory scorer for best-of-K candidate selection.

Implements three uncertainty signals from SRLM (arXiv:2603.15653):
self-consistency filter, verbalized confidence (VC), and trace
length (Len).  Joint scoring: ``s(p) = VC(p) + Len(p)``;
least-negative wins.
"""

import math
from collections import Counter
from typing import Final

from synthorg.engine.trajectory.models import (
    CandidateResult,
    TrajectoryScore,
)
from synthorg.observability import get_logger
from synthorg.observability.events.trajectory import (
    TRAJECTORY_BEST_SELECTED,
    TRAJECTORY_CANDIDATE_SCORED,
    TRAJECTORY_CONSISTENCY_FILTERED,
    TRAJECTORY_SCORING_START,
)

logger = get_logger(__name__)

# Verbalized confidence is expressed as a percentage in [0, 100];
# dividing by this scale yields a probability in [0, 1] for log-space VC.
_CONFIDENCE_PERCENT_SCALE: Final[float] = 100.0
# Floor assigned to zero/negative confidence after the log-space transform.
_NEGATIVE_CONFIDENCE_FLOOR: Final[float] = -100.0


class TrajectoryScorer:
    """Score and select the best trajectory candidate.

    Scoring pipeline:

    1. **Self-consistency filter**: Majority-vote on final tool call
       fingerprints.  Candidates disagreeing with the majority are
       marked inconsistent.
    2. **Verbalized confidence (VC)**: ``log(nu / 100)`` where ``nu``
       is the candidate's verbalized confidence (0 to 100).  Log-space
       so low confidence is heavily penalized.
    3. **Trace length (Len)**: ``-total_output_tokens`` (shorter is
       more confident).
    4. **Joint**: ``VC + Len``; least-negative wins.

    When VC is unavailable (``None``), scoring degrades gracefully
    to Len-only (VC score set to 0.0).
    """

    def score_candidates(
        self,
        candidates: tuple[CandidateResult, ...],
    ) -> tuple[TrajectoryScore, ...]:
        """Score all candidates.

        Args:
            candidates: Candidate execution results to score.

        Returns:
            Tuple of scores in the same order as candidates.

        Raises:
            ValueError: If candidates is empty.
        """
        if not candidates:
            msg = "Cannot score empty candidate list"
            logger.warning(
                TRAJECTORY_SCORING_START,
                k=0,
                error=msg,
            )
            raise ValueError(msg)

        logger.debug(
            TRAJECTORY_SCORING_START,
            k=len(candidates),
        )

        # Step 1: self-consistency filter.
        consistency = _check_consistency(candidates)

        # Step 2+3: score each candidate.
        scores: list[TrajectoryScore] = []
        for candidate in candidates:
            vc = _compute_vc_score(candidate)
            length = -candidate.trace_tokens
            is_consistent = consistency[candidate.candidate_index]

            score = TrajectoryScore(
                candidate_index=candidate.candidate_index,
                vc_score=vc,
                len_score=float(length),
                consistent=is_consistent,
            )
            scores.append(score)

            logger.debug(
                TRAJECTORY_CANDIDATE_SCORED,
                candidate_index=candidate.candidate_index,
                vc_score=vc,
                len_score=float(length),
                joint_score=score.joint_score,
                consistent=is_consistent,
            )

        return tuple(scores)

    def select_best(
        self,
        candidates: tuple[CandidateResult, ...],
    ) -> CandidateResult:
        """Score candidates and select the best one.

        Selection priority:
        1. Among consistent candidates, pick highest joint_score.
        2. If no consistent candidates, pick highest joint_score
           among all candidates.

        Args:
            candidates: Candidate execution results.

        Returns:
            The best candidate.

        Raises:
            ValueError: If candidates is empty.
        """
        scores = self.score_candidates(candidates)
        by_index = {c.candidate_index: c for c in candidates}

        # Prefer consistent candidates.
        consistent_scores = [s for s in scores if s.consistent]
        if consistent_scores:
            best_score = max(
                consistent_scores,
                key=lambda s: s.joint_score,
            )
        else:
            best_score = max(scores, key=lambda s: s.joint_score)

        best_candidate = by_index[best_score.candidate_index]

        logger.info(
            TRAJECTORY_BEST_SELECTED,
            candidate_index=best_score.candidate_index,
            joint_score=best_score.joint_score,
            consistent=best_score.consistent,
            k=len(candidates),
        )

        return best_candidate


def _check_consistency(
    candidates: tuple[CandidateResult, ...],
) -> dict[int, bool]:
    """Check self-consistency via majority-vote on final fingerprints.

    Returns:
        Dict mapping candidate index to a consistency flag: ``True``
        for candidates aligned with the majority final fingerprint
        (or every candidate when no strict majority exists).
    """
    if len(candidates) <= 1:
        return {c.candidate_index: True for c in candidates}

    # Extract final turn fingerprints from each candidate.
    fingerprint_sets: list[tuple[str, ...]] = []
    for candidate in candidates:
        turns = candidate.execution_result.turns
        if turns and turns[-1].tool_call_fingerprints:
            fps = tuple(sorted(turns[-1].tool_call_fingerprints))
        else:
            fps = ()
        fingerprint_sets.append(fps)

    # Find majority fingerprint set (strict >50% threshold).
    counter: Counter[tuple[str, ...]] = Counter(fingerprint_sets)
    majority_fp, majority_count = counter.most_common(1)[0]
    has_majority = majority_count > len(candidates) / 2

    result: dict[int, bool] = {}
    filtered_count = 0
    for candidate, fps in zip(candidates, fingerprint_sets, strict=True):
        is_consistent = (fps == majority_fp) if has_majority else True
        result[candidate.candidate_index] = is_consistent
        if not is_consistent:
            filtered_count += 1

    if filtered_count > 0:
        logger.info(
            TRAJECTORY_CONSISTENCY_FILTERED,
            filtered=filtered_count,
            total=len(candidates),
        )

    return result


def _compute_vc_score(candidate: CandidateResult) -> float:
    """Compute log-space verbalized confidence score.

    ``VC(p) = log(nu / 100)`` where ``nu`` is the candidate's
    single verbalized confidence value (0 to 100).

    When verbalized_confidence is None, returns 0.0 (graceful
    degradation to Len-only scoring).

    Returns:
        ``log(nu / 100)`` for the candidate's verbalised confidence;
        ``0.0`` when the candidate carries no confidence value.
    """
    if candidate.verbalized_confidence is None:
        logger.debug(
            TRAJECTORY_CANDIDATE_SCORED,
            candidate_index=candidate.candidate_index,
            vc_unavailable=True,
            reason="verbalized_confidence is None, using Len-only",
        )
        return 0.0

    vc = candidate.verbalized_confidence
    if vc <= 0:
        logger.warning(
            TRAJECTORY_CANDIDATE_SCORED,
            candidate_index=candidate.candidate_index,
            vc=vc,
            reason=(f"zero or negative VC, flooring to {_NEGATIVE_CONFIDENCE_FLOOR}"),
        )
        return _NEGATIVE_CONFIDENCE_FLOOR
    return math.log(vc / _CONFIDENCE_PERCENT_SCALE)
