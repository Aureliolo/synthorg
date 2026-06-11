"""Module-level helpers for the provider controller."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_PROVIDER_USAGE_ENRICHMENT_FAILED

if TYPE_CHECKING:
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


async def enrich_with_usage(
    summary: ProviderHealthSummary,
    app_state: AppState,
    name: str,
) -> ProviderHealthSummary:
    """Enrich a health summary with token/cost data from CostTracker.

    Returns:
        ``ProviderHealthSummary`` instance.
    """
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if cost_tracker is None:
        return summary
    try:
        now = datetime.now(UTC)
        usage = await cost_tracker.get_provider_usage(
            name,
            start=now - timedelta(hours=24),
            end=now,
        )
        return summary.model_copy(
            update={
                "total_tokens_24h": usage.total_tokens,
                "total_cost_24h": usage.total_cost,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_PROVIDER_USAGE_ENRICHMENT_FAILED,
            provider=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return summary
