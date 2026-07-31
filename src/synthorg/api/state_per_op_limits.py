"""Per-op rate-limit / concurrency config primitives.

Owns the cross-cutting mutable enforcement configs a frozen feature
slice cannot hold: the per-op sliding-window rate-limit config and the
per-op in-flight concurrency config, hot-swapped by the settings
subscribers. Composed onto ``AppState`` as ``app_state.per_op_limits``.
"""

from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.config.rate_limits import LiveRateLimits
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_SERVICE_UNAVAILABLE
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED

logger = get_logger(__name__)


class PerOpLimitsState:
    """Rate-limit + concurrency enforcement configs, per-op and global.

    The per-op configs are ``None`` until the startup snapshot lands; the
    request-time getters surface 503 before then. The settings
    subscribers hot-swap the references without rebuilding the
    underlying stores.

    The global tiers live here too rather than in the Litestar app
    config, which is immutable once built: reading them per request is
    the only way an operator can retune a limit while the system is
    under the load that made them want to.
    """

    __slots__ = ("_concurrency_config", "_global_config", "_rate_limit_config")

    def __init__(self) -> None:
        """Build with every config unset (request-time 503 until set)."""
        self._rate_limit_config: PerOpRateLimitConfig | None = None
        self._concurrency_config: PerOpConcurrencyConfig | None = None
        self._global_config: LiveRateLimits | None = None

    def _require[T](self, value: T | None, name: str) -> T:
        """Return *value* or raise 503 if the startup snapshot has not landed.

        Returns:
            The non-``None`` config.

        Raises:
            ServiceUnavailableError: When *value* is ``None``.
        """
        if value is None:
            logger.warning(API_SERVICE_UNAVAILABLE, service=name)
            msg = f"{name.replace('_', ' ').title()} not configured"
            raise ServiceUnavailableError(msg)
        return value

    @property
    def has_rate_limit_config(self) -> bool:
        """Check whether the per-op sliding-window config is set."""
        return self._rate_limit_config is not None

    @property
    def rate_limit_config(self) -> PerOpRateLimitConfig:
        """Return the current per-op sliding-window config or raise 503."""
        return self._require(self._rate_limit_config, "per_op_rate_limit_config")

    def set_rate_limit_config(self, config: PerOpRateLimitConfig) -> None:
        """Attach the per-op sliding-window config at startup (once).

        Guards and middleware read through :attr:`rate_limit_config`
        at request time, so swapping this reference is how the settings
        subscriber applies runtime overrides without restarting the app.
        """
        self._rate_limit_config = config

    def swap_rate_limit_config(self, config: PerOpRateLimitConfig) -> None:
        """Replace the per-op sliding-window config (hot-reload).

        Called by the settings subscriber when operators change
        ``api.per_op_rate_limit_enabled`` or
        ``api.per_op_rate_limit_overrides``.  The store itself is not
        rebuilt -- only the config object swaps, so already-queued
        timestamps remain in place and a ``backend`` flip still needs
        a restart (it is marked ``restart_required=True``).
        """
        old_enabled = (
            self._rate_limit_config.enabled
            if self._rate_limit_config is not None
            else None
        )
        self._rate_limit_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_rate_limit_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )

    @property
    def global_config(self) -> LiveRateLimits | None:
        """Return the live global tier config, or ``None`` before boot sets it.

        Unlike the per-op getters this does not raise: the middleware falls
        back to the values Litestar was built with, so a request arriving
        before the snapshot lands is limited by the boot config rather than
        by nothing at all.
        """
        return self._global_config

    def set_global_config(self, config: LiveRateLimits) -> None:
        """Attach the global tier config at startup."""
        self._global_config = config

    def swap_global_config(self, config: LiveRateLimits) -> None:
        """Replace the global tier config (hot-reload).

        The stores keep their windows: only the caps and the exclusions
        change, so requests already counted stay counted.
        """
        previous = self._global_config
        self._global_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="global_rate_limit_config",
            old_auth_max=previous.auth_max_requests if previous else None,
            new_auth_max=config.auth_max_requests,
        )

    @property
    def has_concurrency_config(self) -> bool:
        """Check whether the per-op inflight config is set."""
        return self._concurrency_config is not None

    @property
    def concurrency_config(self) -> PerOpConcurrencyConfig:
        """Return the current per-op inflight config or raise 503."""
        return self._require(self._concurrency_config, "per_op_concurrency_config")

    def set_concurrency_config(self, config: PerOpConcurrencyConfig) -> None:
        """Attach the per-op inflight config at startup (once).

        Paired swap target for the inflight subscriber path; mirrors
        :meth:`set_rate_limit_config` so the two per-op guards have
        symmetric wiring.
        """
        self._concurrency_config = config

    def swap_concurrency_config(self, config: PerOpConcurrencyConfig) -> None:
        """Replace the per-op inflight config (hot-reload).

        Called by the settings subscriber on
        ``api.per_op_concurrency_enabled`` or
        ``api.per_op_concurrency_overrides`` change.  The inflight
        store keeps its counters -- only the enforcement config
        changes.
        """
        old_enabled = (
            self._concurrency_config.enabled
            if self._concurrency_config is not None
            else None
        )
        self._concurrency_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_concurrency_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )
