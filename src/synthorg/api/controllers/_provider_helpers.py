"""Module-level helpers for the provider controller."""

import asyncio
from datetime import UTC, datetime, timedelta

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.api.subsystems.runtime import reconciler_of
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import ProviderUsageSummary
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PROVIDER_HEALTH_QUERIED,
    API_PROVIDER_HEALTH_RECHECK_REFUSED,
    API_PROVIDER_HEALTH_RECHECKED,
    API_PROVIDER_RECHECK_RECONCILE_FAILED,
    API_PROVIDER_USAGE_ENRICHMENT_FAILED,
)
from synthorg.observability.events.provider import PROVIDER_HEALTH_PROBE_FAILED
from synthorg.providers.errors import ProviderTimeoutError, ProviderValidationError
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
        raise NotFoundError(msg)


async def _supersede_then_call(app_state: AppState, name: str) -> None:
    """Retire *name*'s stale liveness evidence, then call it.

    The order is the point. Marking the cutoff first means the call this
    makes is the only outcome deciding whether the provider is serving; doing
    it afterwards would leave the failures the operator has just fixed still
    voting, which is exactly the state where pressing Recheck changed nothing
    a person could see.

    Raises:
        ProviderTimeoutError: If the call outran the budget.
        Exception: Whatever else the call raised; see :func:`_call_provider`.
    """
    health_tracker = require_service(
        app_state.slice(ProvidersStateSlice).health_tracker,
        "Provider Health Tracker",
    )
    await health_tracker.supersede_liveness(name, at=app_state.clock.now())
    await _call_provider(app_state, name)


async def _call_provider(app_state: AppState, name: str) -> None:
    """Call one provider so its recorded health reflects the present.

    Bounded by ``api.health_recheck_timeout_seconds``: the call is awaited on
    the request, and it is a completion rather than a ping, so an unreachable
    provider would otherwise hold the response open for whatever the driver's
    own connect timeout happens to be. Timing out costs only this reading,
    which the provider's recorded health still answers.

    Raises:
        ProviderTimeoutError: If the call outran the budget. Translated from
            the bare ``TimeoutError`` ``asyncio.timeout`` raises, which the
            handler table can only read as an unexpected 500; a provider that
            was too slow is a retryable upstream condition (504), and saying
            so is what tells an operator to try again rather than to go
            looking for a fault in the dashboard.
        Exception: Whatever else the call raised. Callers decide whether that
            is fatal: the sweep contains it per provider, the single-provider
            recheck surfaces it rather than reporting a stale summary as new.
    """
    budget = await config_resolver_of(app_state).get_float(
        "api", "health_recheck_timeout_seconds"
    )
    try:
        async with asyncio.timeout(budget):
            _ = await provider_management_of(app_state).test_connection(
                name, TestConnectionRequest()
            )
    except TimeoutError as exc:
        msg = (
            f"Provider {name!r} did not answer within {budget}s "
            f"(api.health_recheck_timeout_seconds)"
        )
        raise ProviderTimeoutError(msg, context={"provider": name}) from exc


async def _require_affordable_fan_out(
    app_state: AppState,
    *,
    provider_count: int,
) -> None:
    """Refuse a sweep whose fan-out costs more than the operator allowed.

    The rate limit bounds how often this runs; it cannot bound what one run
    costs, because that scales with a number the requester does not choose.
    Every provider in the sweep is issued a real billed completion, so an
    install with many providers turns one permitted request into
    proportionally more spend.

    Args:
        app_state: Application state, for the resolver.
        provider_count: How many providers the sweep would call.

    Raises:
        ProviderValidationError: If *provider_count* exceeds
            ``api.health_recheck_max_providers``.
    """
    ceiling = await config_resolver_of(app_state).get_int(
        "api", "health_recheck_max_providers"
    )
    if provider_count <= ceiling:
        return
    msg = (
        f"Rechecking all {provider_count} providers exceeds the "
        f"{ceiling}-provider ceiling for one sweep, and each one costs a "
        f"billed completion. Raise api.health_recheck_max_providers, or "
        f"recheck providers individually."
    )
    logger.warning(
        API_PROVIDER_HEALTH_RECHECK_REFUSED,
        provider_count=provider_count,
        ceiling=ceiling,
    )
    raise ProviderValidationError(msg)


async def _call_provider_contained(app_state: AppState, name: str) -> bool:
    """Call one provider, keeping its failure to itself.

    The sweep's containment, and only the sweep's: a run across every provider
    must not lose the verdicts it already gathered because one of them raised,
    which a bare ``TaskGroup`` member would cause by cancelling its siblings.
    The single-provider recheck deliberately does not use this, because there
    it would turn a failed call into a stale summary presented as a fresh one.

    Returns:
        True when the call completed. The sweep needs to know, because a
        provider whose call never happened has only its previously recorded
        summary to report, and this endpoint's whole promise is that what it
        returns came from a call it just made.

    Raises:
        asyncio.CancelledError: Propagated so shutdown is not swallowed.
    """
    try:
        await _supersede_then_call(app_state, name)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; see below
        # lint-allow: swallow-ok -- one provider's fault must not discard the
        # sweep's other verdicts; the caller omits this provider instead.
        reraise_critical(exc)
        logger.warning(
            PROVIDER_HEALTH_PROBE_FAILED,
            provider=name,
            note="recheck call failed; provider omitted from the sweep",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    return True


async def _reconcile_dependents(app_state: AppState, *, trigger: str) -> None:
    """Re-attempt whatever was blocked on the provider that just answered.

    A recheck is an operator saying they fixed something upstream, and the
    subsystems that gave up on it are the reason they care. Memory is the
    worked example: an unreachable embedding model leaves ``memory_backend``
    blocked, and without this the operator's fix waits for the next periodic
    sweep while every agent keeps running with no recall.

    ``retry_declined`` is set for the same reason: those subsystems declined
    on a condition their declaration cannot model, so a pass that skips
    already-declined activations would skip precisely the ones this is for.

    Contained: the recheck's own answer is already correct and returning it
    matters more than the follow-on pass, so a reconciler fault is logged
    rather than turned into a failed recheck.

    Raises:
        asyncio.CancelledError: Propagated so shutdown is not swallowed.
    """
    try:
        _ = await reconciler_of(app_state).reconcile(
            app_state, trigger=trigger, retry_declined=True
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; see below
        # lint-allow: swallow-ok -- the recheck verdict this follows is
        # already computed and returned; only the follow-on pass is lost, and
        # the periodic sweep runs it again.
        reraise_critical(exc)
        logger.warning(
            API_PROVIDER_RECHECK_RECONCILE_FAILED,
            trigger=trigger,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _safe_resolve_health_summary(
    app_state: AppState,
    name: str,
) -> ProviderHealthSummary | None:
    """Read *name*'s summary, or ``None`` if the read itself failed.

    Contained for the same reason the call is: by the time summaries are read
    every billed completion has already succeeded, so letting one provider's
    read cancel its siblings would discard work that cannot be cheaply redone.

    Returns:
        The provider's summary, or ``None`` when it could not be read.

    Raises:
        asyncio.CancelledError: Propagated so shutdown is not swallowed.
    """
    try:
        return await _resolve_health_summary(app_state, name)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; see below
        # lint-allow: swallow-ok -- the sweep reports the providers it could
        # read; omitting one beats discarding every other provider's result.
        reraise_critical(exc)
        logger.warning(
            PROVIDER_HEALTH_PROBE_FAILED,
            provider=name,
            note="health summary could not be read after a recheck",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


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

    The call is deliberately uncontained here. Swallowing it would return the
    summary that was already on file under a promise that it reflects a fresh
    call, so an operator who had just fixed the provider would read their fix
    as having failed. A provider that is merely unreachable is not this case:
    that answers normally with an unsuccessful verdict, which is recorded and
    reported like any other.

    Returns:
        The health summary the fresh call produced.

    Raises:
        NotFoundError: If the provider is not found.
        ProviderTimeoutError: If the call outran
            ``api.health_recheck_timeout_seconds``, which answers 504 and
            retryable rather than 500.
    """
    await _require_provider(app_state, name)
    await _supersede_then_call(app_state, name)
    summary = await _resolve_health_summary(app_state, name)
    logger.info(
        API_PROVIDER_HEALTH_RECHECKED,
        provider=name,
        health_status=summary.health_status.value,
    )
    await _reconcile_dependents(app_state, trigger=f"provider_recheck:{name}")
    return summary


async def recheck_all_provider_health(
    app_state: AppState,
) -> dict[str, ProviderHealthSummary]:
    """Call every configured provider now and report the results.

    Each provider is called and then read in one task, rather than calling
    every provider and only then reading every summary: the summaries are
    per-provider with nothing to share, so a barrier between the two halves
    would make every provider wait for the slowest call before any summary
    is read, for no gain.

    Returns:
        Mapping of provider name to the health its fresh call produced. A
        provider is omitted when its call failed or its summary could not be
        read, because this endpoint's answer is "what calling it just found";
        returning the summary already on file for a provider that was never
        successfully called would present a stale verdict as a fresh one.

    Raises:
        ProviderFanOutTooLargeError: If the configured provider count exceeds
            ``api.health_recheck_max_providers``.
    """
    providers = await config_resolver_of(app_state).get_provider_configs()
    await _require_affordable_fan_out(app_state, provider_count=len(providers))

    async def _recheck(name: str) -> ProviderHealthSummary | None:
        if not await _call_provider_contained(app_state, name):
            return None
        return await _safe_resolve_health_summary(app_state, name)

    async with asyncio.TaskGroup() as tg:
        tasks = {name: tg.create_task(_recheck(name)) for name in providers}
    results = {
        name: summary
        for name, task in tasks.items()
        if (summary := task.result()) is not None
    }
    await _reconcile_dependents(app_state, trigger="provider_recheck_all")
    return results


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
