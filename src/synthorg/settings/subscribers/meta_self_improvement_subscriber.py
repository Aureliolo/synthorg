"""Meta self-improvement settings subscriber.

Invalidates the cached :class:`~synthorg.meta.config.SelfImprovementConfig`
on the meta slice when an operator edits the structural ``meta.self_improvement``
blob OR any of the hot ``self_improvement.*`` / ``chief_of_staff.*`` overlay
settings. The meta slice caches the parsed config so the read endpoints do not
re-parse per request; this subscriber wires the cache field back to ``None`` so
the next read reloads the fresh value, keeping the effective-config view in step
with the live overlay (the running services already read each value live).

``self_improvement.code_modification_enabled`` is deliberately NOT watched.
It is not that the value cannot change while the system runs; it is that
nothing here should make it take effect faster than the load path, which
re-reads the credentials on every parse and forces the flag back off when
they are absent. ``chief_of_staff.direct_mcp_enabled`` IS watched, because
the actor it gates is rebuilt by the subsystem reconciler behind the same
fail-closed governance gate, so the cached config has to move with it.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

# The structural blob plus every hot overlay flag/model, so the cached
# effective config is invalidated whenever a live value changes. The KEEP
# settings are excluded by design (see module docstring).
_SELF_IMPROVEMENT_HOT_KEYS: frozenset[str] = frozenset(
    {
        "enabled",
        "chief_of_staff_enabled",
        "config_tuning_enabled",
        "architecture_proposals_enabled",
        "prompt_tuning_enabled",
        "tool_creation_enabled",
        "tool_creation_allowed_capabilities",
        "analysis_model",
        "code_modification_model",
    }
)
_CHIEF_OF_STAFF_HOT_KEYS: frozenset[str] = frozenset(
    {
        "routing_enabled",
        "learning_enabled",
        "alerts_enabled",
        "narrative_enabled",
        "invite_enabled",
        "direct_mcp_enabled",
        "chat_model",
        "propose_model",
        "routing_model",
        "narrative_model",
    }
)
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {("meta", "self_improvement")}
    | {("self_improvement", key) for key in _SELF_IMPROVEMENT_HOT_KEYS}
    | {("chief_of_staff", key) for key in _CHIEF_OF_STAFF_HOT_KEYS}
)


class MetaSelfImprovementSettingsSubscriber:
    """Invalidate the cached ``SelfImprovementConfig`` on a config edit.

    Holds :class:`AppState` (where the cache lives) and
    :class:`SettingsService` (for parity with peer subscribers). On a
    watched-key change it wires ``MetaStateSlice.self_improvement_config``
    back to ``None`` so the next :func:`self_improvement_config_of` read
    reloads the operator's new value.

    Args:
        app_state: Application state that owns the cached config.
        settings_service: Settings service held for symmetry with peers.
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
        return "meta-self-improvement"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Invalidate the cached config so the next read reloads it."""
        from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

        try:
            self._app_state.wire(MetaStateSlice, self_improvement_config=None)
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="invalidated cached self-improvement config",
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="meta_self_improvement",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
