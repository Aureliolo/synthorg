"""Calling a provider on demand, and what that answer sets in motion.

Separate from the read side in ``_provider_helpers``: reading replays what was
recorded and costs nothing, while this issues a real billed completion, retires
the evidence that preceded it, and can trigger a reconciler pass. The two share
only the summary read they both finish with.
"""

import asyncio
from typing import NamedTuple

from synthorg._core.features import require_service
from synthorg.api.controllers._provider_helpers import (
    require_provider,
    resolve_health_summary,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.runtime import reconciler_of
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_PROVIDER_HEALTH_RECHECK_REFUSED,
    API_PROVIDER_HEALTH_RECHECKED,
    API_PROVIDER_RECHECK_RECONCILE_FAILED,
)
from synthorg.observability.events.provider import PROVIDER_HEALTH_PROBE_FAILED
from synthorg.providers.errors import ProviderTimeoutError, ProviderValidationError
from synthorg.providers.health import ProviderHealthSummary
from synthorg.providers.management.dtos import TestConnectionRequest
from synthorg.providers.state import ProvidersStateSlice, provider_management_of
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class _RecheckOutcome(NamedTuple):
    """What rechecking one provider produced.

    Attributes:
        served: Whether the fresh call found the provider serving. Kept apart
            from *summary* because the two answer different questions: only a
            provider that answered can have unblocked anything, so this is what
            decides whether the sweep re-attempts dependent subsystems, while a
            provider that answered badly still has a fresh verdict to report.
        summary: The health that call produced, or ``None`` when the call, or
            the read that follows it, failed.
    """

    served: bool
    summary: ProviderHealthSummary | None


async def _supersede_then_call(app_state: AppState, name: str) -> bool:
    """Retire *name*'s stale liveness evidence, then call it.

    The order is the point. Marking the cutoff first retires every outcome the
    operator has just made obsolete, so the verdict is decided by what happens
    from now on. Doing it afterwards would leave the failures they fixed still
    voting, which is the state where pressing Recheck changes nothing a person
    can see.

    The cutoff is a point in time, not a claim on this one call: ordinary
    traffic and the periodic prober keep recording against the same provider,
    and outcomes landing after the cutoff count too. That is deliberate. A
    verdict that ignored every call but this one would report green while real
    requests were failing, which is the same lie in the other direction.

    Returns:
        Whether the call found the provider serving.

    Raises:
        ProviderTimeoutError: If the call outran the budget.
        Exception: Whatever else the call raised; see :func:`_call_provider`.
    """
    health_tracker = require_service(
        app_state.slice(ProvidersStateSlice).health_tracker,
        "Provider Health Tracker",
    )
    await health_tracker.supersede_liveness(name, at=app_state.clock.now())
    return await _call_provider(app_state, name)


async def _call_provider(app_state: AppState, name: str) -> bool:
    """Call one provider so its recorded health reflects the present.

    Bounded by ``api.health_recheck_timeout_seconds``: the call is awaited on
    the request, and it is a completion rather than a ping, so an unreachable
    provider would otherwise hold the response open for whatever the driver's
    own connect timeout happens to be. Timing out costs only this reading,
    which the provider's recorded health still answers.

    Returns:
        Whether the provider answered as serving. A call that completes and
        reports a fault is an ordinary outcome, recorded and reported like any
        other; it is distinguished here only because nothing blocked on this
        provider can have recovered from it.

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
            response = await provider_management_of(app_state).test_connection(
                name, TestConnectionRequest()
            )
    except TimeoutError as exc:
        msg = (
            f"Provider {name!r} did not answer within {budget}s "
            f"(api.health_recheck_timeout_seconds)"
        )
        raise ProviderTimeoutError(msg, context={"provider": name}) from exc
    return response.success


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


async def _call_provider_contained(app_state: AppState, name: str) -> bool | None:
    """Call one provider, keeping its failure to itself.

    The sweep's containment, and only the sweep's: a run across every provider
    must not lose the verdicts it already gathered because one of them raised,
    which a bare ``TaskGroup`` member would cause by cancelling its siblings.
    The single-provider recheck deliberately does not use this, because there
    it would turn a failed call into a stale summary presented as a fresh one.

    Returns:
        Whether the provider answered as serving, or ``None`` when the call
        never completed. Three outcomes rather than two, because the sweep
        does different things with them: a provider that answered has a fresh
        summary worth reporting whichever way it answered, while one whose
        call raised has only its previously recorded summary, and this
        endpoint's whole promise is that what it returns came from a call it
        just made.

    Raises:
        asyncio.CancelledError: Propagated so shutdown is not swallowed.
    """
    try:
        return await _supersede_then_call(app_state, name)
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
        return None


async def _reconcile_dependents(app_state: AppState, *, trigger: str) -> None:
    """Re-attempt whatever was blocked on the provider that just recovered.

    A recheck is an operator saying they fixed something upstream, and the
    subsystems that gave up on it are the reason they care. Memory is the
    worked example: an unreachable embedding model leaves ``memory_backend``
    blocked, and without this the operator's fix waits for the next periodic
    sweep while every agent keeps running with no recall.

    Callers run this only when the fresh call found the provider serving. A
    recheck that confirms the provider is still down has changed nothing a
    dependent could activate on, so a pass there would re-probe every declined
    subsystem, hold the reconciler's lock for its network work, and end where
    it began. The periodic sweep still covers the case where something else
    recovered in the meantime.

    ``retry_declined`` is set for the same reason: those subsystems declined
    on a condition their declaration cannot model, so a pass that skips
    already-declined activations would skip precisely the ones this is for.

    Contained and bounded: the recheck's own answer is already correct and
    returning it matters more than the follow-on pass, so a reconciler fault is
    logged rather than turned into a failed recheck. The budget matters for the
    same reason and is not merely about this response. A pass holds the
    reconciler's lock while it runs, and an activation may do network work of
    its own, so an unbounded await here would let one hung subsystem stall
    every other trigger on the loop behind an operator's diagnostic click.

    Raises:
        asyncio.CancelledError: Propagated so shutdown is not swallowed.
    """
    budget = await config_resolver_of(app_state).get_float(
        "api", "recheck_reconcile_timeout_seconds"
    )
    try:
        async with asyncio.timeout(budget):
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
        return await resolve_health_summary(app_state, name)
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
    reported like any other, and only decides whether the subsystems that were
    blocked on this provider are re-attempted.

    Returns:
        The health summary the fresh call produced.

    Raises:
        NotFoundError: If the provider is not found.
        ProviderTimeoutError: If the call outran
            ``api.health_recheck_timeout_seconds``, which answers 504 and
            retryable rather than 500.
    """
    await require_provider(app_state, name)
    served = await _supersede_then_call(app_state, name)
    summary = await resolve_health_summary(app_state, name)
    logger.info(
        API_PROVIDER_HEALTH_RECHECKED,
        provider=name,
        health_status=summary.health_status.value,
        served=served,
    )
    if served:
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

    Dependent subsystems are re-attempted once, and only when at least one
    provider answered as serving: a sweep across a fleet that is all still
    down has recovered nothing for them to activate on.

    Returns:
        Mapping of provider name to the health its fresh call produced. A
        provider is omitted when its call failed or its summary could not be
        read, because this endpoint's answer is "what calling it just found";
        returning the summary already on file for a provider that was never
        successfully called would present a stale verdict as a fresh one.

    Raises:
        ProviderValidationError: If the configured provider count exceeds
            ``api.health_recheck_max_providers``.
    """
    providers = await config_resolver_of(app_state).get_provider_configs()
    await _require_affordable_fan_out(app_state, provider_count=len(providers))

    async def _recheck(name: str) -> _RecheckOutcome:
        served = await _call_provider_contained(app_state, name)
        if served is None:
            return _RecheckOutcome(served=False, summary=None)
        return _RecheckOutcome(
            served=served,
            summary=await _safe_resolve_health_summary(app_state, name),
        )

    async with asyncio.TaskGroup() as tg:
        tasks = {name: tg.create_task(_recheck(name)) for name in providers}
    outcomes = {name: task.result() for name, task in tasks.items()}
    if any(outcome.served for outcome in outcomes.values()):
        await _reconcile_dependents(app_state, trigger="provider_recheck_all")
    return {
        name: summary
        for name, outcome in outcomes.items()
        if (summary := outcome.summary) is not None
    }
