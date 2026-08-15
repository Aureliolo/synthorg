"""Capability-policy settings subscriber.

The ladder every assignment walks (what rung this work demands, how hard the
model is asked to think, when a deliverable needs a red team, when a weaker
agent is refused rather than logged) is resolved once when the runtime is
assembled. Without this, an operator correcting a floor would see the write
persist, render in the dashboard, and bind nothing until a restart.

There is exactly one :class:`CapabilityPolicy` per process and every consumer
holds that same instance, so re-pointing it here reaches selection, the
coordination router, both quality gates, the review-staffing sweep and the
dispatch binding in one call.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAP_FAILED
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)

#: Every key ``resolve_capability_policy_config`` reads. Kept in step with
#: that function rather than with the config model's field list: a
#: field the resolver does not read cannot change on a rebuild, so watching
#: its key would schedule work that changes nothing. Spelled out rather than
#: comprehended, because a key built by interpolation is a key the liveness
#: gate cannot see and would report as binding nothing.
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("engine", "capability_floor_low"),
        ("engine", "capability_floor_normal"),
        ("engine", "capability_floor_high"),
        ("engine", "capability_floor_critical"),
        ("engine", "reasoning_effort_low"),
        ("engine", "reasoning_effort_normal"),
        ("engine", "reasoning_effort_high"),
        ("engine", "reasoning_effort_critical"),
        ("engine", "red_team_min_stakes"),
        ("engine", "capability_park_min_stakes"),
    }
)


class CapabilityPolicySettingsSubscriber:
    """Push a re-resolved ladder into the running capability policy.

    Args:
        app_state: Application state owning the engine slice + resolver.
        settings_service: Held for symmetry with peer subscribers.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "capability-policy"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-resolve the ladder and adopt it on the live policy.

        One re-resolve per batch: the resolver assembles every field in a
        single pass, so a per-key rebuild would repeat identical work. A boot
        that built no policy (no providers configured, so nothing to grade)
        has nothing to adopt onto and the write applies when one is built.

        Args:
            changes: The watched writes this rebuild carries.
        """
        from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
        from synthorg.settings._resolver_capability_policy import (  # noqa: PLC0415
            resolve_capability_policy_config,
        )
        from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

        policy = self._app_state.slice(EngineStateSlice).capability_policy
        if policy is None:
            return
        try:
            resolved = await resolve_capability_policy_config(
                config_resolver_of(self._app_state)
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="capability_policy",
                trigger=describe_changes(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        policy.set_config(resolved)
