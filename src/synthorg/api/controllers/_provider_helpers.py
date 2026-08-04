"""Module-level helpers for the provider controller."""

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
    API_PROVIDER_HEALTH_RECHECKED,
    API_PROVIDER_USAGE_ENRICHMENT_FAILED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.providers.health import ProviderHealthSummary
from synthorg.providers.management.dtos import TestConnectionRequest
from synthorg.providers.state import ProvidersStateSlice, provider_management_of
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


async def _resolve_health_summary(
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


async def _require_provider(app_state: AppState, name: str) -> None:
    """Reject a request naming a provider that is not configured.

    Raises:
        NotFoundError: If *name* is not a configured provider.
    """
    providers = await config_resolver_of(app_state).get_provider_configs()
    if name not in providers:
        msg = f"Provider {name!r} not found"
        logger.warning(API_RESOURCE_NOT_FOUND, resource="provider", name=name)
        raise NotFoundError(msg)


async def _call_provider(app_state: AppState, name: str) -> None:
    """Call one provider so its recorded health reflects the present.

    Contained per provider: a sweep across every provider must not lose the
    verdicts it already gathered because one of them raised, which a bare
    ``TaskGroup`` member would cause by cancelling its siblings.
    """
    try:
        _ = await provider_management_of(app_state).test_connection(
            name, TestConnectionRequest()
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_PROVIDER_HEALTH_RECHECKED,
            provider=name,
            note="recheck call failed; health keeps its recorded value",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


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
    await _require_provider(app_state, name)
    summary = await _resolve_health_summary(app_state, name)
    logger.debug(
        API_PROVIDER_HEALTH_QUERIED,
        provider=name,
        health_status=summary.health_status.value,
        calls_24h=summary.calls_last_24h,
    )
    return summary


async def recheck_provider_health(
    app_state: AppState,
    name: str,
) -> ProviderHealthSummary:
    """Call *name* now and report the health that call produces.

    The read can only replay what has already been recorded, so a provider
    whose fault an operator has just fixed keeps reporting it until
    something calls it again. This is that call, and it is a completion
    rather than the reachability sweep's ping because a provider configured
    without a ``base_url`` is ineligible for that sweep and would otherwise
    have no way to be rechecked at all.

    Returns:
        The health summary the fresh call produced.

    Raises:
        NotFoundError: If the provider is not found.
    """
    await _require_provider(app_state, name)
    await _call_provider(app_state, name)
    summary = await _resolve_health_summary(app_state, name)
    logger.info(
        API_PROVIDER_HEALTH_RECHECKED,
        provider=name,
        health_status=summary.health_status.value,
    )
    return summary


async def recheck_all_provider_health(
    app_state: AppState,
) -> dict[str, ProviderHealthSummary]:
    """Call every configured provider now and report the results.

    Returns:
        Mapping of provider name to the health its fresh call produced.
    """
    providers = await config_resolver_of(app_state).get_provider_configs()
    async with asyncio.TaskGroup() as tg:
        for name in providers:
            _ = tg.create_task(_call_provider(app_state, name))
    async with asyncio.TaskGroup() as tg:
        summaries = {
            name: tg.create_task(_resolve_health_summary(app_state, name))
            for name in providers
        }
    return {name: task.result() for name, task in summaries.items()}


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
