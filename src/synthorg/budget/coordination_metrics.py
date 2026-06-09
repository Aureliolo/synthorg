"""Coordination metrics for multi-agent system tuning.

Pure computation functions for nine coordination metrics defined in
the Operations design page (Coordination Metrics): efficiency, overhead, error
amplification, message density, redundancy rate, Amdahl ceiling, straggler
gap, token/speedup ratio, and message overhead. Metric model shapes live in
:mod:`synthorg.budget.coordination_metric_models`.
"""

import math
import statistics
from collections.abc import Sequence
from typing import Final

from synthorg.budget.coordination_metric_models import (
    AmdahlCeiling,
    CoordinationEfficiency,
    CoordinationOverhead,
    ErrorAmplification,
    MessageDensity,
    MessageOverhead,
    RedundancyRate,
    StragglerGap,
    TokenSpeedupRatio,
)
from synthorg.observability import get_logger
from synthorg.observability.events.coordination_metrics import (
    COORD_METRICS_VALIDATION_ERROR,
)

logger = get_logger(__name__)


def compute_efficiency(
    *,
    success_rate: float,
    turns_mas: float,
    turns_sas: float,
) -> CoordinationEfficiency:
    """Compute coordination efficiency.

    Args:
        success_rate: Multi-agent task success rate (0.0-1.0).
        turns_mas: Average turns for multi-agent tasks.
        turns_sas: Average turns for single-agent tasks.

    Returns:
        Coordination efficiency model.

    Raises:
        ValueError: If ``turns_sas`` is zero or negative.
        ValidationError: If ``turns_mas`` is zero or negative
            (enforced by ``Field(gt=0)``).
    """
    if turns_sas <= 0:
        msg = "turns_sas must be positive (cannot divide by zero)"
        raise ValueError(msg)
    return CoordinationEfficiency(
        success_rate=success_rate,
        turns_mas=turns_mas,
        turns_sas=turns_sas,
    )


def compute_overhead(
    *,
    turns_mas: float,
    turns_sas: float,
) -> CoordinationOverhead:
    """Compute coordination overhead percentage.

    Args:
        turns_mas: Average turns for multi-agent tasks.
        turns_sas: Average turns for single-agent tasks.

    Returns:
        Coordination overhead model.

    Raises:
        ValueError: If ``turns_sas`` is zero or negative.
        ValidationError: If ``turns_mas`` is zero or negative
            (enforced by ``Field(gt=0)``).
    """
    if turns_sas <= 0:
        msg = "turns_sas must be positive (cannot divide by zero)"
        raise ValueError(msg)
    return CoordinationOverhead(
        turns_mas=turns_mas,
        turns_sas=turns_sas,
    )


def compute_error_amplification(
    *,
    error_rate_mas: float,
    error_rate_sas: float,
) -> ErrorAmplification:
    """Compute error amplification factor.

    Args:
        error_rate_mas: Multi-agent error rate.
        error_rate_sas: Single-agent error rate.

    Returns:
        Error amplification model.

    Raises:
        ValueError: If ``error_rate_sas`` is zero or negative.
    """
    if error_rate_sas <= 0:
        msg = "error_rate_sas must be positive (cannot divide by zero)"
        raise ValueError(msg)
    return ErrorAmplification(
        error_rate_mas=error_rate_mas,
        error_rate_sas=error_rate_sas,
    )


def compute_message_density(
    *,
    inter_agent_messages: int,
    reasoning_turns: int,
) -> MessageDensity:
    """Compute message density.

    Args:
        inter_agent_messages: Number of inter-agent messages.
        reasoning_turns: Number of reasoning turns.

    Returns:
        Message density model.

    Raises:
        ValueError: If ``reasoning_turns`` is zero or negative.
    """
    if reasoning_turns <= 0:
        msg = "reasoning_turns must be positive (cannot divide by zero)"
        raise ValueError(msg)
    return MessageDensity(
        inter_agent_messages=inter_agent_messages,
        reasoning_turns=reasoning_turns,
    )


def compute_redundancy_rate(
    *,
    similarities: Sequence[float],
) -> RedundancyRate:
    """Compute redundancy rate from pairwise similarity scores.

    Args:
        similarities: Sequence of similarity scores (each 0.0-1.0).

    Returns:
        Redundancy rate model.

    Raises:
        ValueError: If any similarity value is outside [0, 1].
        ValueError: If the sequence is empty.
    """
    if not similarities:
        msg = "similarities must not be empty"
        raise ValueError(msg)
    for val in similarities:
        if not 0.0 <= val <= 1.0:
            msg = f"Similarity value {val} is outside [0, 1]"
            raise ValueError(msg)
    value = statistics.mean(similarities)
    return RedundancyRate(
        value=value,
        sample_count=len(similarities),
    )


def compute_amdahl_ceiling(
    *,
    parallelizable_fraction: float,
) -> AmdahlCeiling:
    """Compute Amdahl's Law speedup ceiling.

    Args:
        parallelizable_fraction: Fraction of workload that can
            be parallelized (0.0--<1.0).

    Returns:
        Amdahl ceiling model with max speedup and recommended
        team size.

    Raises:
        ValidationError: If ``parallelizable_fraction`` is outside
            [0.0, 1.0) (enforced by ``Field``).
    """
    return AmdahlCeiling(
        parallelizable_fraction=parallelizable_fraction,
    )


def compute_straggler_gap(
    *,
    agent_durations: Sequence[tuple[str, float]],
) -> StragglerGap:
    """Compute straggler gap from agent completion durations.

    Args:
        agent_durations: Sequence of ``(agent_id, duration_seconds)``
            pairs.

    Returns:
        Straggler gap model.

    Raises:
        ValueError: If ``agent_durations`` is empty or contains
            invalid entries.
    """
    if not agent_durations:
        msg = "agent_durations must not be empty"
        logger.warning(
            COORD_METRICS_VALIDATION_ERROR,
            parameter="agent_durations",
            error=msg,
        )
        raise ValueError(msg)

    for agent_id, duration in agent_durations:
        if not agent_id or not agent_id.strip():
            msg = "agent_id must not be blank"
            logger.warning(
                COORD_METRICS_VALIDATION_ERROR,
                parameter="agent_id",
                value=agent_id,
                error=msg,
            )
            raise ValueError(msg)
        if not math.isfinite(duration) or duration < 0:
            msg = "duration_seconds must be finite and non-negative"
            logger.warning(
                COORD_METRICS_VALIDATION_ERROR,
                parameter="duration_seconds",
                agent_id=agent_id,
                value=duration,
                error=msg,
            )
            raise ValueError(msg)

    slowest_id, slowest_dur = max(
        agent_durations,
        key=lambda x: x[1],
    )
    mean_dur = statistics.mean(d for _, d in agent_durations)
    return StragglerGap(
        slowest_duration_seconds=slowest_dur,
        mean_duration_seconds=mean_dur,
        slowest_agent_id=slowest_id,
    )


def compute_token_speedup_ratio(
    *,
    tokens_mas: float,
    tokens_sas: float,
    duration_mas: float,
    duration_sas: float,
) -> TokenSpeedupRatio:
    """Compute token cost vs latency speedup ratio.

    Args:
        tokens_mas: Total tokens for multi-agent execution.
        tokens_sas: Total tokens for single-agent baseline.
        duration_mas: Wall-clock duration for multi-agent (seconds).
        duration_sas: Wall-clock duration for single-agent (seconds).

    Returns:
        Token speedup ratio model (alerts when ratio exceeds
        :data:`~synthorg.budget.coordination_metric_models._DEFAULT_TOKEN_SPEEDUP_ALERT_RATIO`).

    Raises:
        ValueError: If any input is non-finite, zero, or negative.
    """
    for name, value in (
        ("tokens_mas", tokens_mas),
        ("tokens_sas", tokens_sas),
        ("duration_mas", duration_mas),
        ("duration_sas", duration_sas),
    ):
        if not math.isfinite(value) or value <= 0:
            msg = f"{name} must be finite and positive"
            logger.warning(
                COORD_METRICS_VALIDATION_ERROR,
                parameter=name,
                value=value,
                error=msg,
            )
            raise ValueError(msg)
    return TokenSpeedupRatio(
        token_multiplier=tokens_mas / tokens_sas,
        latency_speedup=duration_sas / duration_mas,
    )


_DEFAULT_QUADRATIC_THRESHOLD: Final[float] = 0.5


def compute_message_overhead(
    *,
    team_size: int,
    message_count: int,
    quadratic_threshold: float = _DEFAULT_QUADRATIC_THRESHOLD,
) -> MessageOverhead:
    """Compute message overhead and detect O(n^2) growth.

    Args:
        team_size: Number of agents.
        message_count: Actual inter-agent message count.
        quadratic_threshold: Fraction of n^2 for alert (0.0--1.0).

    Returns:
        Message overhead model.
    """
    return MessageOverhead(
        team_size=team_size,
        message_count=message_count,
        quadratic_threshold=quadratic_threshold,
    )
