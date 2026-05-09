"""Background health prober for LLM providers.

Periodically pings provider endpoints with lightweight HTTP GET
requests (model list or root URL -- does not trigger inference or
model loading into memory) to detect reachability.  Real API call
outcomes recorded in :class:`ProviderHealthTracker` automatically
reset the probe interval for that provider.
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import urlparse

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.normalization import strip_trailing_slash
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_PROBE_FAILED,
    PROVIDER_HEALTH_PROBE_SKIPPED,
    PROVIDER_HEALTH_PROBE_STARTED,
    PROVIDER_HEALTH_PROBE_SUCCESS,
    PROVIDER_HEALTH_PROBER_CYCLE_FAILED,
    PROVIDER_HEALTH_PROBER_PAUSED,
    PROVIDER_HEALTH_PROBER_RESOLVE_FAILED,
    PROVIDER_HEALTH_PROBER_RESOLVE_RECOVERED,
    PROVIDER_HEALTH_PROBER_STARTED,
    PROVIDER_HEALTH_PROBER_STOPPED,
)
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
    is_url_allowed,
)
from synthorg.providers.errors import ProviderLifecycleConflictError
from synthorg.providers.health import ProviderHealthRecord, ProviderHealthTracker
from synthorg.settings.enums import SettingNamespace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.config.schema import ProviderConfig
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_DEFAULT_INTERVAL_SECONDS: Final[int] = (
    1800  # lint-allow: magic-numbers -- 30 min ctor-kwarg default
)
_PROBE_TIMEOUT_SECONDS: Final[float] = (
    10.0  # lint-allow: magic-numbers -- HTTP client timeout baseline
)
_HTTP_SERVER_ERROR_THRESHOLD: Final[int] = (
    500  # lint-allow: magic-numbers -- HTTP 5xx protocol constant
)
_MAX_ERROR_MESSAGE_LENGTH: Final[int] = (
    200  # lint-allow: magic-numbers -- log truncation limit
)


def _build_ping_url(
    base_url: str,
    litellm_provider: str | None,
    *,
    ollama_port: int,
) -> str:
    """Build a lightweight ping URL for a provider.

    Uses the cheapest possible endpoint -- no model loading.
    Providers whose ``litellm_provider`` is ``"ollama"`` (or whose
    URL is bound to ``ollama_port``) use the root URL; all others
    append ``/models``.

    Args:
        base_url: Provider base URL.
        litellm_provider: LiteLLM provider identifier for path selection.
        ollama_port: Port used to detect a self-hosted Ollama provider
            when ``litellm_provider`` is not set explicitly. Required
            (no default) so the canonical value flows through from the
            registered ``providers.ollama_default_port`` setting at
            every call site instead of mirroring it locally. Must be a
            valid TCP port (1-65535); the registry entry validates the
            bounds at write time, so a value out of range cannot reach
            this function via the resolver path.

    Returns:
        URL to ping.

    Raises:
        ValueError: ``ollama_port`` is outside the valid TCP-port range.
    """
    if not 1 <= ollama_port <= 65535:  # noqa: PLR2004 -- TCP port range
        msg = f"ollama_port must be in 1-65535, got {ollama_port!r}"
        raise ValueError(msg)
    stripped = strip_trailing_slash(base_url)
    is_ollama = litellm_provider == "ollama" or urlparse(stripped).port == ollama_port
    if is_ollama:
        return stripped  # Root URL returns a liveness string
    return f"{stripped}/models"


def _build_auth_headers(
    auth_type: str,
    api_key: str | None,
) -> dict[str, str]:
    """Build auth headers for the probe request.

    Only ``api_key`` and ``subscription`` auth types produce an
    ``Authorization: Bearer`` header.  Other types (oauth,
    custom_header, none) result in no probe auth headers.

    Args:
        auth_type: Provider auth type.
        api_key: API key (may be None for local providers).

    Returns:
        Headers dict (may be empty).
    """
    if api_key and auth_type in ("api_key", "subscription"):
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def _truncate(msg: str, limit: int = _MAX_ERROR_MESSAGE_LENGTH) -> str:
    """Truncate a string to *limit* characters."""
    if len(msg) <= limit:
        return msg
    return msg[: limit - 3] + "..."


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
        "_discovery_policy_loader",
        "_health_tracker",
        "_interval",
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
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        if interval_seconds < 1:
            msg = f"interval_seconds must be >= 1, got {interval_seconds}"
            raise ValueError(msg)
        self._health_tracker = health_tracker
        self._config_resolver = config_resolver
        self._discovery_policy_loader = discovery_policy_loader
        self._interval = interval_seconds
        # ``Clock`` seam per ``CLAUDE.md`` -- tests inject ``FakeClock``
        # so the lifecycle drain timeout and probe-cycle interval can
        # be driven on virtual time instead of wall time.
        self._clock: Clock = clock if clock is not None else SystemClock()
        # Per ``docs/reference/lifecycle-sync.md`` the lifecycle lock,
        # stop event, drain timeout, and unrestartable flag are
        # constructed eagerly so a racing ``stop()`` cannot observe a
        # half-published lock attribute.
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init -- see above.
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = 30.0
        # Resolver-failure warnings are log-once per failure run so a
        # prolonged settings outage cannot flood logs with one warning
        # per cycle. The flag clears on the first successful resolution
        # so a re-failure surfaces a fresh warning.
        self._resolve_failed_logged: bool = False

    async def start(self) -> None:
        """Start the background probe loop.

        Idempotent + concurrent-safe: concurrent ``start()`` calls
        serialise on ``self._lifecycle_lock`` so at most one task is
        spawned even when multiple callers race on the ``_task is
        None`` check. After a timed-out stop the prober is marked
        unrestartable; constructing a fresh instance is required.
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
        """
        async with self._lifecycle_lock:
            self._stop_event.set()
            task = self._task
            if task is None:
                return
            task.cancel()

            async def _drain() -> None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
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

        Operators flip ``api.health_prober_enabled=false`` to pause
        provider HTTP probing mid-flight without tearing down the loop.
        A settings-backend outage must not silently pause observability,
        so any resolver failure resolves to enabled.

        Resolver-failure warnings are throttled via
        ``_resolve_failed_logged`` -- a prolonged outage emits a single
        ``PROVIDER_HEALTH_PROBER_RESOLVE_FAILED`` warning, and the next
        successful read clears the flag and emits one
        ``PROVIDER_HEALTH_PROBER_RESOLVE_RECOVERED`` info before
        resuming silent operation. Without this guard a short probe
        interval against a degraded settings backend would tile dashboards
        with cycle-failed events that are actually clean fallback
        cycles.
        """
        try:
            value = await self._config_resolver.get_bool(
                SettingNamespace.API.value, "health_prober_enabled"
            )
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            if not self._resolve_failed_logged:
                logger.warning(
                    PROVIDER_HEALTH_PROBER_RESOLVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    fallback_enabled=True,
                )
                self._resolve_failed_logged = True
            return True
        if self._resolve_failed_logged:
            logger.info(PROVIDER_HEALTH_PROBER_RESOLVE_RECOVERED)
            self._resolve_failed_logged = False
        return value

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
        """
        while not self._stop_event.is_set():
            if await self._resolve_enabled():
                try:
                    await self._probe_all()
                except asyncio.CancelledError:
                    raise
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.warning(
                        PROVIDER_HEALTH_PROBER_CYCLE_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            else:
                logger.debug(PROVIDER_HEALTH_PROBER_PAUSED, reason="paused_by_setting")
            sleep_task: asyncio.Task[None] = asyncio.create_task(
                self._clock.sleep(self._interval),
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

    async def _probe_all(self) -> None:
        """Probe all eligible providers in parallel."""
        providers = await self._config_resolver.get_provider_configs()
        policy: ProviderDiscoveryPolicy | None = None
        if self._discovery_policy_loader is not None:
            policy = await self._discovery_policy_loader()
        ollama_port = await self._config_resolver.get_int(
            "providers", "ollama_default_port"
        )
        eligible: list[tuple[str, ProviderConfig]] = []
        for name, config in providers.items():
            if config.base_url is None:
                continue  # cloud providers -- no lightweight ping available
            url = _build_ping_url(
                config.base_url, config.litellm_provider, ollama_port=ollama_port
            )
            if policy is not None and not is_url_allowed(url, policy):
                # Skip -- SSRF-blocked providers are in an indeterminate
                # state, not a failed one.  UNKNOWN (zero records) is the
                # correct health status for them.
                logger.warning(
                    PROVIDER_HEALTH_PROBE_SKIPPED,
                    provider=name,
                    reason="url_not_allowed_by_discovery_policy",
                )
                continue
            summary = await self._health_tracker.get_summary(name)
            if summary.last_check_timestamp is not None:
                elapsed = (
                    datetime.now(UTC) - summary.last_check_timestamp
                ).total_seconds()
                if elapsed < self._interval:
                    logger.debug(
                        PROVIDER_HEALTH_PROBE_SKIPPED,
                        provider=name,
                        seconds_since_last=round(elapsed),
                    )
                    continue
            eligible.append((name, config))
        if eligible:
            async with asyncio.TaskGroup() as tg:
                for name, config in eligible:
                    tg.create_task(
                        self._safe_probe_one(name, config, ollama_port=ollama_port)
                    )

    async def _safe_probe_one(
        self,
        name: str,
        config: ProviderConfig,
        *,
        ollama_port: int,
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
        """
        try:
            await self._probe_one(name, config, ollama_port=ollama_port)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
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
    ) -> None:
        """Ping a single provider and record the result.

        Args:
            name: Provider name.
            config: Provider configuration.
            ollama_port: Resolved ``providers.ollama_default_port`` for
                Ollama-detection in :func:`_build_ping_url`.
        """
        # base_url is guaranteed non-None: _probe_all filters out
        # providers without it before calling _probe_one.
        url = _build_ping_url(
            config.base_url,  # type: ignore[arg-type]
            config.litellm_provider,
            ollama_port=ollama_port,
        )
        auth_type = str(config.auth_type)
        headers = _build_auth_headers(auth_type, config.api_key)

        logger.debug(PROVIDER_HEALTH_PROBE_STARTED, provider=name)
        result = await self._execute_probe(url, headers)
        elapsed_ms, success, error_msg = result

        record = ProviderHealthRecord(
            provider_name=name,
            timestamp=datetime.now(UTC),
            success=success,
            response_time_ms=round(elapsed_ms, 1),
            error_message=error_msg,
        )
        await self._health_tracker.record(record)

        if success:
            logger.info(
                PROVIDER_HEALTH_PROBE_SUCCESS,
                provider=name,
                latency_ms=round(elapsed_ms, 1),
            )
        else:
            logger.warning(
                PROVIDER_HEALTH_PROBE_FAILED,
                provider=name,
                error=error_msg,
                latency_ms=round(elapsed_ms, 1),
            )

    async def _execute_probe(
        self,
        url: str,
        headers: dict[str, str],
    ) -> tuple[float, bool, str | None]:
        """Execute the HTTP probe request.

        Args:
            url: URL to probe.
            headers: Auth headers for the request.

        Returns:
            Tuple of (elapsed_ms, success, error_message).
        """
        start = self._clock.monotonic()
        success = False
        error_msg: str | None = None

        try:
            async with httpx.AsyncClient(
                timeout=_PROBE_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                resp = await client.get(url, headers=headers)
                success = resp.status_code < _HTTP_SERVER_ERROR_THRESHOLD
                if not success:
                    error_msg = f"HTTP {resp.status_code}"
        except httpx.ConnectError as exc:
            error_msg = f"connect failed: {type(exc).__name__}"
        except httpx.TimeoutException:
            error_msg = "timeout"
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_msg = _truncate(
                f"{type(exc).__name__}: {safe_error_description(exc)}"
            )

        elapsed_ms = (self._clock.monotonic() - start) * 1000
        return elapsed_ms, success, error_msg
