"""Background health prober for LLM providers.

Periodically pings provider endpoints with lightweight HTTP GET
requests (model list or root URL -- does not trigger inference or
model loading into memory) to detect reachability.

Reachability is one source of health, not the only one. Real completion
outcomes reach :class:`ProviderHealthTracker` from the drivers themselves
(``BaseCompletionProvider`` reports every call), and a connection test or an
on-demand recheck files its verdict there too. This prober covers the gap
those leave: a provider nothing has called yet still needs a verdict, and a
provider that went unreachable between calls should not read healthy until
something happens to use it.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Final

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_PROBE_FAILED,
    PROVIDER_HEALTH_PROBE_SKIPPED,
    PROVIDER_HEALTH_PROBE_STARTED,
    PROVIDER_HEALTH_PROBE_SUCCESS,
    PROVIDER_HEALTH_PROBER_CYCLE_COMPLETED,
    PROVIDER_HEALTH_PROBER_CYCLE_FAILED,
    PROVIDER_HEALTH_PROBER_PAUSED,
    PROVIDER_HEALTH_PROBER_STARTED,
    PROVIDER_HEALTH_PROBER_STOPPED,
)
from synthorg.providers._probe_request import execute_probe, resolve_probe_api_key
from synthorg.providers.discovery_policy import ProviderDiscoveryPolicy
from synthorg.providers.errors import ProviderLifecycleConflictError
from synthorg.providers.health import CallOutcome, RecordSource
from synthorg.providers.health_prober_helpers import (
    build_auth_headers,
    ping_identity,
    ping_identity_still_current,
    resolve_probe_interval,
    resolve_prober_enabled,
)
from synthorg.providers.health_prober_targets import (
    resolve_probe_target,
    select_probe_targets,
)
from synthorg.providers.health_recording import record_call_outcome
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.network_validator import DnsValidationOk

logger = get_logger(__name__)

#: Cadence used only until the first settings read succeeds, and as the
#: fallback whenever one fails. The operator-facing value is
#: ``providers.health_probe_interval_seconds``, read per cycle.
_DEFAULT_INTERVAL_SECONDS: Final[int] = 300


class ProviderHealthProber:
    """Background service that pings providers to check reachability.

    Only probes providers that have a ``base_url`` configured (local
    and self-hosted providers).  Cloud providers without base_url rely
    on real API call outcomes for health status.

    The prober skips providers that have recent health records in the
    tracker (i.e. recent real API traffic), avoiding redundant probes.

    Args:
        health_tracker: Health tracker to record probe results.
        config_resolver: Config resolver to read provider configs.
        discovery_policy_loader: Async callable returning the current
            discovery policy.  When provided, the prober validates
            probe URLs against the SSRF allowlist before sending
            requests (including auth headers).
        interval_seconds: Seconds between probe cycles (must be >= 1).

    Raises:
        ValueError: If *interval_seconds* is less than 1.
    """

    __slots__ = (
        "_clock",
        "_config_resolver",
        "_connection_catalog",
        "_discovery_policy_loader",
        "_health_tracker",
        "_interval",
        "_interval_failed_logged",
        "_lifecycle_lock",
        "_resolve_failed_logged",
        "_stop_drain_timeout_seconds",
        "_stop_event",
        "_stop_failed",
        "_task",
    )

    def __init__(
        self,
        health_tracker: ProviderHealthTracker,
        config_resolver: ConfigResolver,
        *,
        discovery_policy_loader: (
            Callable[[], Awaitable[ProviderDiscoveryPolicy]] | None
        ) = None,
        connection_catalog: ConnectionCatalog | None = None,
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        if interval_seconds < 1:
            msg = f"interval_seconds must be >= 1, got {interval_seconds}"
            raise ValueError(msg)
        self._health_tracker = health_tracker
        self._config_resolver = config_resolver
        self._discovery_policy_loader = discovery_policy_loader
        # Catalog-only credentials: probes resolve the provider's api_key from
        # its connection_name connection. None disables auth headers on the
        # probe (the provider may still answer an unauthenticated ping).
        self._connection_catalog = connection_catalog
        self._interval = interval_seconds
        # Time seam: tests inject ``FakeClock`` to drive the drain
        # deadline and probe-cycle cadence on virtual time, so the
        # suite never depends on wall-clock waits.
        self._clock: Clock = clock if clock is not None else SystemClock()
        # Lifecycle primitives are constructed eagerly so a racing
        # ``stop()`` cannot observe a half-published lock / event /
        # task. The unrestartable flag survives a timed-out stop so
        # a subsequent ``start()`` cannot attach a fresh task while
        # the orphaned ``_run_loop`` still holds resources.
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init -- see above.
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS
        # Resolver-failure warnings are log-once per failure run so a
        # prolonged settings outage cannot flood logs with one warning
        # per cycle. The flag clears on the first successful resolution
        # so a re-failure surfaces a fresh warning.
        self._resolve_failed_logged: bool = False
        self._interval_failed_logged: bool = False

    async def start(self) -> None:
        """Start the background probe loop.

        Idempotent + concurrent-safe: concurrent ``start()`` calls
        serialise on ``self._lifecycle_lock`` so at most one task is
        spawned even when multiple callers race on the ``_task is
        None`` check. After a timed-out stop the prober is marked
        unrestartable; constructing a fresh instance is required.

        Raises:
            ProviderLifecycleConflictError: If the prober was previously
                stopped with a timeout and is marked unrestartable.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "ProviderHealthProber is unrestartable after a "
                    "timed-out stop; construct a fresh prober instead"
                )
                logger.warning(
                    PROVIDER_HEALTH_PROBER_CYCLE_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise ProviderLifecycleConflictError(msg)
            if self._task is not None and not self._task.done():
                return
            self._stop_event.clear()
            # ``_resolve_failed_logged`` survives a graceful stop/start
            # otherwise, which would silence the resolver-failure
            # warning on a re-started service that hits the same
            # outage. Reset before each fresh run so the
            # log-once-per-failure-run contract holds across lifecycle
            # transitions.
            self._resolve_failed_logged = False
            self._interval_failed_logged = False
            self._task = asyncio.create_task(
                self._run_loop(),
                name="provider-health-prober",
            )
            logger.info(
                PROVIDER_HEALTH_PROBER_STARTED,
                interval_seconds=self._interval,
            )

    async def stop(self) -> None:
        """Stop the background probe loop gracefully.

        Holds ``self._lifecycle_lock`` so a concurrent ``start()``
        cannot recreate the task mid-stop. The drain is shielded from
        the outer ``wait_for`` so a hung downstream cannot indefinitely
        hold the lifecycle lock; on timeout the prober is marked
        unrestartable.

        Raises:
            TimeoutError: If the drain does not complete within
                ``_stop_drain_timeout_seconds``; the prober is marked
                unrestartable.
        """
        async with self._lifecycle_lock:
            self._stop_event.set()
            task = self._task
            if task is None:
                return
            task.cancel()

            async def _drain() -> None:
                """Await the cancelled probe task, swallowing its cancellation."""
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        PROVIDER_HEALTH_PROBER_CYCLE_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown",
                    )

            drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=self._stop_drain_timeout_seconds,
                )
            except TimeoutError:
                self._stop_failed = True
                logger.error(
                    PROVIDER_HEALTH_PROBER_CYCLE_FAILED,
                    error=("stop exceeded hard deadline; prober marked unrestartable"),
                    timeout_seconds=self._stop_drain_timeout_seconds,
                )
                raise
            self._task = None
            # Recreate the loop-bound stop event WHILE holding the
            # lifecycle lock. Doing it outside the lock leaves a
            # window where a racing ``start()`` could acquire the
            # lock, spawn a probe task that captures
            # ``self._stop_event`` (still the OLD event), and then
            # this stop()'s ``self._stop_event = asyncio.Event()``
            # outside the lock would swap in a NEW event. A later
            # stop() would signal the new event, but the running
            # task is still waiting on the old one, so shutdown
            # stalls until the interval timeout. Holding the lock
            # across the swap eliminates that race.
            # ``self._lifecycle_lock`` itself MUST stay the same
            # instance for the service lifetime; only the event is
            # swapped.
            self._stop_event = asyncio.Event()
            logger.info(PROVIDER_HEALTH_PROBER_STOPPED)

    async def _resolve_enabled(self) -> bool:
        """Resolve the kill-switch, fail-safe to ``True``.

        Returns:
            ``True`` when the setting is ``True`` or the resolver fails
            (fail-safe to enabled); ``False`` only when explicitly set
            to ``False``.

        Raises:
            asyncio.CancelledError: Propagated from the resolver when the
                task is cancelled.
        """
        enabled, failed = await resolve_prober_enabled(
            self._config_resolver,
            already_reported=self._resolve_failed_logged,
        )
        self._resolve_failed_logged = failed
        return enabled

    async def _run_loop(self) -> None:
        """Main loop: probe all, then sleep until next cycle or stop.

        Gated by ``api.health_prober_enabled`` (live, per-cycle): when
        the setting is ``False`` every cycle short-circuits -- the loop
        keeps running so operators can re-enable without restarting,
        but no probe traffic is sent.

        The cycle wait routes through the injected ``Clock`` seam so
        ``FakeClock.sleep`` can drive the cadence on virtual time in
        tests (matches the constructor's ``clock=`` parameter
        contract). ``asyncio.wait_for(stop_event.wait(),
        timeout=self._interval)`` would bypass the seam by relying on
        the event-loop's wall-clock timer instead.

        Raises:
            asyncio.CancelledError: Propagated from ``_probe_all`` or the
                sleep/stop wait when the task is cancelled.
        """
        while not self._stop_event.is_set():
            interval = await self._resolve_interval()
            if await self._resolve_enabled():
                try:
                    await self._probe_all(interval=interval)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        PROVIDER_HEALTH_PROBER_CYCLE_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            else:
                logger.debug(PROVIDER_HEALTH_PROBER_PAUSED, reason="paused_by_setting")
            sleep_task: asyncio.Task[None] = asyncio.create_task(
                self._clock.sleep(interval),
            )
            stop_task: asyncio.Task[bool] = asyncio.create_task(
                self._stop_event.wait(),
            )
            try:
                done, _pending = await asyncio.wait(
                    {sleep_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (sleep_task, stop_task):
                    if not task.done():
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
            if stop_task in done:
                break  # stop_event was set
            # ``sleep_task`` completed -- next cycle

    async def _load_policy(self) -> ProviderDiscoveryPolicy | None:
        """Load the current discovery policy, or ``None`` when ungated.

        Returns:
            The policy from the injected loader, or ``None`` when no loader
            was wired (the prober then applies no SSRF gate).
        """
        if self._discovery_policy_loader is None:
            return None
        return await self._discovery_policy_loader()

    async def _resolve_interval(self) -> int:
        """Resolve the probe cadence, fail-safe to the constructed value.

        Returns:
            Seconds between probe cycles.

        Raises:
            asyncio.CancelledError: Propagated from the resolver when the
                task is cancelled.
        """
        interval, failed = await resolve_probe_interval(
            self._config_resolver,
            fallback=self._interval,
            already_reported=self._interval_failed_logged,
        )
        self._interval_failed_logged = failed
        return interval

    async def probe_provider(self, name: str) -> None:
        """Probe one provider immediately, outside the cycle cadence.

        Called when a provider is created or its endpoint changes so its
        health is real straight away rather than UNKNOWN until the next
        cycle (up to ``interval_seconds`` later). The recency guard is
        deliberately not applied: an endpoint that just changed must be
        re-probed even if the old one answered moments ago.

        A paused prober, an unknown provider, or a gate rejection is logged
        and skipped; only a completed probe records a result, and an
        unexpected probe error is logged rather than raised. Resolver,
        config-read and DNS errors do propagate, so a caller that must not
        fail on one contains it itself; see
        ``ProviderManagementService._probe_after_mutation``.
        """
        if not await self._resolve_enabled():
            logger.debug(
                PROVIDER_HEALTH_PROBER_PAUSED,
                reason="paused_by_setting",
                provider=name,
            )
            return
        providers = await self._config_resolver.get_provider_configs()
        config = providers.get(name)
        if config is None:
            logger.debug(
                PROVIDER_HEALTH_PROBE_SKIPPED,
                provider=name,
                reason="provider_not_configured",
            )
            return
        ollama_port = await self._config_resolver.get_int(
            "providers", "ollama_default_port"
        )
        target = await resolve_probe_target(
            name, config, await self._load_policy(), ollama_port=ollama_port
        )
        if not target.eligible:
            return
        await self._safe_probe_one(
            name, config, ollama_port=ollama_port, validation=target.validation
        )

    async def _probe_all(self, *, interval: int) -> None:
        """Probe all eligible providers in parallel.

        Args:
            interval: The cadence this cycle is running at, resolved once by
                the caller. Passed rather than re-read so the recency gate and
                the sleep that follows it cannot disagree about how long a
                cycle is when an operator changes the setting mid-cycle.
        """
        providers = await self._config_resolver.get_provider_configs()
        policy = await self._load_policy()
        ollama_port = await self._config_resolver.get_int(
            "providers", "ollama_default_port"
        )
        eligible = await select_probe_targets(
            providers,
            policy,
            health_tracker=self._health_tracker,
            clock=self._clock,
            ollama_port=ollama_port,
            interval=interval,
        )
        if eligible:
            async with asyncio.TaskGroup() as tg:
                for name, config, validation in eligible:
                    _ = tg.create_task(
                        self._safe_probe_one(
                            name,
                            config,
                            ollama_port=ollama_port,
                            validation=validation,
                        )
                    )
        # A cycle that probes nothing is otherwise indistinguishable from one
        # that probed everything successfully, so record both counts: an empty
        # provider map (the state before the first provider is configured)
        # would otherwise iterate zero times and emit no trace at all. INFO,
        # not DEBUG: the deployed default is ``info``, so a DEBUG heartbeat
        # would leave that exact silence in place. One line per cycle at the
        # cadence an operator sets is a heartbeat, not noise.
        #
        # After the group, so the event means what its name says: a group that
        # raises leaves no completion line rather than one that already
        # claimed success. Outside the ``if``, so a zero-provider sweep still
        # reports.
        logger.info(
            PROVIDER_HEALTH_PROBER_CYCLE_COMPLETED,
            provider_count=len(providers),
            eligible_count=len(eligible),
        )

    async def _safe_probe_one(
        self,
        name: str,
        config: ProviderConfig,
        *,
        ollama_port: int,
        validation: DnsValidationOk | None = None,
    ) -> None:
        """Probe a single provider, isolating failures from peers.

        Wraps ``_probe_one`` so that an unexpected error (e.g.
        Pydantic validation failure in record construction) does
        not cancel sibling probes in the ``TaskGroup``.

        Args:
            name: Provider name.
            config: Provider configuration.
            ollama_port: Resolved ``providers.ollama_default_port`` for
                Ollama-detection in :func:`_build_ping_url`.
            validation: DNS pre-flight result for the probe URL, carrying
                the IPs to pin; ``None`` when no discovery policy gates
                the prober.
        """
        try:
            await self._probe_one(
                name, config, ollama_port=ollama_port, validation=validation
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_HEALTH_PROBE_FAILED,
                provider=name,
                note="Unexpected error during probe",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _probe_one(
        self,
        name: str,
        config: ProviderConfig,
        *,
        ollama_port: int,
        validation: DnsValidationOk | None = None,
    ) -> None:
        """Ping a single provider and record the result.

        Args:
            name: Provider name.
            config: Provider configuration.
            ollama_port: Resolved ``providers.ollama_default_port`` for
                Ollama-detection in :func:`_build_ping_url`.
            validation: DNS pre-flight result carrying the IPs to pin the
                probe connection to; ``None`` disables pinning.
        """
        # base_url is guaranteed non-None: _probe_all filters out
        # providers without it before calling _probe_one.
        # base_url is guaranteed non-None here, so the identity is too:
        # _probe_all filters out providers without one before calling in.
        identity = ping_identity(config, ollama_port=ollama_port)
        if identity is None or identity.url is None:
            return
        auth_type = str(config.auth_type)
        api_key = await resolve_probe_api_key(config, self._connection_catalog)
        headers = build_auth_headers(auth_type, api_key)

        logger.debug(PROVIDER_HEALTH_PROBE_STARTED, provider=name)
        result = await execute_probe(
            identity.url, headers, clock=self._clock, validation=validation
        )
        elapsed_ms, success, error_msg = result

        if not await ping_identity_still_current(
            name, identity, config_resolver=self._config_resolver
        ):
            logger.debug(
                PROVIDER_HEALTH_PROBE_SKIPPED, provider=name, reason="config_changed"
            )
            return

        latency = await record_call_outcome(
            self._health_tracker,
            name,
            CallOutcome(
                success=success,
                response_time_ms=elapsed_ms,
                error_message=error_msg,
            ),
            clock=self._clock,
            source=RecordSource.PROBE,
        )
        if success:
            logger.info(
                PROVIDER_HEALTH_PROBE_SUCCESS, provider=name, latency_ms=latency
            )
        else:
            logger.warning(
                PROVIDER_HEALTH_PROBE_FAILED,
                provider=name,
                error=error_msg,
                latency_ms=latency,
            )

    async def record_outcome(self, name: str, outcome: CallOutcome) -> None:
        """Record an observed call outcome against *name*'s health.

        Serves a caller that made its own call rather than asking for a
        probe: a connection test reaches a provider with no ``base_url``,
        which :meth:`probe_provider` skips as ineligible. That caller
        reports the outcome itself, so nothing is logged here.

        Recorded as a real call rather than a probe, because it is one: a
        connection test issues an actual completion against an actual model,
        so it is evidence about whether that pair serves work in a way a
        reachability ping never is. The caller names the model it used.
        """
        _ = await record_call_outcome(
            self._health_tracker,
            name,
            outcome,
            clock=self._clock,
            source=RecordSource.REAL_CALL,
        )
