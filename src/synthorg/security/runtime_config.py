"""Mutable runtime holder for the live :class:`SecurityConfig`.

``app_state.config.security`` is a frozen ``RootConfig`` attribute with no swap
path, and the per-request security interceptor (``SecOpsService``) is built
fresh from it on each agent run. To make the operator-tunable security toggles
(``security.enabled`` / ``audit_enabled`` / ``post_tool_scanning_enabled`` /
``output_scan_policy_type``) hot-reloadable, the engine reads the live config
through this holder instead of the boot-frozen attribute, and
``SecurityBridgeSettingsSubscriber`` swaps a rebuilt config in on an operator change.

Seeded at startup with the boot ``SecurityConfig`` so the holder always returns
a valid value (the engine never sees a transient ``None`` from a missing swap).
"""

import threading

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED
from synthorg.security.config import SecurityConfig

logger = get_logger(__name__)


class MutableSecurityConfig:
    """Thread-safe holder for the live ``SecurityConfig``.

    Mirrors the ``WsAuthLimits`` owner pattern: a single mutable primitive a
    frozen feature slice cannot hold. The per-request interceptor factory
    reads :attr:`current`; ``SecurityBridgeSettingsSubscriber`` replaces it via
    :meth:`swap` under the lock. ``current`` may be ``None`` only when the
    process booted with no ``SecurityConfig`` at all (security feature absent).
    """

    __slots__ = ("_config", "_lock")

    def __init__(self, config: SecurityConfig | None) -> None:
        """Seed the holder with the boot-time security config (or ``None``)."""
        self._config: SecurityConfig | None = config
        self._lock = threading.Lock()

    @property
    def current(self) -> SecurityConfig | None:
        """Return the live ``SecurityConfig`` snapshot (or ``None``)."""
        with self._lock:
            return self._config

    def swap(self, config: SecurityConfig) -> None:
        """Replace the live config wholesale under the lock.

        Logs which of the operator-tunable toggles changed so a runtime
        security posture change is auditable.
        """
        with self._lock:
            previous = self._config
            self._config = config
        changed = _changed_security_fields(previous, config)
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="security_runtime_config",
            transition="swap",
            changed_fields=changed,
        )


def _changed_security_fields(
    previous: SecurityConfig | None,
    new: SecurityConfig,
) -> list[str]:
    """Return the operator-tunable toggle names whose value changed."""
    if previous is None:
        return ["<seeded>"]
    fields = (
        "enabled",
        "audit_enabled",
        "post_tool_scanning_enabled",
        "output_scan_policy_type",
    )
    return sorted(
        name
        for name in fields
        if getattr(previous, name, None) != getattr(new, name, None)
    )
