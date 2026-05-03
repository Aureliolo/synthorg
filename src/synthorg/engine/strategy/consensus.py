"""Consensus velocity detection for trendslop mitigation.

Detects when a group of agents reaches consensus too quickly (premature
consensus, "trendslop"). Uses text similarity analysis to identify when
diverse positions are collapsing into unanimity, triggering mitigation
actions like devil's advocate or escalation.
"""

import difflib
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.engine.strategy.models import (  # noqa: TC001
    ConsensusAction,
    ConsensusVelocityConfig,
)
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import (
    STRATEGY_CONSENSUS_CONFIG_INVALID,
    STRATEGY_CONSENSUS_DETECTED,
    STRATEGY_CONSENSUS_INIT,
    STRATEGY_CONSENSUS_NOT_DETECTED,
)

logger = get_logger(__name__)


class ConsensusVelocityResult(BaseModel):
    """Result of consensus velocity analysis.

    Attributes:
        detected: Whether premature consensus was detected.
        action: Action to take (None if not detected).
        mean_similarity: Mean pairwise text similarity (0.0-1.0).
        disagreement_count: Number of substantially different position pairs.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    detected: bool = Field(description="Whether premature consensus was detected")
    action: ConsensusAction | None = Field(
        default=None,
        description="Action to take (None if not detected)",
    )
    mean_similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="Mean pairwise text similarity",
    )
    disagreement_count: int = Field(
        ge=0,
        description="Number of substantially different position pairs",
    )

    @model_validator(mode="after")
    def _validate_action_consistency(self) -> Self:
        """Ensure action/detected are consistent."""
        if self.detected and self.action is None:
            msg = "action must not be None when detected is True"
            raise ValueError(msg)
        if not self.detected and self.action is not None:
            msg = "action must be None when detected is False"
            raise ValueError(msg)
        return self


_MIN_POSITION_PAIRS: int = 2
_SUBSTANTIAL_DIFF_THRESHOLD: float = 0.5


class ConsensusVelocityDetector:
    """Detect premature consensus using text similarity.

    Uses difflib.SequenceMatcher for pure-Python string similarity
    analysis. Detects consensus as "premature" when:
    - Mean pairwise similarity exceeds threshold, AND
    - Disagreement count falls below minimum threshold

    This catches situations where agents are using different words to
    express the same idea, or have converged too quickly on similar
    recommendations.
    """

    def __init__(self, *, min_disagreements: int = 2) -> None:
        """Initialize detector.

        Args:
            min_disagreements: Minimum number of substantially different
                position pairs to consider consensus as non-premature.
                Default is 2 (at least one pair of significantly different
                positions keeps consensus from being "too fast").

        Raises:
            TypeError: If min_disagreements is not an int or is a bool.
            ValueError: If min_disagreements is negative.
        """
        if isinstance(min_disagreements, bool) or not isinstance(
            min_disagreements,
            int,
        ):
            msg = "min_disagreements must be an int"
            logger.warning(
                STRATEGY_CONSENSUS_CONFIG_INVALID,
                min_disagreements=min_disagreements,
                value_type=type(min_disagreements).__name__,
                reason=msg,
            )
            raise TypeError(msg)
        if min_disagreements < 0:
            msg = "min_disagreements must be >= 0"
            logger.warning(
                STRATEGY_CONSENSUS_CONFIG_INVALID,
                min_disagreements=min_disagreements,
                reason=msg,
            )
            raise ValueError(msg)
        self._min_disagreements = min_disagreements
        logger.debug(
            STRATEGY_CONSENSUS_INIT,
            min_disagreements=min_disagreements,
        )

    def detect(
        self,
        positions: tuple[str, ...],
        config: ConsensusVelocityConfig,
    ) -> ConsensusVelocityResult:
        """Analyze positions for premature consensus.

        Args:
            positions: Text positions from agents (e.g., recommendations).
            config: Configuration including similarity threshold and action.

        Returns:
            ConsensusVelocityResult with detection status and metrics.
        """
        if len(positions) < _MIN_POSITION_PAIRS:
            logger.info(
                STRATEGY_CONSENSUS_NOT_DETECTED,
                num_positions=len(positions),
                reason="insufficient positions",
            )
            return ConsensusVelocityResult(
                detected=False,
                mean_similarity=1.0,
                disagreement_count=0,
            )

        # Compute pairwise similarity across all position pairs
        similarities: list[float] = []
        disagreements = 0

        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                ratio = difflib.SequenceMatcher(
                    None, positions[i], positions[j]
                ).ratio()
                similarities.append(ratio)

                if ratio < _SUBSTANTIAL_DIFF_THRESHOLD:
                    disagreements += 1

        rounded_mean = round(sum(similarities) / len(similarities), 4)

        is_premature = (
            rounded_mean > config.threshold and disagreements < self._min_disagreements
        )

        action = config.action if is_premature else None

        event = (
            STRATEGY_CONSENSUS_DETECTED
            if is_premature
            else STRATEGY_CONSENSUS_NOT_DETECTED
        )
        logger.info(
            event,
            detected=is_premature,
            action=action,
            mean_similarity=rounded_mean,
            disagreement_count=disagreements,
        )

        return ConsensusVelocityResult(
            detected=is_premature,
            action=action,
            mean_similarity=rounded_mean,
            disagreement_count=disagreements,
        )
