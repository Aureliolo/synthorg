"""Reading what is already known about a provider.

The recheck side, which calls the provider and can trigger a reconciler pass,
lives in ``_provider_recheck`` and consumes the two lookups exported here.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import ProviderUsageSummary
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PROVIDER_HEALTH_QUERIED,
    API_PROVIDER_USAGE_ENRICHMENT_FAILED,
)
from synthorg.providers.health import ProviderHealthSummary
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import config_resolver_of

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


async def resolve_health_summary(
    app_state: AppState,
    name: str,
) -> ProviderHealthSummary:
    """Build *name*'s health summary, enriched with its 24h usage.

    The summary fetch and the usage fetch both depend only on *name*, so
    they run concurrently and merge.

    Returns:
        The provider's current health summary.
    """
    health_tracker = require_service(
        app_state.slice(ProvidersStateSlice).health_tracker,
        "Provider Health Tracker",
    )
    async with asyncio.TaskGroup() as tg:
        summary_task = tg.create_task(health_tracker.get_summary(name))
        usage_task = tg.create_task(fetch_provider_usage_24h(app_state, name))
    return apply_usage(summary_task.result(), usage_task.result())


async def require_provider(app_state: AppState, name: str) -> None:
    """Reject a request naming a provider that is not configured.

    Raises:
        NotFoundError: If *name* is not a configured provider.
    """
    providers = await config_resolver_of(app_state).get_provider_configs()
    if name not in providers:
        msg = f"Provider {name!r} not found"
        raise NotFoundError(msg)


async def read_provider_health(
    app_state: AppState,
    name: str,
) -> ProviderHealthSummary:
    """Report what has been recorded about *name*'s health.

    Returns:
        The provider's current health summary.

    Raises:
        NotFoundError: If the provider is not found.
    """
    await require_provider(app_state, name)
    summary = await resolve_health_summary(app_state, name)
    logger.debug(
        API_PROVIDER_HEALTH_QUERIED,
        provider=name,
        health_status=summary.health_status.value,
        calls_24h=summary.calls_last_24h,
    )
    return summary


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
