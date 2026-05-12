"""Org-level inflection detection via snapshot comparison.

Compares two consecutive ``OrgSignalSnapshot`` objects and detects
significant metric changes that exceed configurable thresholds.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import OrgInflection
from synthorg.meta.models import OrgSignalSnapshot, RuleSeverity
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import COS_INFLECTION_DETECTED

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)
_DEFAULT_WARNING_THRESHOLD: Final[float] = 0.15
_DEFAULT_CRITICAL_THRESHOLD: Final[float] = 0.3

_EPSILON: Final[float] = 1e-9


def _perf_quality(s: OrgSignalSnapshot) -> float:
    return s.performance.avg_quality_score


def _perf_success(s: OrgSignalSnapshot) -> float:
    return s.performance.avg_success_rate


def _perf_collab(s: OrgSignalSnapshot) -> float:
    return s.performance.avg_collaboration_score


def _budget_spend(s: OrgSignalSnapshot) -> float:
    return s.budget.total_spend


def _budget_orch(s: OrgSignalSnapshot) -> float:
    return s.budget.orchestration_overhead


def _coord_overhead(s: OrgSignalSnapshot) -> float | None:
    return s.coordination.coordination_overhead_pct


def _error_count(s: OrgSignalSnapshot) -> float:
    return float(s.errors.total_findings)


_TRACKED_METRICS: tuple[
    tuple[str, str, Callable[[OrgSignalSnapshot], float | None]], ...
] = (
    ("quality_score", "performance", _perf_quality),
    ("success_rate", "performance", _perf_success),
    ("collaboration_score", "performance", _perf_collab),
    ("total_spend", "budget", _budget_spend),
    ("orchestration_overhead", "budget", _budget_orch),
    ("coordination_overhead_pct", "coordination", _coord_overhead),
    ("total_error_findings", "errors", _error_count),
)


class OrgInflectionDetector:
    """Detects org-level signal changes by comparing snapshots.

    For each tracked metric, computes the fractional change
    between two snapshots. Changes exceeding the warning or
    critical threshold produce ``OrgInflection`` events.

    Args:
        warning_threshold: Fractional change for WARNING (default 15%).
        critical_threshold: Fractional change for CRITICAL (default 30%).
    """

    def __init__(
        self,
        *,
        warning_threshold: float = _DEFAULT_WARNING_THRESHOLD,
        critical_threshold: float = _DEFAULT_CRITICAL_THRESHOLD,
    ) -> None:
        self._warning = warning_threshold
        self._critical = critical_threshold

    async def detect(
        self,
        *,
        previous: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
    ) -> tuple[OrgInflection, ...]:
        """Compare two snapshots and return detected inflections.

        Args:
            previous: Earlier snapshot.
            current: Later snapshot.

        Returns:
            Tuple of inflections for metrics that changed
            beyond the warning threshold.
        """
        inflections: list[OrgInflection] = []
        now = datetime.now(UTC)
        for name, domain, extractor in _TRACKED_METRICS:
            old_val = extractor(previous)
            new_val = extractor(current)
            if old_val is None or new_val is None:
                continue
            inflection = self._check_metric(
                name,
                domain,
                float(old_val),
                float(new_val),
                now,
            )
            if inflection is not None:
                inflections.append(inflection)
        return tuple(inflections)

    def _check_metric(
        self,
        name: str,
        domain: str,
        old_val: float,
        new_val: float,
        now: datetime,
    ) -> OrgInflection | None:
        """Check a single metric for significant change."""
        if old_val == 0.0 and new_val == 0.0:
            return None
        ratio = abs(new_val - old_val) / max(
            abs(old_val),
            abs(new_val),
            _EPSILON,
        )
        if ratio < self._warning:
            return None
        severity = (
            RuleSeverity.CRITICAL if ratio >= self._critical else RuleSeverity.WARNING
        )
        direction = "increased" if new_val > old_val else "decreased"
        description = (
            f"{name} {direction} by {ratio:.0%} (from {old_val:.4g} to {new_val:.4g})"
        )
        inflection = OrgInflection(
            severity=severity,
            affected_domains=(NotBlankStr(domain),),
            metric_name=NotBlankStr(name),
            old_value=old_val,
            new_value=new_val,
            description=NotBlankStr(description),
            detected_at=now,
        )
        logger.info(
            COS_INFLECTION_DETECTED,
            metric=name,
            domain=domain,
            severity=severity.value,
            change_ratio=ratio,
        )
        return inflection
