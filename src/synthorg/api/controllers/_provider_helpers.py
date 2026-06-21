"""Module-level helpers for the provider controller."""

from datetime import UTC, datetime, timedelta

from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import ProviderUsageSummary
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_PROVIDER_USAGE_ENRICHMENT_FAILED
from synthorg.providers.health import ProviderHealthSummary

logger = get_logger(__name__)


def sse_error(msg: str) -> dict[str, object]:
    """Build a PullProgressEvent-shaped error dict for SSE.

    Returns:
        Mapping with the declared key/value types.
    """
    return {
        "status": msg,
        "progress_percent": None,
        "total_bytes": None,
        "completed_bytes": None,
        "error": msg,
        "done": True,
    }


async def fetch_provider_usage_24h(
    app_state: AppState,
    name: str,
) -> ProviderUsageSummary | None:
    """Fetch a provider's last-24h token/cost usage from the CostTracker.

    Depends only on *name*, so it can be awaited concurrently with the
    health-summary fetch and merged afterwards via :func:`apply_usage`.

    Returns:
        The 24h ``ProviderUsageSummary``, or ``None`` when the cost
        tracker is unwired or the lookup fails (a degraded read, logged).
    """
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if cost_tracker is None:
        return None
    try:
        now = datetime.now(UTC)
        return await cost_tracker.get_provider_usage(
            name,
            start=now - timedelta(hours=24),
            end=now,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_PROVIDER_USAGE_ENRICHMENT_FAILED,
            provider=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def apply_usage(
    summary: ProviderHealthSummary,
    usage: ProviderUsageSummary | None,
) -> ProviderHealthSummary:
    """Merge fetched 24h usage into a health summary.

    Returns:
        The summary unchanged when *usage* is ``None``, else a copy
        carrying the 24h token/cost totals.
    """
    if usage is None:
        return summary
    return summary.model_copy(
        update={
            "total_tokens_24h": usage.total_tokens,
            "total_cost_24h": usage.total_cost,
        },
    )
