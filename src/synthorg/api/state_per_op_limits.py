"""Per-op rate-limit / concurrency config primitives.

Owns the cross-cutting mutable enforcement configs a frozen feature
slice cannot hold: the per-op sliding-window rate-limit config and the
per-op in-flight concurrency config, hot-swapped by the settings
subscribers. Composed onto ``AppState`` as ``app_state.per_op_limits``.
"""

from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_SERVICE_UNAVAILABLE
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED

logger = get_logger(__name__)


class PerOpLimitsState:
    """Per-op rate-limit + concurrency enforcement configs.

    Both configs are ``None`` until the startup snapshot lands; the
    request-time getters surface 503 before then. The settings
    subscribers hot-swap the references without rebuilding the
    underlying stores.
    """

    __slots__ = ("_concurrency_config", "_rate_limit_config")

    def __init__(self) -> None:
        """Build with both per-op configs unset (request-time 503 until set)."""
        self._rate_limit_config: PerOpRateLimitConfig | None = None
        self._concurrency_config: PerOpConcurrencyConfig | None = None

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
        """Check whether the per-op sliding-window config is set.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._rate_limit_config is not None

    @property
    def rate_limit_config(self) -> PerOpRateLimitConfig:
        """Return the current per-op sliding-window config or raise 503.

        Returns:
            ``PerOpRateLimitConfig`` instance.
        """
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
    def has_concurrency_config(self) -> bool:
        """Check whether the per-op inflight config is set.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._concurrency_config is not None

    @property
    def concurrency_config(self) -> PerOpConcurrencyConfig:
        """Return the current per-op inflight config or raise 503.

        Returns:
            ``PerOpConcurrencyConfig`` instance.
        """
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
