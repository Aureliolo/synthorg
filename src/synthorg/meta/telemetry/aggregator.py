"""Cross-deployment pattern aggregation.

Pure function that groups anonymized events by (source_rule, altitude),
computes cross-deployment statistics, and filters by minimum deployment
count.
"""

from collections import Counter, defaultdict
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.telemetry.models import AggregatedPattern, AnonymizedOutcomeEvent
from synthorg.observability import get_logger

logger = get_logger(__name__)


_DEFAULT_MIN_DEPLOYMENTS: Final[int] = 3


def aggregate_patterns(
    events: tuple[AnonymizedOutcomeEvent, ...],
    *,
    min_deployments: int = _DEFAULT_MIN_DEPLOYMENTS,
) -> tuple[AggregatedPattern, ...]:
    """Identify cross-deployment patterns from anonymized events.

    Groups events by ``(source_rule, altitude)`` and computes
    aggregated statistics. Only groups observed across at least
    ``min_deployments`` unique deployments are included.

    Args:
        events: All collected anonymized events.
        min_deployments: Minimum unique deployments required.

    Returns:
        Aggregated patterns sorted by deployment count (descending),
        then by total events (descending).

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if min_deployments < 1:
        msg = f"min_deployments must be >= 1, got {min_deployments}"
        raise ValueError(msg)
    if not events:
        return ()

    # Group events by (source_rule, altitude).
    # Events without a source_rule are excluded -- no rule means
    # no actionable threshold to recommend adjusting.
    groups: dict[tuple[str, str], list[AnonymizedOutcomeEvent]] = defaultdict(list)
    for event in events:
        if event.source_rule is None:
            continue
        key = (event.source_rule, event.altitude)
        groups[key].append(event)

    patterns: list[AggregatedPattern] = []
    for (rule, altitude), group_events in groups.items():
        deployment_ids = {e.deployment_id for e in group_events}
        if len(deployment_ids) < min_deployments:
            continue

        decisions = [e for e in group_events if e.event_type == "proposal_decision"]
        rollouts = [e for e in group_events if e.event_type == "rollout_result"]

        approval_rate = _compute_approval_rate(decisions)
        success_rate = _compute_success_rate(rollouts)
        avg_confidence = _compute_avg_confidence(decisions)
        avg_observation = _compute_avg_observation_hours(rollouts)
        industry = _compute_industry_breakdown(group_events)

        patterns.append(
            AggregatedPattern(
                source_rule=NotBlankStr(rule),
                altitude=NotBlankStr(altitude),
                deployment_count=len(deployment_ids),
                total_events=len(group_events),
                decision_count=len(decisions),
                approval_rate=approval_rate,
                success_rate=success_rate,
                avg_confidence=avg_confidence,
                avg_observation_hours=avg_observation,
                industry_breakdown=industry,
            ),
        )

    patterns.sort(key=lambda p: (-p.deployment_count, -p.total_events))
    return tuple(patterns)


def _compute_approval_rate(
    decisions: list[AnonymizedOutcomeEvent],
) -> float:
    """Compute approval rate from decision events.

    Returns:
        Resulting numeric value.
    """
    if not decisions:
        return 0.0
    approved = sum(1 for d in decisions if d.decision == "approved")
    return approved / len(decisions)


def _compute_success_rate(
    rollouts: list[AnonymizedOutcomeEvent],
) -> float:
    """Compute rollout success rate.

    Returns:
        Resulting numeric value.
    """
    if not rollouts:
        return 0.0
    successes = sum(1 for r in rollouts if r.rollout_outcome == "success")
    return successes / len(rollouts)


def _compute_avg_confidence(
    decisions: list[AnonymizedOutcomeEvent],
) -> float:
    """Compute average confidence from decision events.

    Returns:
        Resulting numeric value.
    """
    confidences = [d.confidence for d in decisions if d.confidence is not None]
    if not confidences:
        return 0.0
    return sum(confidences) / len(confidences)


def _compute_avg_observation_hours(
    rollouts: list[AnonymizedOutcomeEvent],
) -> float | None:
    """Compute average observation hours from rollout events.

    Returns:
        The ``float`` value when present, ``None`` otherwise.
    """
    hours = [r.observation_hours for r in rollouts if r.observation_hours is not None]
    if not hours:
        return None
    return sum(hours) / len(hours)


def _compute_industry_breakdown(
    events: list[AnonymizedOutcomeEvent],
) -> tuple[tuple[NotBlankStr, int], ...]:
    """Compute industry tag distribution, sorted by count descending.

    Returns:
        Tuple of the declared element types.
    """
    counter: Counter[str] = Counter()
    for event in events:
        if event.industry_tag is not None:
            counter[event.industry_tag] += 1
    return tuple((NotBlankStr(tag), count) for tag, count in counter.most_common())
